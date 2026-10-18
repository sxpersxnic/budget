"""Wealth from monthly account balances (account entries) and projections from the budget."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_REGULAR, BUDGET_EXPENDITURE, BUDGET_INCOME, Account, AccountEntry
from budget_app.i18n import t
from budget_app.services import accounts as acc_svc
from budget_app.services.context import Context, ValidationError


@dataclass
class AccountRow:
    account: Account
    amount: float  # in account currency (carried forward if no entry this month)
    amount_view: float  # in the view currency
    has_entry: bool  # an entry exists for exactly this month
    needs_entry: bool  # no entry for this month but the last entry was non-zero


def entry_for(ctx: Context, account_id: str, month: str) -> AccountEntry | None:
    for e in ctx.store.account_entries.all():
        if e.account_id == account_id and e.month == month:
            return e
    return None


def balance_entry(ctx: Context, account_id: str, month: str) -> AccountEntry | None:
    """Latest entry for the account in or before `month` (balances carry forward)."""
    best = None
    for e in ctx.store.account_entries.all():
        if e.account_id == account_id and e.month <= month and (best is None or e.month > best.month):
            best = e
    return best


def upsert_entry(ctx: Context, account_id: str, month: str, amount: float | None) -> AccountEntry:
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        raise ValidationError(t("Select an account"))
    if not month:
        raise ValidationError(t("Select a month"))
    if amount is None:
        raise ValidationError(t("Enter the amount"))
    rate_date = ctx.month_rate_date(month)
    amount = round(float(amount), 2)
    amount_chf = round(ctx.convert_on(amount, acc.currency, BASE_CURRENCY, rate_date), 6)
    existing = entry_for(ctx, account_id, month)
    if existing:
        updated = replace(existing, amount=amount, currency=acc.currency, amount_chf=amount_chf, fx_date=rate_date)
        return ctx.store.account_entries.update(updated)
    return ctx.store.account_entries.add(
        AccountEntry(
            account_id=account_id,
            month=month,
            amount=amount,
            currency=acc.currency,
            amount_chf=amount_chf,
            fx_date=rate_date,
        )
    )


def first_recorded_month(ctx: Context) -> str | None:
    ms = [e.month for e in ctx.store.account_entries.all()]
    return min(ms) if ms else None


def last_recorded_month(ctx: Context) -> str | None:
    """Last month with at least one account entry."""
    ms = [e.month for e in ctx.store.account_entries.all()]
    return max(ms) if ms else None


def account_rows(ctx: Context, month: str, view_cur: str) -> list[AccountRow]:
    rate_date = ctx.month_rate_date(month)
    rows = []
    for acc in acc_svc.accounts(ctx, ACCOUNT_REGULAR):
        entry = balance_entry(ctx, acc.id, month)
        if entry is None:
            continue
        has = entry.month == month
        rows.append(
            AccountRow(
                acc,
                entry.amount,
                ctx.convert_on(entry.amount, acc.currency, view_cur, rate_date),
                has,
                needs_entry=not has and abs(entry.amount) > 0.005,
            )
        )
    return rows


def wealth(ctx: Context, month: str, view_cur: str) -> float:
    return sum(r.amount_view for r in account_rows(ctx, month, view_cur))


def has_wealth_data(ctx: Context, month: str) -> bool:
    first = first_recorded_month(ctx)
    return first is not None and first <= month


def budget_net_by_month(ctx: Context, view_cur: str) -> dict[str, float]:
    """Budgeted income minus budgeted expenditure per month (latest exchange rates)."""
    out: dict[str, float] = {}
    for b in ctx.store.budget.all():
        value = ctx.convert_on(b.amount, b.currency, view_cur, None)
        sign = 1.0 if b.type == BUDGET_INCOME else -1.0 if b.type == BUDGET_EXPENDITURE else 0.0
        out[b.month] = out.get(b.month, 0.0) + sign * value
    return out


def _projection_start(ctx: Context, view_cur: str, net: dict[str, float]) -> tuple[str | None, float]:
    """First projected month and the wealth it builds on (last recorded month's wealth)."""
    last = last_recorded_month(ctx)
    if last is None:
        return (min(net) if net else None), 0.0
    return months.add_months(last, 1), wealth(ctx, last, view_cur)


def projected_wealth(ctx: Context, month: str, view_cur: str) -> float:
    """Wealth of the last recorded month plus the budgeted net of every month after it up to `month`."""
    last = last_recorded_month(ctx)
    if last is not None and month <= last:
        return wealth(ctx, month, view_cur)
    net = budget_net_by_month(ctx, view_cur)
    start, base = _projection_start(ctx, view_cur, net)
    if start is None:
        return base
    return base + sum(v for m, v in net.items() if start <= m <= month)


def current_wealth(ctx: Context, view_cur: str, today: date | None = None) -> float:
    """Wealth at the end of last month."""
    return wealth(ctx, months.previous_month(today), view_cur)


def wealth_series(
    ctx: Context, view_cur: str, end: str | None = None, today: date | None = None
) -> list[tuple[str, float, bool]]:
    """(month, wealth, is_projection) from the first recorded month to `end` (default: horizon)."""
    end = end or ctx.horizon()
    last = last_recorded_month(ctx)
    out: list[tuple[str, float, bool]] = []
    first = first_recorded_month(ctx)
    if last is not None and first is not None:
        out = [(m, wealth(ctx, m, view_cur), False) for m in months.month_range(first, min(last, end))]
    net = budget_net_by_month(ctx, view_cur)
    start, running = _projection_start(ctx, view_cur, net)
    if last is None:
        start = min(start or months.current_month(today), months.current_month(today))
    if start is not None:
        for m in months.month_range(start, end):
            running += net.get(m, 0.0)
            out.append((m, running, True))
    return out
