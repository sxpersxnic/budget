"""Month-end balances from bank files (F36): the running balance of a CSV export, or a camt closing balance.

A CSV export's balance column holds the account's balance after each booking. `from_rows` finds the order
of the lines (newest or oldest first) as the reading in which the dates run forward and every balance
equals the one before plus the amounts in between; the balance after a month's last booking is that
month's end balance. A month is complete when the file continues past it, or when its last booking is on
the month's last day; otherwise the file ends inside it and its balance is offered unticked (decision 6).
When the balances do not chain in either order, nothing is offered and `BalanceOffer.problem` says why.

A camt statement brings its closing booked balance (CLBD), ticked when it is dated on a month's last day.

`compare` looks up what is recorded for the account; `save` stores the ticked balances with
`wealth.upsert_entry` (at the month-end rate, replacing what was recorded for that month).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise

from budget_app.domain import months
from budget_app.domain.models import AccountEntry, Transaction
from budget_app.i18n import N_, t
from budget_app.services import camt, importer, wealth
from budget_app.services.camt import Statement
from budget_app.services.context import Context
from budget_app.services.importer import ImportRow
from budget_app.services.undo import recorded


@dataclass
class BalanceCandidate:
    """A balance the file shows for the end of one month."""

    month: str  # 'YYYY-MM'
    amount: float  # in the account's currency
    date: date  # the last booking on or before the month's end (CSV), or the closing balance's date (camt)
    complete: bool  # a true month-end balance; False when the file ends inside the month
    include: bool = True
    recorded: float | None = None  # the balance recorded for the account and month, if any

    @property
    def unchanged(self) -> bool:
        """The same balance is recorded already."""
        return self.recorded is not None and abs(self.recorded - self.amount) < 0.005

    @property
    def replaces(self) -> bool:
        """Saving it would replace a different recorded balance."""
        return self.recorded is not None and not self.unchanged


@dataclass
class BalanceOffer:
    candidates: list[BalanceCandidate] = field(default_factory=list)
    problem: str = ""  # why the file's balances are not offered ('' when they are, or when there are none)


def _cents(value: float) -> int:
    return round(value * 100)


def from_rows(rows: list[ImportRow]) -> BalanceOffer:
    """Month-end balances from the rows' running balance (rows without a date or amount are left out)."""
    usable = [r for r in rows if r.date is not None and r.amount is not None]
    if not any(r.balance is not None for r in usable):
        return BalanceOffer()
    readings = [_month_ends(chrono) for chrono in (usable, usable[::-1]) if _in_order(chrono) and _chains(chrono)]
    if not readings:
        return BalanceOffer(
            problem=t(
                "The balances in the file do not follow from its amounts, so no month-end balances are offered. "
                "Check the balance column."
            )
        )
    if len(readings) == 2 and _key(readings[0]) != _key(readings[1]):
        return BalanceOffer(
            problem=t(
                "The order of the lines can’t be told from the file (newest or oldest first), "
                "so no month-end balances are offered."
            )
        )
    return BalanceOffer(readings[0])


def _in_order(chrono: list[ImportRow]) -> bool:
    return all(a.date <= b.date for a, b in pairwise(chrono))  # type: ignore[operator]


def _chains(chrono: list[ImportRow]) -> bool:
    """Each balance equals the previous balance plus the amounts booked after it."""
    previous: int | None = None
    moved = 0
    for row in chrono:
        if previous is not None:
            moved += _cents(row.amount or 0.0)
        if row.balance is None:
            continue
        if previous is not None and previous + moved != _cents(row.balance):
            return False
        previous, moved = _cents(row.balance), 0
    return True


def _month_ends(chrono: list[ImportRow]) -> list[BalanceCandidate]:
    """The balance after each row (from the first row with a balance), then the last one of every month."""
    anchor = next(i for i, r in enumerate(chrono) if r.balance is not None)
    after = [0] * len(chrono)
    after[anchor] = _cents(chrono[anchor].balance or 0.0)
    for i in range(anchor - 1, -1, -1):
        after[i] = after[i + 1] - _cents(chrono[i + 1].amount or 0.0)
    for i in range(anchor + 1, len(chrono)):
        after[i] = after[i - 1] + _cents(chrono[i].amount or 0.0)
    last: dict[str, int] = {}  # month -> index of its last booking
    for i, row in enumerate(chrono):
        last[months.month_of(row.date)] = i  # type: ignore[arg-type]
    first_month, last_month = min(last), max(last)
    out = []
    index = last[first_month]
    for month in months.month_range(first_month, last_month):
        index = last.get(month, index)  # a month without bookings ends with the balance before it
        booked: date = chrono[index].date  # type: ignore[assignment]
        complete = month < last_month or booked == months.last_day(month)
        out.append(BalanceCandidate(month, after[index] / 100, booked, complete, include=complete))
    return out


def _key(candidates: list[BalanceCandidate]) -> list[tuple[str, float, bool]]:
    return [(c.month, c.amount, c.complete) for c in candidates]


def from_statement(statement: Statement) -> BalanceOffer:
    """The statement's closing booked balance, at its date."""
    balance = statement.closing_balance
    if balance is None or (balance.currency and statement.currency and balance.currency != statement.currency):
        return BalanceOffer()
    day = balance.date or statement.end
    if day is None:
        return BalanceOffer()
    month = months.month_of(day)
    complete = day == months.last_day(month)
    return BalanceOffer([BalanceCandidate(month, round(balance.amount, 2), day, complete, include=complete)])


def compare(ctx: Context, candidates: list[BalanceCandidate], account_id: str) -> list[BalanceCandidate]:
    """Note the balance recorded for each month; a balance that is recorded already is left unticked."""
    for c in candidates:
        entry = wealth.entry_for(ctx, account_id, c.month) if account_id else None
        c.recorded = entry.amount if entry else None
        if c.unchanged:
            c.include = False
    return candidates


@recorded(N_("Record balances"))
def save(ctx: Context, account_id: str, candidates: list[BalanceCandidate]) -> list[AccountEntry]:
    """Record every ticked balance as the account's balance at the end of its month."""
    return [wealth.upsert_entry(ctx, account_id, c.month, c.amount) for c in candidates if c.include]


@recorded(N_("Import transactions"))
def import_file(
    ctx: Context,
    rows: list[ImportRow],
    account_id: str,
    candidates: list[BalanceCandidate],
    statement: Statement | None = None,
) -> tuple[list[Transaction], list[AccountEntry]]:
    """Save the chosen rows, then the ticked month-end balances, as one undoable step: undoing the import takes
    back both. A camt statement also remembers its account for the IBAN (`camt.commit`)."""
    saved = camt.commit(ctx, statement, rows, account_id) if statement else importer.commit(ctx, rows, account_id)
    return saved, save(ctx, account_id, candidates)
