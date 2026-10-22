"""Monthly income/expenditure statements and time series for charts.

Display conversion rules:
* transactions are converted with the ECB rate of their recorded `fx_date`,
* budget entries are converted with the latest available ECB rate,
* account balances are valued with the last ECB rate of the month (see wealth.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from budget_app.domain import months
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, REVAL_FX, REVAL_INVEST, REVALUATION_KINDS
from budget_app.i18n import t
from budget_app.services import wealth as wealth_svc
from budget_app.services.aggregates import (  # noqa: F401 - re-exported for callers of reports
    ADV_REPAY,
    ADV_REPAY_NAME,
    account_flows,
    actual_expenditure,
    actual_income,
    booked_revaluations,
    budgeted,
    expenditure_parts,
    income_key,
    rollup,
    top_expenditure_codes,
    tx_value,
)
from budget_app.services.context import Context


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
    total_expenditure: Line = field(default_factory=lambda: Line("TOTAL", t("Total expenditure")))
    total_income: Line = field(default_factory=lambda: Line("TOTAL", t("Total income")))
    wealth_start: float | None = None
    wealth_end: float | None = None
    # revaluations (F45): booked per kind, plus exchange-rate moves on foreign balances (complete months)
    revaluation_lines: list[Line] = field(default_factory=list)
    total_revaluation: Line = field(default_factory=lambda: Line("TOTAL", t("Total revaluations")))
    fx_translation: float = 0.0
    # reconciliation (F42), for complete months with balances at both ends
    balance_change: float | None = None  # wealth_end - wealth_start
    # balance_change - (income - expenditure) - revaluations; negative: money left without a record
    unexplained: float | None = None
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
    for code in top_expenditure_codes():
        if code not in rep.expenditure_tree:
            cat = coicop.categories[code]  # divisions and pseudo-categories always exist
            rep.expenditure_tree[code] = Line(
                code, cat.label, level=1, has_children=bool(cat.children), editable=code != ADV
            )
    rep.top_expenditure = top_expenditure_codes()

    act_inc = actual_income(ctx, mset, view_cur)
    bud_inc = budgeted(ctx, mset, BUDGET_INCOME, view_cur)
    names = {c.id: c.name for c in ctx.store.income_categories.all()} | {ADV_REPAY: t(ADV_REPAY_NAME)}
    for key in sorted(set(act_inc) | set(bud_inc), key=lambda k: (names.get(k) or k).lower()):
        line = Line(key, names.get(key, key), act_inc.get(key, 0.0), bud_inc.get(key, 0.0), editable=key != ADV_REPAY)
        if abs(line.actual) > 0.005 or abs(line.budget) > 0.005:
            rep.income_lines.append(line)

    total_inc_act = sum(act_inc.values())
    recorded_exp = sum(rep.expenditure_tree[c].actual for c in rep.top_expenditure)
    total_exp_budget = sum(rep.expenditure_tree[c].budget for c in rep.top_expenditure)

    prev = months.add_months(month, -1)
    if complete and wealth_svc.has_wealth_data(ctx, prev):
        rep.wealth_start = wealth_svc.wealth(ctx, prev, view_cur)
        rep.wealth_end = wealth_svc.wealth(ctx, month, view_cur)
        rep.balance_change = rep.wealth_end - rep.wealth_start

    rep.total_expenditure = Line("TOTAL", t("Total expenditure"), recorded_exp, total_exp_budget)
    rep.total_income = Line("TOTAL", t("Total income"), total_inc_act, sum(bud_inc.values()))

    if complete:
        flows = account_flows(ctx, month, view_cur)
        start_rate, end_rate = ctx.month_rate_date(prev), ctx.month_rate_date(month)
        for r in wealth_svc.account_rows(ctx, month, view_cur):
            before = wealth_svc.balance_entry(ctx, r.account.id, prev)
            r.opening = before.amount if before else (0.0 if r.account.holds_securities else None)
            moves = flows.get(r.account.id, [])
            r.flows = sum(a for a, _ in moves)
            if r.has_entry and r.opening is not None:
                r.unexplained = round(r.amount - r.opening - r.flows, 2)
            if rep.balance_change is not None:
                # opening balance and each movement revalued from its own rate to the month-end rate
                cur, opening = r.account.currency, r.opening or 0.0
                r.fx_gain = round(
                    ctx.convert_on(opening, cur, view_cur, end_rate)
                    - ctx.convert_on(opening, cur, view_cur, start_rate)
                    + ctx.convert_on(r.flows, cur, view_cur, end_rate)
                    - sum(v for _, v in moves),
                    2,
                )
                rep.fx_translation += r.fx_gain
            if abs(r.amount) > 0.005 or r.has_entry or abs(r.opening or 0.0) > 0.005:
                rep.accounts.append(r)
    else:
        rep.projected_wealth = wealth_svc.projected_wealth(ctx, month, view_cur)

    booked = booked_revaluations(ctx, month, view_cur)
    rep.revaluation_lines = [
        Line(REVAL_FX, t(REVALUATION_KINDS[REVAL_FX]), booked.get(REVAL_FX, 0.0) + rep.fx_translation),
        Line(REVAL_INVEST, t(REVALUATION_KINDS[REVAL_INVEST]), booked.get(REVAL_INVEST, 0.0)),
    ]
    rep.total_revaluation = Line("TOTAL", t("Total revaluations"), sum(line.actual for line in rep.revaluation_lines))
    if rep.balance_change is not None:
        rep.unexplained = rep.balance_change - (total_inc_act - recorded_exp) - rep.total_revaluation.actual
    return rep


# ----------------------------------------------------------------- time series
def series_keys(ctx: Context) -> dict[str, str]:
    """Selectable chart series: expenditure divisions, taxes, advances and income categories."""
    coicop = get_coicop()
    out = {"EXP:TOTAL": t("Total expenditure"), "INC:TOTAL": t("Total income")}
    for code in [*coicop.divisions, TAX, ADV]:
        out[f"EXP:{code}"] = coicop.label(code)
    for c in ctx.store.income_categories.all():
        out[f"INC:{c.id}"] = t("Income: {name}", name=c.name)
    out[f"INC:{ADV_REPAY}"] = t("Income: {name}", name=t(ADV_REPAY_NAME))
    out[UNEXPLAINED_KEY] = t("Unexplained difference")
    return out


UNEXPLAINED_KEY = "REC:UNEXPLAINED"


def unexplained(ctx: Context, month: str, view_cur: str, today: date | None = None) -> float | None:
    """The reconciliation's unexplained difference of a month (None without balances at both ends)."""
    return month_report(ctx, month, view_cur, today).unexplained


def budget_label(label: str) -> str:
    """Name of the budget series next to an actual series in `monthly_series`."""
    return t("{series} (budget)", series=label)


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
            if key == UNEXPLAINED_KEY:  # no budget; empty for months without balances at both ends
                gap = unexplained(ctx, m, view_cur)
                row[labels[key]] = None if gap is None else round(gap, 2)
                continue
            kind, code = key.split(":", 1)
            src, bsrc = (exp, bexp) if kind == "EXP" else (inc, binc)
            if code == "TOTAL":
                top = top_expenditure_codes() if kind == "EXP" else list(src)
                value = sum(src.get(c, 0.0) for c in top)
                bvalue = sum(bsrc.get(c, 0.0) for c in (top if kind == "EXP" else bsrc))
            else:
                value, bvalue = src.get(code, 0.0), bsrc.get(code, 0.0)
            row[labels.get(key, key)] = round(value, 2)
            if include_budget:
                row[budget_label(labels.get(key, key))] = round(bvalue, 2)
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
