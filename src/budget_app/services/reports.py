"""Monthly income/expenditure statements and time series for charts.

Display conversion rules:
* transactions are converted with the ECB rate of their recorded `fx_date`,
* budget entries are converted with the latest available ECB rate,
* account balances are valued with the last ECB rate of the month (see wealth.py).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain.coicop import ADV, TAX, UNACCOUNTED, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, TX_EXPENDITURE, TX_INCOME, Transaction
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context

ADV_REPAY = "ADV_REPAY"
ADV_REPAY_NAME = "Advance repayments"


def tx_value(ctx: Context, tx: Transaction, view_cur: str) -> float:
    """Transaction amount in the view currency, using the rate recorded with the transaction."""
    if tx.currency == view_cur:
        return tx.amount
    return ctx.convert_on(tx.amount_chf, BASE_CURRENCY, view_cur, tx.fx_date)


def expenditure_parts(ctx: Context, tx: Transaction, view_cur: str) -> list[tuple[str, float]]:
    """(category, amount) parts of an expenditure: own share to its category, advance share to ADV."""
    total = tx_value(ctx, tx, view_cur)
    if tx.is_advance and tx.advance_amount > 0 and tx.amount > 0:
        adv = total * tx.advance_amount / tx.amount
        parts = [(ADV, adv)]
        if total - adv > 0.0049:
            parts.insert(0, (tx.category, total - adv))
        return parts
    return [(tx.category, total)]


def income_key(tx: Transaction) -> str:
    return ADV_REPAY if tx.is_advance else tx.category


def _in_months(d: date, month_set: set[str]) -> bool:
    return months.month_of(d) in month_set


# ------------------------------------------------------------------ aggregates
def actual_expenditure(ctx: Context, month_set: set[str], view_cur: str) -> dict[str, float]:
    """Direct (not rolled up) actual expenditure per category code."""
    out: dict[str, float] = defaultdict(float)
    for t in ctx.store.transactions.all():
        if t.type == TX_EXPENDITURE and _in_months(t.date, month_set):
            for code, value in expenditure_parts(ctx, t, view_cur):
                out[code] += value
    return dict(out)


def actual_income(ctx: Context, month_set: set[str], view_cur: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in ctx.store.transactions.all():
        if t.type == TX_INCOME and _in_months(t.date, month_set):
            out[income_key(t)] += tx_value(ctx, t, view_cur)
    return dict(out)


def budgeted(ctx: Context, month_set: set[str], type_: str, view_cur: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for b in ctx.store.budget.all():
        if b.type == type_ and b.month in month_set:
            out[b.category] += ctx.convert_on(b.amount, b.currency, view_cur, None)
    return dict(out)


def rollup(direct: dict[str, float]) -> dict[str, float]:
    """Sum every amount into its own code and all ancestor codes."""
    coicop = get_coicop()
    out: dict[str, float] = defaultdict(float)
    for code, value in direct.items():
        for anc in coicop.ancestors(code):
            out[anc] += value
    return dict(out)


# ------------------------------------------------------------- month statement
@dataclass
class Line:
    key: str
    label: str
    actual: float = 0.0
    budget: float = 0.0
    level: int = 1
    has_children: bool = False
    editable: bool = False  # budget can be edited directly for this key

    @property
    def diff(self) -> float:
        return self.actual - self.budget


@dataclass
class MonthReport:
    month: str
    currency: str
    complete: bool
    expenditure_tree: dict[str, Line] = field(default_factory=dict)  # all codes with a value
    top_expenditure: list[str] = field(default_factory=list)  # keys shown at top level
    income_lines: list[Line] = field(default_factory=list)
    total_expenditure: Line = field(default_factory=lambda: Line("TOTAL", "Total expenditure"))
    total_income: Line = field(default_factory=lambda: Line("TOTAL", "Total income"))
    unaccounted: float | None = None
    wealth_start: float | None = None
    wealth_end: float | None = None
    accounts: list[wealth_svc.AccountRow] = field(default_factory=list)
    projected_wealth: float | None = None

    def children(self, code: str, include_empty: bool = False) -> list[Line]:
        coicop = get_coicop()
        out = []
        for child in coicop.children(code):
            line = self.expenditure_tree.get(child)
            if line is None and include_empty:
                cat = coicop.categories[child]  # children are always known codes
                line = Line(child, cat.label, level=cat.level, has_children=bool(cat.children), editable=True)
            if line is not None:
                out.append(line)
        return out


def month_report(ctx: Context, month: str, view_cur: str, today: date | None = None) -> MonthReport:
    coicop = get_coicop()
    complete = months.is_complete(month, today)
    rep = MonthReport(month=month, currency=view_cur, complete=complete)
    mset = {month}

    act_exp = rollup(actual_expenditure(ctx, mset, view_cur))
    bud_exp = rollup(budgeted(ctx, mset, BUDGET_EXPENDITURE, view_cur))
    for code in set(act_exp) | set(bud_exp):
        cat = coicop.get(code)
        rep.expenditure_tree[code] = Line(
            code,
            cat.label if cat else code,
            act_exp.get(code, 0.0),
            bud_exp.get(code, 0.0),
            level=cat.level if cat else 1,
            has_children=bool(cat and cat.children),
            editable=code != ADV,
        )
    for code in [*coicop.divisions, TAX, ADV]:
        if code not in rep.expenditure_tree:
            cat = coicop.categories[code]  # divisions and pseudo-categories always exist
            rep.expenditure_tree[code] = Line(
                code, cat.label, level=1, has_children=bool(cat.children), editable=code != ADV
            )
    rep.top_expenditure = [*coicop.divisions, TAX, ADV]

    act_inc = actual_income(ctx, mset, view_cur)
    bud_inc = budgeted(ctx, mset, BUDGET_INCOME, view_cur)
    names = {c.id: c.name for c in ctx.store.income_categories.all()} | {ADV_REPAY: ADV_REPAY_NAME}
    for key in sorted(set(act_inc) | set(bud_inc), key=lambda k: (names.get(k) or k).lower()):
        line = Line(key, names.get(key, key), act_inc.get(key, 0.0), bud_inc.get(key, 0.0), editable=key != ADV_REPAY)
        if abs(line.actual) > 0.005 or abs(line.budget) > 0.005:
            rep.income_lines.append(line)

    total_inc_act = sum(act_inc.values())
    recorded_exp = sum(rep.expenditure_tree[c].actual for c in rep.top_expenditure)
    total_exp_budget = sum(rep.expenditure_tree[c].budget for c in rep.top_expenditure)

    if complete and wealth_svc.has_wealth_data(ctx, months.add_months(month, -1)):
        rep.wealth_start = wealth_svc.wealth(ctx, months.add_months(month, -1), view_cur)
        rep.wealth_end = wealth_svc.wealth(ctx, month, view_cur)
        rep.unaccounted = total_inc_act - recorded_exp - (rep.wealth_end - rep.wealth_start)
        rep.expenditure_tree[UNACCOUNTED] = Line(UNACCOUNTED, coicop.label(UNACCOUNTED), rep.unaccounted)
        rep.top_expenditure.append(UNACCOUNTED)

    rep.total_expenditure = Line(
        "TOTAL", "Total expenditure", recorded_exp + (rep.unaccounted or 0.0), total_exp_budget
    )
    rep.total_income = Line("TOTAL", "Total income", total_inc_act, sum(bud_inc.values()))

    if complete:
        rep.accounts = [
            r for r in wealth_svc.account_rows(ctx, month, view_cur) if abs(r.amount) > 0.005 or r.has_entry
        ]
    else:
        rep.projected_wealth = wealth_svc.projected_wealth(ctx, month, view_cur)
    return rep


# ----------------------------------------------------------------- time series
def series_keys(ctx: Context) -> dict[str, str]:
    """Selectable chart series: expenditure divisions, taxes, advances and income categories."""
    coicop = get_coicop()
    out = {"EXP:TOTAL": "Total expenditure", "INC:TOTAL": "Total income"}
    for code in [*coicop.divisions, TAX, ADV]:
        out[f"EXP:{code}"] = coicop.label(code)
    for c in ctx.store.income_categories.all():
        out[f"INC:{c.id}"] = f"Income: {c.name}"
    out[f"INC:{ADV_REPAY}"] = f"Income: {ADV_REPAY_NAME}"
    return out


def monthly_series(
    ctx: Context, start: str, end: str, view_cur: str, keys: list[str], include_budget: bool = False
) -> list[dict]:
    """One row per month with actual (and optionally budgeted) values for the requested keys."""
    labels = series_keys(ctx)
    rows = []
    for m in months.month_range(start, end):
        mset = {m}
        exp = rollup(actual_expenditure(ctx, mset, view_cur))
        inc = actual_income(ctx, mset, view_cur)
        row: dict = {"month": m}
        bexp = rollup(budgeted(ctx, mset, BUDGET_EXPENDITURE, view_cur)) if include_budget else {}
        binc = budgeted(ctx, mset, BUDGET_INCOME, view_cur) if include_budget else {}
        for key in keys:
            kind, code = key.split(":", 1)
            src, bsrc = (exp, bexp) if kind == "EXP" else (inc, binc)
            if code == "TOTAL":
                top = [*get_coicop().divisions, TAX, ADV] if kind == "EXP" else list(src)
                value = sum(src.get(c, 0.0) for c in top)
                bvalue = sum(bsrc.get(c, 0.0) for c in (top if kind == "EXP" else bsrc))
            else:
                value, bvalue = src.get(code, 0.0), bsrc.get(code, 0.0)
            row[labels.get(key, key)] = round(value, 2)
            if include_budget:
                row[f"{labels.get(key, key)} (budget)"] = round(bvalue, 2)
        rows.append(row)
    return rows


def account_changes(ctx: Context, start: str, end: str, view_cur: str) -> list[dict]:
    """Per-account change in balance (view currency) between the end of `start` and the end of `end`."""
    before = {r.account.id: r for r in wealth_svc.account_rows(ctx, start, view_cur)}
    after = {r.account.id: r for r in wealth_svc.account_rows(ctx, end, view_cur)}
    out = []
    for acc_id in sorted(set(before) | set(after)):
        acc = (after[acc_id] if acc_id in after else before[acc_id]).account
        b = before[acc_id].amount_view if acc_id in before else 0.0
        a = after[acc_id].amount_view if acc_id in after else 0.0
        out.append(
            {
                "account": f"{acc.name} ({acc.currency})",
                "start": round(b, 2),
                "end": round(a, 2),
                "change": round(a - b, 2),
            }
        )
    return out
