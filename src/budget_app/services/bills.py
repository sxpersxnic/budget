"""QR bills (F03, decision 15): a scanned bill waits in the Bills list with its due date until it is paid.

**Scanning** reads the QR codes of a PDF or photo (`qrcodes`, on the computer), keeps each one that is a valid
QR bill (`domain.qrbill`) and is not in the list yet, and stores it as a `Bill` with the file as its attachment
(in `attachments/<bill id>/`, see `services/attachments.py`). A bill counts as already in the list when its
IBAN, reference and amount match one there, whatever its status; a bill without a reference must also match
in its message and bill information, so two monthly bills of the same amount stay apart. The due date comes
from the bill information when it states one (Swico syntax: invoice date plus the net term), else the user
sets it.

**Paying** books an expenditure: payee the creditor (looked up like a bank text, so a known payee keeps its
spelling, and added when stored), the bill's amount and currency (the user's amount where the bill leaves it
open), the message and reference as note, and the category suggested for the payee (F07) unless one is
chosen. The bill is then *paid*, names the transaction, and its file belongs to the transaction as well.
`mark_paid` does all of this; the Bills page lets the user check the transaction sheet first and calls
`paid_with` inside the same action instead. Deleting the payment opens the bill again (`release`).

A bill can be dismissed (not to be paid here, for example paid by eBill) and reopened, and deleted. Every
write is one undoable action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from budget_app.domain import qrbill
from budget_app.domain.models import BILL_DISMISSED, BILL_OPEN, BILL_PAID, TX_EXPENDITURE, Bill, Transaction
from budget_app.domain.qrbill import QrBill, QrBillError
from budget_app.i18n import N_, t
from budget_app.services import attachments, categorize, qrcodes
from budget_app.services import payees as payee_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.services.undo import recorded

SOON = 7  # days: a bill due within a week (or overdue) is counted in the sidebar
NO_BILL = N_("No QR bill found in this file")
ALREADY = N_("This bill is already in the list")


@dataclass
class Scan:
    """What scanning a file found: the bills added, those already in the list, and codes that were refused."""

    added: list[Bill] = field(default_factory=list)
    duplicates: list[Bill] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # why, translated


# ------------------------------------------------------------------ scanning
def _amount(value: Decimal | None) -> float:
    return float(value) if value is not None else 0.0


def _key(iban: str, reference: str, amount: float, message: str, bill_info: str) -> tuple:
    # a bill without reference is told apart by its texts too: "Miete Mai" and "Miete Juni" are two bills
    return (iban, reference, round(amount, 2), "" if reference else message, "" if reference else bill_info)


def bill_key(bill: Bill) -> tuple:
    return _key(bill.iban, bill.reference, bill.amount, bill.message, bill.bill_info)


def from_payload(parsed: QrBill, today: date | None = None) -> Bill:
    """A new open bill for a parsed QR bill (not stored)."""
    due = qrbill.due_date(parsed.bill_info)
    creditor = parsed.creditor
    return Bill(
        creditor=creditor.name,
        iban=parsed.iban,
        currency=parsed.currency,
        amount=_amount(parsed.amount),
        creditor_address=", ".join([*creditor.lines(), creditor.country]),
        reference_type=parsed.reference_type,
        reference=parsed.reference,
        message=parsed.message,
        bill_info=parsed.bill_info,
        due=due.isoformat() if due else "",
        added=today or date.today(),
    )


def read(name: str, data: bytes) -> tuple[list[QrBill], list[str]]:
    """The valid QR bills in a file and the reasons the other QR-bill codes in it were refused. Writes nothing.

    Raises ValidationError when the file can't be read or holds no QR bill at all."""
    attachments.check(name, data)  # empty, too large, not what its extension says
    found: list[QrBill] = []
    refused: list[str] = []
    for text in qrcodes.texts(name, data):
        if not qrbill.is_payload(text):
            continue  # a QR code that isn't a bill (a link, a contact)
        try:
            parsed = qrbill.parse(text)
        except QrBillError as exc:
            refused.append(exc.translated())
            continue
        if parsed.notice:
            refused.append(t("This QR bill is only a notice and is not to be paid"))
            continue
        found.append(parsed)
    if not found and not refused:
        raise ValidationError(t(NO_BILL))
    return found, refused


@recorded(N_("Scan a QR bill"))
def scan(ctx: Context, name: str, data: bytes, today: date | None = None) -> Scan:
    """Add the QR bills of a PDF or photo to the list, each with the file as its attachment.

    Raises ValidationError when the file has no usable QR bill: none at all, only refused ones (the first
    reason is given), or only bills that are in the list already."""
    found, refused = read(name, data)
    known = {bill_key(b): b for b in ctx.store.bills.all()}
    result = Scan(refused=refused)
    for parsed in found:
        bill = from_payload(parsed, today)
        if (twin := known.get(bill_key(bill))) is not None:
            if twin not in result.duplicates:
                result.duplicates.append(twin)
            continue
        known[bill_key(bill)] = bill
        result.added.append(bill)
    if not result.added:
        if result.duplicates:
            raise ValidationError(t(ALREADY))
        raise ValidationError(t("The QR bill in this file can’t be used: {reason}", reason=refused[0]))
    for bill in result.added:  # everything is checked: now write, the file after its bill
        ctx.store.bills.add(bill)
        attachments.add_to_bill(ctx, bill.id, name, data)
    return result


# ------------------------------------------------------------------ lists
def get(ctx: Context, bill_id: str) -> Bill | None:
    return ctx.store.bills.get(bill_id) if bill_id else None


def _due_order(b: Bill) -> tuple:
    return (b.due_date is None, b.due_date or date.max, b.added, b.creditor.lower())


def open_bills(ctx: Context) -> list[Bill]:
    """The open bills, the earliest due first; bills without a due date last."""
    return sorted((b for b in ctx.store.bills.all() if b.status == BILL_OPEN), key=_due_order)


def closed_bills(ctx: Context) -> list[Bill]:
    """Paid and dismissed bills, the latest added first."""
    rows = [b for b in ctx.store.bills.all() if b.status != BILL_OPEN]
    return sorted(rows, key=lambda b: (b.added, b.id), reverse=True)


def due(ctx: Context, today: date, within: int = SOON) -> list[Bill]:
    """Open bills that are overdue or due within `within` days, the earliest first."""
    limit = today + timedelta(days=within)
    return [b for b in open_bills(ctx) if b.due_date is not None and b.due_date <= limit]


def due_count(ctx: Context, today: date) -> int:
    return len(due(ctx, today))


# ------------------------------------------------------------------ paying
def payee(ctx: Context, bill: Bill) -> str:
    """The payee for the creditor: a known payee by name or alias, else the creditor's name, cleaned like a
    bank text (the legal form stays, decision 11)."""
    return payee_svc.resolve(ctx, bill.creditor) or payee_svc.clean_name(bill.creditor)


def note(bill: Bill) -> str:
    """The transaction's note: the message (or the invoice number) and the reference."""
    parts = [bill.message] if bill.message else []
    info = qrbill.swico(bill.bill_info)
    if not bill.message and info is not None and info.invoice_number:
        parts.append(t("Invoice {number}", number=info.invoice_number))
    if bill.reference:
        parts.append(t("Ref. {reference}", reference=qrbill.format_reference(bill.reference_type, bill.reference)))
    return " · ".join(parts)


def payment_input(ctx: Context, bill: Bill, on: date | None = None) -> TransactionInput:
    """The expenditure that pays the bill, as the transaction form starts with it (no category: the form
    suggests one, F07)."""
    return TransactionInput(
        date=on or date.today(),
        type=TX_EXPENDITURE,
        amount=bill.amount or None,
        currency=bill.currency,
        payee=payee(ctx, bill),
        comment=note(bill),
    )


def suggestion(ctx: Context, bill: Bill, account: str | None = None) -> categorize.Suggestion | None:
    """The category (and, without an account, the account) suggested for paying the bill (F07)."""
    return categorize.suggest(ctx, TX_EXPENDITURE, payee(ctx, bill), bill.message, account=account)


def _open(ctx: Context, bill_id: str) -> Bill:
    bill = get(ctx, bill_id)
    if bill is None:
        raise ValidationError(t("This bill is no longer in the list"))
    if bill.status != BILL_OPEN:
        raise ValidationError(t("This bill is no longer open"))
    return bill


def _settle(ctx: Context, bill: Bill, tx: Transaction) -> None:
    bill.status, bill.transaction_id = BILL_PAID, tx.id
    ctx.store.bills.update(bill)
    moved = []
    for att in attachments.list_for_bill(ctx, bill.id):
        copy = ctx.store.attachments.get(att.id)  # the list holds the live records
        if copy is not None and not copy.transaction_id:
            copy.transaction_id = tx.id
            moved.append(copy)
    ctx.store.attachments.update_many(moved)


@recorded(N_("Pay a bill"))
def mark_paid(
    ctx: Context, bill_id: str, on: date, account: str, category: str = "", amount: float | None = None
) -> Transaction:
    """Book the expenditure that pays an open bill and mark the bill paid; its file goes with the transaction.

    `amount` is needed where the bill leaves it open, and replaces the bill's otherwise; without a
    `category` the suggested one (F07) is used."""
    bill = _open(ctx, bill_id)
    inp = payment_input(ctx, bill, on)
    inp.from_account = account
    if amount is not None:
        inp.amount = amount
    hit = None if category else suggestion(ctx, bill, account)
    inp.category = category or (hit.category if hit else "")
    tx = tx_svc.save(ctx, inp)
    _settle(ctx, bill, tx)
    return tx


def check_payment(ctx: Context, bill_id: str, type_: str) -> Bill:
    """The open bill a transaction of `type_` is about to pay; raises ValidationError before anything is saved."""
    bill = _open(ctx, bill_id)
    if type_ != TX_EXPENDITURE:
        raise ValidationError(t("A bill is paid with an expenditure"))
    return bill


@recorded(N_("Pay a bill"))
def paid_with(ctx: Context, bill_id: str, tx: Transaction) -> Bill:
    """Mark an open bill paid by an expenditure just saved (the transaction sheet's way, inside its action;
    the sheet calls `check_payment` before it saves)."""
    bill = check_payment(ctx, bill_id, tx.type)
    _settle(ctx, bill, tx)
    return bill


def release(ctx: Context, tx_ids: set[str]) -> int:
    """Open the bills whose payment is deleted (inside the deletion's action); their files stay with them."""
    released = []
    for bill in ctx.store.bills.all():
        if bill.status == BILL_PAID and bill.transaction_id in tx_ids:
            back = ctx.store.bills.get(bill.id)
            if back is not None:
                back.status, back.transaction_id = BILL_OPEN, ""
                released.append(back)
    ctx.store.bills.update_many(released)
    return len(released)


# ------------------------------------------------------------------ the rest
@recorded(N_("Dismiss a bill"))
def dismiss(ctx: Context, bill_id: str) -> Bill:
    """Take an open bill off the open list without booking anything (paid elsewhere, or not owed)."""
    bill = _open(ctx, bill_id)
    bill.status = BILL_DISMISSED
    return ctx.store.bills.update(bill)


@recorded(N_("Reopen a bill"))
def reopen(ctx: Context, bill_id: str) -> Bill:
    """Put a dismissed bill back on the open list. A paid bill opens again when its payment is deleted."""
    bill = get(ctx, bill_id)
    if bill is None:
        raise ValidationError(t("This bill is no longer in the list"))
    if bill.status == BILL_PAID:
        raise ValidationError(t("This bill is paid. Delete its payment to open it again."))
    bill.status = BILL_OPEN
    return ctx.store.bills.update(bill)


@recorded(N_("Change a due date"))
def set_due(ctx: Context, bill_id: str, due_on: date | None) -> Bill:
    bill = get(ctx, bill_id)
    if bill is None:
        raise ValidationError(t("This bill is no longer in the list"))
    bill.due = due_on.isoformat() if due_on else ""
    return ctx.store.bills.update(bill)


@recorded(N_("Delete bills"))
def delete(ctx: Context, bill_ids: list[str]) -> int:
    """Delete bills and their files; a paid bill's payment stays, with its file."""
    ids = {i for i in bill_ids if ctx.store.bills.get(i) is not None}
    attachments.remove_for_bills(ctx, ids)
    return ctx.store.bills.delete(list(ids))


def payment(ctx: Context, bill: Bill) -> Transaction | None:
    """The transaction that paid the bill, if it still exists."""
    return ctx.store.transactions.get(bill.transaction_id) if bill.transaction_id else None
