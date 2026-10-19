"""Sums of transactions and budget entries per category and month, in a view currency.

Shared by the monthly statement (reports.py) and the wealth projection (wealth.py).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, TX_EXPENDITURE, TX_INCOME, Transaction
from budget_app.i18n import N_
from budget_app.services.context import Context

ADV_REPAY = "ADV_REPAY"
ADV_REPAY_NAME = N_("Advance repayments")  # display with t()


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
    for tx in ctx.store.transactions.all():
        if tx.type == TX_EXPENDITURE and _in_months(tx.date, month_set):
            for code, value in expenditure_parts(ctx, tx, view_cur):
                out[code] += value
    return dict(out)


def actual_income(ctx: Context, month_set: set[str], view_cur: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for tx in ctx.store.transactions.all():
        if tx.type == TX_INCOME and _in_months(tx.date, month_set):
            out[income_key(tx)] += tx_value(ctx, tx, view_cur)
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


def top_expenditure_codes() -> list[str]:
    """The expenditure rows of the statement's top level: COICOP divisions, taxes, advances."""
    return [*get_coicop().divisions, TAX, ADV]


def projected_net(ctx: Context, month: str, view_cur: str) -> float:
    """Income minus expenditure a month is expected to end with (F47).

    Per top-level expenditure row and per income category, the higher of what is recorded and what
    is budgeted: recorded overspending (or extra income) counts, an unspent budget still does too.
    Advances and their repayments have no budget, so they count as recorded.
    """
    mset = {month}
    exp_act = rollup(actual_expenditure(ctx, mset, view_cur))
    exp_bud = rollup(budgeted(ctx, mset, BUDGET_EXPENDITURE, view_cur))
    spending = sum(max(exp_act.get(c, 0.0), exp_bud.get(c, 0.0)) for c in top_expenditure_codes())
    inc_act = actual_income(ctx, mset, view_cur)
    inc_bud = budgeted(ctx, mset, BUDGET_INCOME, view_cur)
    income = sum(max(inc_act.get(k, 0.0), inc_bud.get(k, 0.0)) for k in set(inc_act) | set(inc_bud))
    return income - spending
