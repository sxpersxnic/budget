"""Recurring transactions (F05): series, the occurrences that are due, booking and skipping them.

A series is a transaction template with a schedule. Its occurrences become transactions only when
they are booked: from the Due list, one by one or all at once (the amount and date can be changed
first), or, for a series that books automatically, without asking when the app starts and at each
refresh (`book_automatic`). Booking goes through `transactions.save`, so validation and valuation
are those of a transaction entered by hand. `Recurring.next_due` is the first occurrence not yet
booked or skipped; occurrences are handled in order.

Editing a series changes its future occurrences only: transactions it booked stay as they are.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date

from budget_app.domain.coicop import get_coicop
from budget_app.domain.fx import FxError
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, TX_TRANSFER, Recurring, Transaction
from budget_app.i18n import N_, t
from budget_app.services import payees as payee_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.services.undo import recorded

log = logging.getLogger(__name__)

RECURRING_TYPES = (TX_EXPENDITURE, TX_INCOME, TX_TRANSFER)


@dataclass
class RecurringInput:
    type: str = TX_EXPENDITURE
    amount: float | None = None
    currency: str = ""
    category: str = ""
    from_account: str = ""
    to_account: str = ""
    comment: str = ""
    start: date | None = None
    recur_months: int = 1
    end: date | None = None  # None: no end
    auto: bool = False
    payee: str = ""  # expenditure and income (F09)


@dataclass
class Occurrence:
    """One due occurrence of a series."""

    series: Recurring
    date: date
    first: bool = True  # the series' earliest due occurrence: the one to book or skip next


@dataclass
class Booking:
    """What booking several occurrences did: the transactions booked and the occurrences that failed."""

    booked: list[Transaction] = field(default_factory=list)
    failed: list[tuple[Occurrence, str]] = field(default_factory=list)


# ------------------------------------------------------------------ schedule
def occurrence(start: date, recur_months: int, k: int) -> date:
    """The k-th occurrence (0 = start): the start's day, or the month's last day if it is shorter."""
    year, month0 = divmod(start.year * 12 + start.month - 1 + k * recur_months, 12)
    month = month0 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def next_after(start: date, recur_months: int, after: date) -> date:
    """The first occurrence later than `after` (the start, if `after` is before it)."""
    k = max(0, ((after.year - start.year) * 12 + after.month - start.month) // recur_months)
    d = occurrence(start, recur_months, k)
    while d <= after:
        k += 1
        d = occurrence(start, recur_months, k)
    return d


def previous_before(start: date, recur_months: int, before: date) -> date | None:
    """The last occurrence earlier than `before`, or None when `before` is on or before the start."""
    if before <= start:
        return None
    k = max(0, ((before.year - start.year) * 12 + before.month - start.month) // recur_months)
    while k > 0 and occurrence(start, recur_months, k) >= before:
        k -= 1
    return occurrence(start, recur_months, k)


def ended(series: Recurring) -> bool:
    end = series.end_date
    return end is not None and series.next_due > end


# ------------------------------------------------------------------ series
def _template(inp: RecurringInput) -> TransactionInput:
    """The transaction an occurrence of `inp` books, dated on its first occurrence."""
    return TransactionInput(
        date=inp.start,
        type=inp.type,
        amount=inp.amount,
        currency=inp.currency,
        category=inp.category if inp.type != TX_TRANSFER else "",
        from_account=inp.from_account if inp.type != TX_INCOME else "",
        to_account=inp.to_account if inp.type != TX_EXPENDITURE else "",
        comment=inp.comment,
        payee=inp.payee if inp.type in payee_svc.PAYEE_TYPES else "",
    )


def validate(ctx: Context, inp: RecurringInput) -> None:
    if inp.type not in RECURRING_TYPES:
        raise ValidationError(t("Select expenditure, income or account transfer"))
    if inp.start is None:
        raise ValidationError(t("Select the first date"))
    if inp.recur_months < 1:
        raise ValidationError(t("The recurrence must be at least every month"))
    if inp.end is not None and inp.end < inp.start:
        raise ValidationError(t("Until must not be before the first date"))
    tx_svc.validate(ctx, _template(inp))


def to_input(series: Recurring) -> RecurringInput:
    return RecurringInput(
        type=series.type,
        amount=series.amount,
        currency=series.currency,
        category=series.category,
        from_account=series.from_account,
        to_account=series.to_account,
        comment=series.comment,
        start=series.start,
        recur_months=series.recur_months,
        end=series.end_date,
        auto=series.auto,
        payee=series.payee,
    )


def from_transaction(tx: Transaction, recur_months: int = 1) -> RecurringInput:
    """A series that continues `tx`: same template, first occurrence on its date."""
    return RecurringInput(
        type=tx.type,
        amount=tx.amount,
        currency=tx.currency,
        category=tx.category,
        from_account=tx.from_account,
        to_account=tx.to_account,
        comment=tx.comment,
        start=tx.date,
        recur_months=recur_months,
        payee=tx.payee,
    )


def can_continue(tx: Transaction) -> bool:
    """Whether a transaction can start a series: expenditure, income or transfer, without an advance."""
    return tx.type in RECURRING_TYPES and not tx.is_advance


def planned_next_due(ctx: Context, inp: RecurringInput, editing_id: str = "", first_booked: str = "") -> date:
    """The first occurrence `save` leaves to be handled.

    A new series starts with its first occurrence, or, continuing a transaction, with the first one
    after it. A changed series continues after the last occurrence it booked or skipped (from the
    start if there is none), on the new schedule.
    """
    start = inp.start or date.today()
    old = ctx.store.recurring.get(editing_id) if editing_id else None
    if old is not None:
        handled = previous_before(old.start, old.recur_months, old.next_due)
        return next_after(start, inp.recur_months, handled) if handled else start
    tx = ctx.store.transactions.get(first_booked) if first_booked else None
    return next_after(start, inp.recur_months, tx.date) if tx else start


@recorded(N_("Save a recurring transaction"))
def save(ctx: Context, inp: RecurringInput, editing_id: str = "", first_booked: str = "") -> Recurring:
    """Create a series, or change one (`editing_id`); see `planned_next_due` for where it continues.

    `first_booked` names a transaction that is the series' first booking ("Make recurring"): it is
    linked to the series, and the occurrences up to its date count as handled.
    """
    validate(ctx, inp)
    if editing_id and ctx.store.recurring.get(editing_id) is None:
        raise ValidationError(t("Recurring transaction not found"))
    tpl = _template(inp)
    start = inp.start or date.today()  # validated above
    series = Recurring(
        type=inp.type,
        amount=round(float(inp.amount or 0), 2),
        currency=inp.currency,
        start=start,
        next_due=planned_next_due(ctx, inp, editing_id, first_booked),
        category=tpl.category,
        from_account=tpl.from_account,
        to_account=tpl.to_account,
        comment=inp.comment.strip(),
        recur_months=inp.recur_months,
        end=inp.end.isoformat() if inp.end else "",
        auto=inp.auto,
        payee=payee_svc.ensure(ctx, tpl.payee),
    )
    if editing_id:
        series.id = editing_id
        return ctx.store.recurring.update(series)
    if first_booked:
        tx = ctx.store.transactions.get(first_booked)
        if tx is None:
            raise ValidationError(t("Transaction not found"))
        tx.recurring_id = series.id
        ctx.store.transactions.update(tx)
    return ctx.store.recurring.add(series)


@recorded(N_("Delete a recurring transaction"))
def delete(ctx: Context, ids: list[str]) -> int:
    """Delete series; the transactions they booked stay."""
    return ctx.store.recurring.delete(ids)


def series(ctx: Context) -> list[Recurring]:
    """Every series: running ones by their next date, ended ones last."""
    return sorted(ctx.store.recurring.all(), key=lambda r: (ended(r), r.next_due, r.id))


def title(ctx: Context, series: Recurring) -> str:
    """What a series is about: its payee and category, or for a transfer its accounts."""
    if series.type == TX_EXPENDITURE:
        label = get_coicop().label(series.category)
        return f"{series.payee} · {label}" if series.payee else label
    if series.type == TX_INCOME:
        cat = ctx.store.income_categories.get(series.category)
        label = cat.name if cat else ""
        return f"{series.payee} · {label}" if series.payee else label
    names = {a.id: a.name for a in ctx.store.accounts.all()}
    return f"{names.get(series.from_account, '')} → {names.get(series.to_account, '')}"


def last_booked(ctx: Context) -> dict[str, date]:
    """The date of the latest transaction each series booked, by series id."""
    out: dict[str, date] = {}
    for tx in ctx.store.transactions.all():
        if tx.recurring_id and (tx.recurring_id not in out or tx.date > out[tx.recurring_id]):
            out[tx.recurring_id] = tx.date
    return out


# ------------------------------------------------------------------ due occurrences
def due(ctx: Context, today: date, series_id: str = "") -> list[Occurrence]:
    """Occurrences on or before `today` that are neither booked nor skipped, oldest first.

    A series that was not looked after for a while has one occurrence per missed date.
    """
    out = []
    for r in ctx.store.recurring.all():
        if series_id and r.id != series_id:
            continue
        end = r.end_date
        d, first = r.next_due, True
        while d <= today and (end is None or d <= end):
            out.append(Occurrence(r, d, first))
            d, first = next_after(r.start, r.recur_months, d), False
    return sorted(out, key=lambda o: (o.date, o.series.id))


def due_count(ctx: Context, today: date) -> int:
    return len(due(ctx, today))


def _handle(ctx: Context, series_id: str, on: date) -> Recurring:
    """The series, checked that `on` is the occurrence to handle next; advanced past it."""
    series = ctx.store.recurring.get(series_id)
    if series is None:
        raise ValidationError(t("Recurring transaction not found"))
    if on < series.next_due:
        raise ValidationError(t("This occurrence has already been booked or skipped"))
    if on > series.next_due:
        raise ValidationError(t("Book or skip the earlier occurrences of this series first"))
    if ended(series):
        raise ValidationError(t("This series has ended"))
    series.next_due = next_after(series.start, series.recur_months, on)
    return series


@recorded(N_("Book a recurring transaction"))
def book(
    ctx: Context, series_id: str, on: date, amount: float | None = None, booking_date: date | None = None
) -> Transaction:
    """Book the occurrence of `on` as a transaction, with another amount or date if given."""
    series = _handle(ctx, series_id, on)
    inp = _template(to_input(series))
    inp.date = booking_date or on
    inp.amount = series.amount if amount is None else amount
    inp.recurring_id = series.id
    tx = tx_svc.save(ctx, inp)
    ctx.store.recurring.update(series)
    return tx


@recorded(N_("Skip a recurring transaction"))
def skip(ctx: Context, series_id: str, on: date) -> Recurring:
    """Drop the occurrence of `on`: nothing is booked for it."""
    return ctx.store.recurring.update(_handle(ctx, series_id, on))


# changes made in the Due list before booking: (series id, occurrence) -> (amount, booking date)
Changes = dict[tuple[str, date], tuple[float | None, date | None]]


def _book_due(ctx: Context, occurrences: list[Occurrence], changes: Changes | None = None) -> Booking:
    result = Booking()
    failed: set[str] = set()
    for occ in occurrences:
        if occ.series.id in failed:  # its later occurrences wait for the one that failed
            continue
        amount, booking_date = (changes or {}).get((occ.series.id, occ.date), (None, None))
        try:
            result.booked.append(book(ctx, occ.series.id, occ.date, amount, booking_date))
        except (ValidationError, FxError) as exc:
            failed.add(occ.series.id)
            result.failed.append((occ, str(exc)))
    return result


@recorded(N_("Book recurring transactions"))
def book_all(ctx: Context, today: date, changes: Changes | None = None) -> Booking:
    """Book every due occurrence, at its series' amount and date unless `changes` has others.

    A series that can't be booked is left due, with its later occurrences.
    """
    return _book_due(ctx, due(ctx, today), changes)


@recorded(N_("Book recurring transactions automatically"))
def book_automatic(ctx: Context, today: date, series_id: str = "") -> Booking:
    """Book the due occurrences of every series that books automatically (or only of `series_id`).

    Called at startup and at each refresh; booking advances the series, so a second call books nothing.
    """
    result = _book_due(ctx, [o for o in due(ctx, today, series_id) if o.series.auto])
    for occ, reason in result.failed:
        log.warning("Recurring transaction %s on %s not booked: %s", occ.series.id, occ.date, reason)
    return result
