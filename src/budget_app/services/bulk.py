"""Bulk edit (F13): change the category, payee, account or note of several transactions in one step.

Every changed row goes through the same checks as an edit in the transaction form
(`transactions.prepare_edit`), so accounts stay in the transaction's currency, advance payments and
repayments stay valid, and an exchange keeps its spread booking in step. The edit is all or nothing:
every row is checked first, and if one fails nothing is saved and the failures name the rows.

A field leaves the rows it does not apply to as they are: an expenditure category applies to
expenditure, an income category to income other than repayments, a payee to expenditure and income
(F09), an account to the From account of expenditure and the To account of income. A spread booking
(a revaluation linked to its exchange, F44) follows its exchange and is never changed on its own.
Booked rows keep their recurring series.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from budget_app import i18n
from budget_app.domain.fx import FxError
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, TX_REVALUATION, Transaction
from budget_app.i18n import N_, tn
from budget_app.services import payees as payee_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.services.undo import recorded

NOTE_KEEP, NOTE_REPLACE, NOTE_APPEND, NOTE_CLEAR = "keep", "replace", "append", "clear"


@dataclass
class BulkChanges:
    """What to change in every selected row; None (and NOTE_KEEP) leaves a field as it is."""

    expenditure_category: str | None = None  # a COICOP code or TAX
    income_category: str | None = None  # an income category id
    payee: str | None = None  # a payee name; a new one becomes a payee when saved
    account: str | None = None  # expenditure: the From account; income: the To account
    note_mode: str = NOTE_KEEP
    note: str = ""  # the text for NOTE_REPLACE and NOTE_APPEND


# ------------------------------------------------------------------ which field applies to which row
def is_spread(tx: Transaction) -> bool:
    """A spread booking: the revaluation an exchange keeps in step with itself (F44)."""
    return tx.type == TX_REVALUATION and bool(tx.linked_id)


def is_invested(tx: Transaction) -> bool:
    """Booked by a trade or price on the Investments page (F21): it changes only with its trade or price."""
    return bool(tx.invest_id)


def takes_expenditure_category(tx: Transaction) -> bool:
    return tx.type == TX_EXPENDITURE and not is_invested(tx)


def takes_income_category(tx: Transaction) -> bool:
    return tx.type == TX_INCOME and not tx.is_advance and not is_invested(tx)  # a repayment has no category


def takes_payee(tx: Transaction) -> bool:
    return tx.type in payee_svc.PAYEE_TYPES and not is_invested(tx)


def takes_account(tx: Transaction) -> bool:
    return tx.type in (TX_EXPENDITURE, TX_INCOME) and not is_invested(tx)


def takes_note(tx: Transaction) -> bool:
    return not is_spread(tx) and not is_invested(tx)


@dataclass
class Selection:
    """The selected transactions, and which of them each field applies to."""

    rows: list[Transaction] = field(default_factory=list)

    def taking(self, applies) -> list[Transaction]:
        return [tx for tx in self.rows if applies(tx)]

    @property
    def account_currencies(self) -> list[str]:
        return sorted({tx.currency for tx in self.taking(takes_account)})


def selection(ctx: Context, ids: list[str]) -> Selection:
    """The stored transactions of `ids`, each once; ids deleted in the meantime are left out."""
    rows = [ctx.store.transactions.get(i) for i in dict.fromkeys(ids)]
    return Selection([tx for tx in rows if tx is not None])


# ------------------------------------------------------------------ planning and saving
def _note(current: str, changes: BulkChanges) -> str:
    text = changes.note.strip()
    if changes.note_mode == NOTE_REPLACE:
        return text
    if changes.note_mode == NOTE_APPEND:
        return f"{current.strip()} {text}".strip() if text else current
    if changes.note_mode == NOTE_CLEAR:
        return ""
    return current


def apply(tx: Transaction, inp: TransactionInput, changes: BulkChanges) -> TransactionInput:
    """The form input of `tx` with the changes that apply to it."""
    out = replace(inp)
    if changes.expenditure_category is not None and takes_expenditure_category(tx):
        out.category = changes.expenditure_category
    if changes.income_category is not None and takes_income_category(tx):
        out.category = changes.income_category
    if changes.payee is not None and takes_payee(tx):
        out.payee = changes.payee
    if changes.account is not None and takes_account(tx):
        if tx.type == TX_EXPENDITURE:
            out.from_account = changes.account
        else:
            out.to_account = changes.account
    if takes_note(tx):
        out.comment = _note(inp.comment, changes)
    return out


def row_label(tx: Transaction) -> str:
    """A row in messages: its date and amount, the way the list shows them."""
    amount = f"{tx.amount:,.2f}".replace(",", "'")
    return f"{i18n.date_label(tx.date)} · {amount} {tx.currency}"


@dataclass
class Plan:
    """What a bulk edit would do: the rows it changes, as (new record, stored record), and the rows it can't."""

    edits: list[tuple[Transaction, Transaction]] = field(default_factory=list)
    failures: list[tuple[Transaction, str]] = field(default_factory=list)


def plan(ctx: Context, ids: list[str], changes: BulkChanges) -> Plan:
    """Check every row against the changes without saving anything; rows the changes leave as they are drop out."""
    out = Plan()
    for tx in selection(ctx, ids).rows:
        if is_spread(tx) or is_invested(tx):
            continue
        current = tx_svc.edit_input(ctx, tx)
        edited = apply(tx, current, changes)
        if edited == current:
            continue
        try:
            new, old = tx_svc.prepare_edit(ctx, edited, tx.id)
        except (ValidationError, FxError) as exc:
            out.failures.append((tx, str(exc)))
            continue
        if new != old:  # "migros" for the payee Migros changes nothing
            out.edits.append((new, old))
    return out


class BulkEditError(ValidationError):
    """Some rows can't take the changes; nothing was saved."""

    def __init__(self, failures: list[tuple[Transaction, str]]):
        self.failures = failures
        n = len(failures)
        lines = [
            tn(
                "{n} transaction can’t be changed, so nothing is saved:",
                "{n} transactions can’t be changed, so nothing is saved:",
                n,
            )
        ]
        lines += [f"{row_label(tx)}: {reason}" for tx, reason in failures]
        super().__init__("\n".join(lines))


@recorded(N_("Edit several transactions"))
def bulk_edit(ctx: Context, ids: list[str], changes: BulkChanges) -> list[Transaction]:
    """Apply `changes` to the transactions `ids`, all or none, as one step to undo; returns the changed ones."""
    planned = plan(ctx, ids, changes)
    if planned.failures:
        raise BulkEditError(planned.failures)
    return [tx_svc.store_edit(ctx, new, old) for new, old in planned.edits]
