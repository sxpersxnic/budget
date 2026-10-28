"""Wealth from monthly account balances (account entries), and its projection from budget and records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_REGULAR, ASSET_CASH, ASSET_CLASSES, ASSET_OTHER, Account, AccountEntry
from budget_app.i18n import N_, t
from budget_app.services import accounts as acc_svc
from budget_app.services import investments as inv_svc
from budget_app.services.aggregates import projected_net
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

# a month's expected net: `aggregates.projected_net` for the plan; a what-if scenario (F15) passes its own
NetFn = Callable[[Context, str, str], float]


@dataclass
class AccountRow:
    account: Account
    amount: float  # in account currency (carried forward if no entry this month)
    amount_view: float  # in the view currency
    has_entry: bool  # an entry exists for exactly this month
    needs_entry: bool  # no entry for this month but the last entry was non-zero
    opening: float | None = None  # balance at the end of the previous month (account currency), if any
    flows: float = 0.0  # the month's recorded movements, in the account's currency
    unexplained: float | None = None  # change minus flows (F46); only with a balance recorded for this month
    fx_gain: float = 0.0  # value change from exchange-rate moves, in the view currency (decision 4)
    valued: bool = False  # valued from its holdings this month (F21), not from a recorded balance

    @property
    def change(self) -> float | None:
        """Change over the month in the account's currency (F41); None without an opening balance."""
        return None if self.opening is None else self.amount - self.opening


def entry_for(ctx: Context, account_id: str, month: str) -> AccountEntry | None:
    for e in ctx.store.account_entries.all():
        if e.account_id == account_id and e.month == month:
            return e
    return None


def balance_entry(ctx: Context, account_id: str, month: str) -> AccountEntry | None:
    """Latest entry for the account in or before `month` (balances carry forward).

    A securities or crypto account has no recorded entries: its balance is computed from its holdings (F21). One
    converted from an account with history (decision 13) keeps its recorded balances before the conversion month.
    """
    acc = ctx.store.accounts.get(account_id)
    if acc is not None and acc.holds_in(month):
        return inv_svc.value_entry(ctx, acc, month)
    best = None
    for e in ctx.store.account_entries.all():
        if e.account_id == account_id and e.month <= month and (best is None or e.month > best.month):
            best = e
    return best


@recorded(N_("Record a balance"))
def upsert_entry(ctx: Context, account_id: str, month: str, amount: float | None) -> AccountEntry:
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        raise ValidationError(t("Select an account"))
    if not month:
        raise ValidationError(t("Select a month"))
    if amount is None:
        raise ValidationError(t("Enter the amount"))
    if acc.holds_in(month):
        if acc.invest_from:
            raise ValidationError(
                t(
                    "{name} is valued from its holdings from {month} on; balances can be recorded for earlier months",
                    name=acc.name,
                    month=months.label(acc.invest_from),
                )
            )
        raise ValidationError(t("A securities or crypto account is valued from its holdings, not from balances"))
    rate_date = ctx.month_rate_date(month)
    amount = round(float(amount), 2)
    amount_chf = round(ctx.convert_on(amount, acc.currency, BASE_CURRENCY, rate_date), 6)
    existing = entry_for(ctx, account_id, month)
    if existing:
        updated = replace(existing, amount=amount, currency=acc.currency, amount_chf=amount_chf, fx_date=rate_date)
        saved = ctx.store.account_entries.update(updated)
    else:
        saved = ctx.store.account_entries.add(
            AccountEntry(
                account_id=account_id,
                month=month,
                amount=amount,
                currency=acc.currency,
                amount_chf=amount_chf,
                fx_date=rate_date,
            )
        )
    if acc.invest_from:  # a balance before the conversion: its revaluation starts from the last one (decision 13)
        inv_svc.book_conversion(ctx, acc.id)
    return saved


def first_recorded_month(ctx: Context) -> str | None:
    """First month with a balance: a recorded one, or a securities or crypto account's first trade or price."""
    ms = [e.month for e in ctx.store.account_entries.all()]
    if held := inv_svc.first_month(ctx):
        ms.append(held)
    return min(ms) if ms else None


def last_recorded_month(ctx: Context) -> str | None:
    """Last month with at least one recorded account entry (holdings don't move the projection's start)."""
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
        valued = acc.holds_in(month)
        rows.append(
            AccountRow(
                acc,
                entry.amount,
                ctx.convert_on(entry.amount, acc.currency, view_cur, rate_date),
                has,
                needs_entry=not has and abs(entry.amount) > 0.005 and not valued,
                valued=valued,
            )
        )
    return rows


def wealth(ctx: Context, month: str, view_cur: str) -> float:
    return sum(r.amount_view for r in account_rows(ctx, month, view_cur))


def by_class(ctx: Context, month: str, view_cur: str) -> dict[str, float]:
    """Wealth at the end of `month` per asset class (F21), in the order of `ASSET_CLASSES`; empty classes left out."""
    sums = dict.fromkeys(ASSET_CLASSES, 0.0)
    for r in account_rows(ctx, month, view_cur):
        sums[r.account.asset_class if r.account.asset_class in sums else ASSET_OTHER] += r.amount_view
    return {k: v for k, v in sums.items() if abs(v) > 0.005}


def uses_classes(ctx: Context) -> bool:
    """Whether any regular account has an asset class other than cash."""
    return any(a.asset_class != ASSET_CASH for a in acc_svc.accounts(ctx, ACCOUNT_REGULAR))


def has_wealth_data(ctx: Context, month: str) -> bool:
    first = first_recorded_month(ctx)
    return first is not None and first <= month


def _first_activity_month(ctx: Context) -> str | None:
    """Earliest month with a budget entry or a transaction (where a projection without balances starts)."""
    found = [b.month for b in ctx.store.budget.all()]
    found += [months.month_of(tx.date) for tx in ctx.store.transactions.all()]
    return min(found) if found else None


def _projection_start(ctx: Context, view_cur: str) -> tuple[str | None, float]:
    """First projected month and the wealth it builds on (last recorded month's wealth)."""
    last = last_recorded_month(ctx)
    if last is None:
        return _first_activity_month(ctx), 0.0
    return months.add_months(last, 1), wealth(ctx, last, view_cur)


def projected_wealth(ctx: Context, month: str, view_cur: str, net: NetFn = projected_net) -> float:
    """Wealth of the last recorded month plus the expected net of every month after it up to `month`.

    A month's expected net takes, per category, the higher of recorded and budgeted amounts
    (`aggregates.projected_net`); this holds for months that are over but have no balances yet too. A scenario
    (F15) passes its own `net`, which lays its changes over the plan's budgets.
    """
    last = last_recorded_month(ctx)
    if last is not None and month <= last:
        return wealth(ctx, month, view_cur)
    start, base = _projection_start(ctx, view_cur)
    if start is None or start > month:
        return base
    return base + sum(net(ctx, m, view_cur) for m in months.month_range(start, month))


def current_wealth(ctx: Context, view_cur: str, today: date | None = None) -> float:
    """Wealth at the end of last month."""
    return wealth(ctx, months.previous_month(today), view_cur)


def wealth_series(
    ctx: Context, view_cur: str, end: str | None = None, today: date | None = None, net: NetFn = projected_net
) -> list[tuple[str, float, bool]]:
    """(month, wealth, is_projection) from the first recorded month to `end` (default: horizon); the projection
    with the plan's budgets, or with a scenario's `net` (F15)."""
    end = end or ctx.horizon()
    last = last_recorded_month(ctx)
    out: list[tuple[str, float, bool]] = []
    first = first_recorded_month(ctx)
    if last is not None and first is not None:
        out = [(m, wealth(ctx, m, view_cur), False) for m in months.month_range(first, min(last, end))]
    start, running = _projection_start(ctx, view_cur)
    if last is None:
        start = min(start or months.current_month(today), months.current_month(today))
    if start is not None:
        for m in months.month_range(start, end):
            running += net(ctx, m, view_cur)
            out.append((m, running, True))
    return out
