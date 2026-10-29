"""Sums of transactions and budget entries per category, month and account, in a view currency.

Shared by the monthly statement (reports.py) and the wealth projection (wealth.py).

Every sum of expenditure by category or tag goes through `transaction_parts`: a split transaction (F11) counts
each item line with a category of its own there, the rest in its own category (`domain.splits`). Net totals,
account flows and the reconciliation use whole transactions (`tx_value`), which the parts add up to.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months, splits
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import (
    BUDGET_EXPENDITURE,
    BUDGET_INCOME,
    PROJECTION_BUDGET,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    Transaction,
)
from budget_app.i18n import N_
from budget_app.services.context import Context

ADV_REPAY = "ADV_REPAY"
ADV_REPAY_NAME = N_("Advance repayments")  # display with t()


def tx_value(ctx: Context, tx: Transaction, view_cur: str) -> float:
    """Transaction amount in the view currency, using the rate recorded with the transaction."""
    if tx.currency == view_cur:
        return tx.amount
    return ctx.convert_on(tx.amount_chf, BASE_CURRENCY, view_cur, tx.fx_date)


# ------------------------------------------------------------------ parts of a transaction (F11)
def split_lines(ctx: Context) -> dict[str, list[splits.Line]]:
    """The item lines that may split their transaction (a category or tags of their own), per transaction id."""
    out: dict[str, list[splits.Line]] = defaultdict(list)
    for item in sorted(ctx.store.transaction_items.all(), key=lambda i: i.position):
        if item.category or item.tags:
            out[item.transaction_id].append(splits.Line(item.total, item.category, item.tags))
    return dict(out)


def transaction_parts(ctx: Context, tx: Transaction, lines: list[splits.Line] | None = None) -> list[splits.Part]:
    """The parts a transaction counts in: (category, amount, amount_chf, tags), adding up to it exactly.

    Only expenditure is split (by its item lines, decision 14, and its advance share); everything else is one
    part. `lines` are the transaction's entry of `split_lines`, looked up when not given; callers that walk many
    transactions pass them, so the items are read once.
    """
    if tx.type != TX_EXPENDITURE:
        return [splits.Part(tx.category, tx.amount, tx.amount_chf, tx.tags)]
    if lines is None:
        lines = split_lines(ctx).get(tx.id, [])
    advance = tx.advance_amount if tx.is_advance else 0.0
    return splits.parts(tx.amount, tx.amount_chf, tx.category, lines, advance, tx.tags)


def part_value(ctx: Context, tx: Transaction, part: splits.Part, view_cur: str) -> float:
    """A part in the view currency, converted the way the transaction is (`tx_value`)."""
    if tx.currency == view_cur:
        return part.amount
    return ctx.convert_on(part.amount_chf, BASE_CURRENCY, view_cur, tx.fx_date)


def expenditure_parts(
    ctx: Context, tx: Transaction, view_cur: str, lines: list[splits.Line] | None = None
) -> list[tuple[str, float]]:
    """(category, amount) parts of an expenditure in the view currency: the rest in its own category, each split
    line in its category, the advance share in ADV."""
    return [(p.category, part_value(ctx, tx, p, view_cur)) for p in transaction_parts(ctx, tx, lines)]


def is_split(ctx: Context, tx: Transaction, lines: list[splits.Line] | None = None) -> bool:
    """Whether an expenditure counts in more than its own category (and ADV)."""
    return any(p.category not in (tx.category, ADV) for p in transaction_parts(ctx, tx, lines))


def income_key(tx: Transaction) -> str:
    return ADV_REPAY if tx.is_advance else tx.category


def _in_months(d: date, month_set: set[str]) -> bool:
    return months.month_of(d) in month_set


# ------------------------------------------------------------------ aggregates
def actual_expenditure(ctx: Context, month_set: set[str], view_cur: str) -> dict[str, float]:
    """Direct (not rolled up) actual expenditure per category code; split transactions count per part (F11)."""
    out: dict[str, float] = defaultdict(float)
    lines = split_lines(ctx)
    for tx in ctx.store.transactions.all():
        if tx.type == TX_EXPENDITURE and _in_months(tx.date, month_set):
            for code, value in expenditure_parts(ctx, tx, view_cur, lines.get(tx.id, [])):
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
    """Income minus expenditure a month is expected to end with, by the rule chosen in Settings.

    `PROJECTION_HIGHER` (F47, the default): per top-level expenditure row and per income category, the higher of
    what is recorded and what is budgeted: recorded overspending (or extra income) counts, an unspent budget still
    does too; advances and their repayments have no budget, so they count as recorded. `PROJECTION_BUDGET`: the
    budgeted income minus the budgeted expenditure, whatever is recorded.
    """
    mset = {month}
    return net_with_budgets(
        ctx,
        month,
        view_cur,
        budgeted(ctx, mset, BUDGET_EXPENDITURE, view_cur),
        budgeted(ctx, mset, BUDGET_INCOME, view_cur),
    )


def net_with_budgets(
    ctx: Context, month: str, view_cur: str, exp_budget: dict[str, float], inc_budget: dict[str, float]
) -> float:
    """`projected_net` with the budgets given (direct, per category, in the view currency) instead of the plan's: a
    what-if scenario (F15) lays its changes over the plan and projects with the result, by the same rule."""
    exp_bud = rollup(exp_budget)
    if ctx.store.settings.projection == PROJECTION_BUDGET:  # the same rows, the budgets alone
        return sum(inc_budget.values()) - sum(exp_bud.get(c, 0.0) for c in top_expenditure_codes())
    mset = {month}
    exp_act = rollup(actual_expenditure(ctx, mset, view_cur))
    spending = sum(max(exp_act.get(c, 0.0), exp_bud.get(c, 0.0)) for c in top_expenditure_codes())
    inc_act = actual_income(ctx, mset, view_cur)
    income = sum(max(inc_act.get(k, 0.0), inc_budget.get(k, 0.0)) for k in set(inc_act) | set(inc_budget))
    return income - spending


# ------------------------------------------------------------------ account flows (F45, F46)
def account_flows(ctx: Context, month: str, view_cur: str) -> dict[str, list[tuple[float, float]]]:
    """Every movement of a month per account: (amount in the account's currency, value in `view_cur`).

    The value uses each transaction's own rate, like the statement does. Income and incoming
    transfers, exchanges and gains are positive; spending (the full amount, including a share
    paid for others) and outgoing transfers, exchanges and losses are negative.
    """
    out: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for tx in ctx.store.transactions.all():
        if months.month_of(tx.date) != month:
            continue
        value = tx_value(ctx, tx, view_cur)
        if tx.from_account:
            out[tx.from_account].append((-tx.amount, -value))
        if tx.to_account:
            if tx.type == TX_EXCHANGE:  # credited in the other currency, at the exchange's rate date
                received = ctx.convert_on(tx.to_amount, tx.to_currency, view_cur, tx.fx_date)
                out[tx.to_account].append((tx.to_amount, received))
            else:
                out[tx.to_account].append((tx.amount, value))
    return dict(out)


def booked_revaluations(ctx: Context, month: str, view_cur: str) -> dict[str, float]:
    """Revaluations of a month per kind (REVAL_FX, REVAL_INVEST), signed, in `view_cur`."""
    out: dict[str, float] = defaultdict(float)
    for tx in ctx.store.transactions.all():
        if tx.type == TX_REVALUATION and months.month_of(tx.date) == month:
            value = tx_value(ctx, tx, view_cur)
            out[tx.category] += value if tx.to_account else -value
    return dict(out)
