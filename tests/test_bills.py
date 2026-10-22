"""QR bills (F03): reading the codes of PDFs and photos, the Bills list, paying, and the bill's file."""

import io
import json
import zipfile
from datetime import date, timedelta

import pytest
import qr_fixtures as fx
from PIL import Image

from budget_app.domain.models import BILL_DISMISSED, BILL_OPEN, BILL_PAID, Attachment
from budget_app.services import archive, bills, qrcodes
from budget_app.services import attachments as att_svc
from budget_app.services import context as context_module
from budget_app.services import export as export_svc
from budget_app.services import payees as payee_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.storage.history import History
from budget_app.ui.widgets import attachments as att_ui

QRR_BILL = fx.payload()  # Robert Schneider AG, 1'949.75 CHF, QR reference
SWICO = "//S1/10/10201409/11/261001/20/1400.000-53/40/2:10;0:30"


def scr(amount: str = "199.95", message: str = "", bill_info: str = "") -> str:
    return fx.payload(
        iban="CH5800791123000889012",
        amount=amount,
        reference_type="SCOR",
        reference="RF18539007547034",
        message=message,
        bill_info=bill_info,
    )


def non(amount: str = "1450.00", message: str = "Miete Mai") -> str:
    return fx.payload(
        iban="CH9300762011623852957",
        creditor="Immo Muster AG",
        amount=amount,
        reference_type="NON",
        reference="",
        message=message,
    )


def bill_file(ctx, att: Attachment):
    return ctx.store.data_dir / "attachments" / att.folder / att.stored


def latest_undo(ctx) -> None:
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)


# ------------------------------------------------------------------ reading the codes of a file
def test_codes_in_a_vector_pdf_a_picture_pdf_and_photos():
    text = fx.payload(message="Grüsse aus Zürich – Höhe 1'000 m")
    assert qrcodes.texts("bill.pdf", fx.pdf(text)) == [text]
    assert qrcodes.texts("bill.pdf", fx.pdf(text, vector=False)) == [text]
    assert qrcodes.texts("scan.png", fx.png(text)) == [text]
    assert qrcodes.texts("IMG_0042.JPG", fx.jpeg(text, rotate=True)) == [text]
    buf = io.BytesIO()
    fx.code_image(text).save(buf, format="WEBP", lossless=True)
    assert qrcodes.texts("photo.webp", buf.getvalue()) == [text]


def test_every_page_is_read_and_each_code_once():
    first, second = scr(), non()
    data = fx.pdf(first, second, first, pages_before=2)
    assert qrcodes.texts("bills.pdf", data) == [first, second]


def test_a_pdf_without_codes_is_read_twice(monkeypatch):
    scales = []
    real = qrcodes._pdf_pages

    def pages(name, data, scale):
        scales.append(scale)
        return real(name, data, scale)

    monkeypatch.setattr(qrcodes, "_pdf_pages", pages)
    assert qrcodes.texts("letter.pdf", fx.pdf(pages_before=1)) == []
    assert scales == list(qrcodes.SCALES)


def test_long_documents_are_read_up_to_the_page_limit(monkeypatch):
    monkeypatch.setattr(qrcodes, "MAX_PAGES", 2)
    assert qrcodes.texts("long.pdf", fx.pdf(QRR_BILL, pages_before=2)) == []


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16, "is a HEIC photo"),
        ("contract.docx", b"PK\x03\x04" + b"\x00" * 26, "is not a PDF or a photo"),
        ("notes.txt", b"hello", "is not a PDF or a photo"),
        ("bill.pdf", b"%PDF-1.4\nnot really\n", "can’t be read as a PDF"),
        ("scan.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "can’t be read as a photo"),
    ],
)
def test_files_that_cant_be_read(name: str, data: bytes, message: str):
    with pytest.raises(ValidationError, match=message):
        qrcodes.texts(name, data)


def test_a_code_that_is_not_utf8_falls_back_to_its_text():
    class Code:
        bytes = b"\xff\xfe"
        text = "SPC?"

    class FakeZxing:
        class BarcodeFormat:
            QRCode = 1

        @staticmethod
        def BarcodeFormats(f):
            return f

        @staticmethod
        def read_barcodes(image, formats):
            return [Code()]

    import sys  # noqa: PLC0415

    real = sys.modules.get("zxingcpp")
    sys.modules["zxingcpp"] = FakeZxing  # type: ignore[assignment]
    try:
        assert qrcodes._decode(Image.new("L", (10, 10))) == ["SPC?"]
    finally:
        sys.modules["zxingcpp"] = real  # type: ignore[assignment]


# ------------------------------------------------------------------ scanning into the list
def test_scan_adds_an_open_bill_with_its_file(ctx):
    data = fx.pdf(fx.payload(bill_info=SWICO))
    result = bills.scan(ctx, "Rechnung Schneider.pdf", data, today=date(2026, 10, 18))
    [bill] = result.added
    assert result.duplicates == [] and result.refused == []
    assert (bill.creditor, bill.iban, bill.amount, bill.currency) == (
        "Robert Schneider AG",
        "CH4431999123000889012",
        1949.75,
        "CHF",
    )
    assert bill.creditor_address == "Rue du Lac 1268, 2501 Biel, CH"
    assert (bill.reference_type, bill.reference, bill.message) == (
        "QRR",
        "210000000003139471430009017",
        "Auftrag vom 15.06.2020",
    )
    assert bill.due == "2026-10-31" and bill.due_date == date(2026, 10, 31)  # 1 Oct + 30 days net
    assert (bill.status, bill.transaction_id, bill.added) == (BILL_OPEN, "", date(2026, 10, 18))
    [att] = att_svc.list_for_bill(ctx, bill.id)
    assert (att.transaction_id, att.bill_id, att.name, att.folder) == ("", bill.id, "Rechnung Schneider.pdf", bill.id)
    assert bill_file(ctx, att).read_bytes() == data and att_svc.read(ctx, att) == data
    assert att_svc.counts(ctx) == {} and att_svc.names_by_transaction(ctx) == {}  # no transaction has it yet

    # one undo step takes back the bill and its file record
    assert undo_svc.latest(ctx).label == "Scan a QR bill"
    latest_undo(ctx)
    assert ctx.store.bills.all() == [] and ctx.store.attachments.all() == []


def test_the_same_bill_is_refused_whatever_its_status(ctx):
    data = fx.png(scr())
    [bill] = bills.scan(ctx, "a.png", data).added
    with pytest.raises(ValidationError, match="This bill is already in the list"):
        bills.scan(ctx, "again.pdf", fx.pdf(scr()))
    bills.dismiss(ctx, bill.id)
    with pytest.raises(ValidationError, match="already in the list"):
        bills.scan(ctx, "a.png", data)
    # another amount is another bill
    assert len(bills.scan(ctx, "b.png", fx.png(scr(amount="20.00"))).added) == 1


def test_bills_without_reference_are_told_apart_by_their_message(ctx):
    bills.scan(ctx, "mai.pdf", fx.pdf(non(message="Miete Mai")))
    assert len(bills.scan(ctx, "juni.pdf", fx.pdf(non(message="Miete Juni"))).added) == 1
    with pytest.raises(ValidationError, match="already in the list"):
        bills.scan(ctx, "juni-2.pdf", fx.pdf(non(message="Miete Juni")))


def test_a_file_with_several_bills(ctx):
    bills.scan(ctx, "one.png", fx.png(scr()))
    result = bills.scan(ctx, "all.pdf", fx.pdf(scr(), non(), QRR_BILL))
    assert [b.creditor for b in result.added] == ["Immo Muster AG", "Robert Schneider AG"]
    assert [b.reference_type for b in result.duplicates] == ["SCOR"]
    for bill in result.added:  # each bill keeps its own copy of the file
        [att] = att_svc.list_for_bill(ctx, bill.id)
        assert att.name == "all.pdf" and bill_file(ctx, att).is_file()


def test_no_bill_in_the_file(ctx):
    with pytest.raises(ValidationError, match="No QR bill found in this file"):
        bills.scan(ctx, "letter.pdf", fx.pdf(pages_before=1))
    with pytest.raises(ValidationError, match="No QR bill found in this file"):
        bills.scan(ctx, "link.png", fx.png("https://example.com/invoice/17"))
    with pytest.raises(ValidationError, match="is empty"):
        bills.scan(ctx, "empty.pdf", b"")
    assert ctx.store.bills.all() == [] and ctx.store.attachments.all() == []


def test_refused_codes_are_explained(ctx):
    bad = fx.payload(iban="CH4431999123000889013")
    with pytest.raises(ValidationError) as info:
        bills.scan(ctx, "bad.png", fx.png(bad))
    assert str(info.value) == (
        "The QR bill in this file can’t be used: The creditor’s IBAN CH4431999123000889013 is invalid"
    )
    with pytest.raises(ValidationError, match="only a notice and is not to be paid"):
        bills.scan(ctx, "notice.png", fx.png(scr(amount="0.00")))
    # a good bill next to a bad one is added, the bad one reported
    result = bills.scan(ctx, "both.pdf", fx.pdf(bad, scr()))
    assert len(result.added) == 1 and result.refused == ["The creditor’s IBAN CH4431999123000889013 is invalid"]


def test_bill_without_amount_and_without_due_date(ctx):
    [bill] = bills.scan(ctx, "spende.png", fx.png(scr(amount=""))).added
    assert bill.amount == 0 and bill.due == "" and bill.due_date is None
    assert bills.payment_input(ctx, bill).amount is None


# ------------------------------------------------------------------ the lists
def test_open_bills_by_due_date_and_what_needs_attention(ctx):
    today = date(2026, 10, 18)
    late, soon, later, undated = (
        bills.scan(ctx, f"{k}.png", fx.png(scr(amount=f"{k}.00"))).added[0] for k in (10, 20, 30, 40)
    )
    bills.set_due(ctx, late.id, today - timedelta(days=3))
    bills.set_due(ctx, soon.id, today + timedelta(days=7))
    bills.set_due(ctx, later.id, today + timedelta(days=8))
    assert [b.id for b in bills.open_bills(ctx)] == [late.id, soon.id, later.id, undated.id]
    assert [b.id for b in bills.due(ctx, today)] == [late.id, soon.id]
    assert bills.due_count(ctx, today) == 2
    bills.dismiss(ctx, late.id)
    assert bills.due_count(ctx, today) == 1
    assert [b.id for b in bills.closed_bills(ctx)] == [late.id]
    bills.set_due(ctx, soon.id, None)
    assert bills.get(ctx, soon.id).due == ""
    assert undo_svc.latest(ctx).label == "Change a due date"
    with pytest.raises(ValidationError, match="no longer in the list"):
        bills.set_due(ctx, "nope", None)
    assert bills.get(ctx, "") is None


# ------------------------------------------------------------------ paying
def history_for(ctx, setup, payee: str, category: str = "04.4.1", times: int = 2) -> None:
    for k in range(times):
        tx_svc.save(
            ctx,
            TransactionInput(
                date=date(2026, 9, 1 + k),
                type="expenditure",
                amount=80,
                currency="CHF",
                category=category,
                from_account=setup["chf"].id,
                payee=payee,
            ),
        )


def test_mark_paid_books_the_expenditure_and_moves_the_file(ctx, setup):
    history_for(ctx, setup, "Robert Schneider AG")
    data = fx.pdf(QRR_BILL)
    [bill] = bills.scan(ctx, "schneider.pdf", data).added
    hit = bills.suggestion(ctx, bill)
    assert hit is not None and hit.category == "04.4.1" and hit.account == setup["chf"].id
    assert hit.reason == "Used 2 times for this payee"

    tx = bills.mark_paid(ctx, bill.id, date(2026, 10, 20), setup["chf"].id)
    assert (tx.type, tx.date, tx.amount, tx.currency, tx.category, tx.from_account) == (
        "expenditure",
        date(2026, 10, 20),
        1949.75,
        "CHF",
        "04.4.1",
        setup["chf"].id,
    )
    assert tx.payee == "Robert Schneider AG"
    assert tx.comment == "Auftrag vom 15.06.2020 · Ref. 21 00000 00003 13947 14300 09017"
    paid = bills.get(ctx, bill.id)
    assert (paid.status, paid.transaction_id) == (BILL_PAID, tx.id) and bills.payment(ctx, paid) == tx
    [att] = att_svc.list_for(ctx, tx.id)
    assert att.bill_id == bill.id and att_svc.list_for_bill(ctx, bill.id) == [att]
    assert bill_file(ctx, att) == ctx.store.data_dir / "attachments" / bill.id / att.stored  # never moved
    assert att_svc.read(ctx, att) == data and att_svc.counts(ctx)[tx.id] == 1
    assert att_svc.names_by_transaction(ctx)[tx.id] == "schneider.pdf"
    with pytest.raises(ValidationError, match="no longer open"):
        bills.mark_paid(ctx, bill.id, date(2026, 10, 20), setup["chf"].id)

    # one undo step: the transaction goes, the bill is open, the file is the bill's again
    assert undo_svc.latest(ctx).label == "Pay a bill"
    latest_undo(ctx)
    assert ctx.store.transactions.get(tx.id) is None
    back = bills.get(ctx, bill.id)
    assert (back.status, back.transaction_id) == (BILL_OPEN, "")
    assert att_svc.list_for_bill(ctx, bill.id)[0].transaction_id == ""


def test_mark_paid_with_the_users_category_and_amount(ctx, setup):
    [bill] = bills.scan(ctx, "spende.png", fx.png(scr(amount=""))).added
    with pytest.raises(ValidationError, match="Amount must be a positive number"):
        bills.mark_paid(ctx, bill.id, date(2026, 10, 20), setup["chf"].id, category="09.4")
    with pytest.raises(ValidationError, match="Select an expenditure category"):
        bills.mark_paid(ctx, bill.id, date(2026, 10, 20), setup["chf"].id, amount=50)  # nothing to suggest
    tx = bills.mark_paid(ctx, bill.id, date(2026, 10, 20), setup["chf"].id, category="09.4", amount=50)
    assert (tx.amount, tx.category) == (50, "09.4")
    assert tx.comment == "Ref. RF18 5390 0754 7034"
    with pytest.raises(ValidationError, match="no longer in the list"):
        bills.mark_paid(ctx, "unknown", date(2026, 10, 20), setup["chf"].id)


def test_the_creditor_becomes_a_known_payee(ctx, setup):
    payee_svc.ensure(ctx, "Immo Muster AG")
    payee_svc.rename(ctx, payee_svc.find(ctx, "Immo Muster AG").id, "Hausverwaltung Muster")
    [bill] = bills.scan(ctx, "miete.png", fx.png(non())).added
    assert bills.payee(ctx, bill) == "Hausverwaltung Muster"  # the former name leads to the payee kept
    tx = bills.mark_paid(ctx, bill.id, date(2026, 10, 1), setup["chf"].id, category="04.1")
    assert tx.payee == "Hausverwaltung Muster"
    [new] = bills.scan(ctx, "neu.png", fx.png(fx.payload(creditor="Neue Gärtnerei GmbH"))).added
    tx = bills.mark_paid(ctx, new.id, date(2026, 10, 1), setup["chf"].id, category="05.6")
    assert tx.payee == "Neue Gärtnerei GmbH" and payee_svc.find(ctx, "Neue Gärtnerei GmbH") is not None


def test_note_with_the_invoice_number_when_there_is_no_message(ctx):
    [bill] = bills.scan(ctx, "x.png", fx.png(scr(bill_info="//S1/10/R-2026-117"))).added
    assert bills.note(bill) == "Invoice R-2026-117 · Ref. RF18 5390 0754 7034"
    [plain] = bills.scan(ctx, "y.png", fx.png(non(message=""))).added
    assert bills.note(plain) == ""


def test_paid_with_a_transaction_from_the_sheet(ctx, setup):
    [bill] = bills.scan(ctx, "a.png", fx.png(scr())).added
    income = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 1),
            type="income",
            amount=199.95,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    with pytest.raises(ValidationError, match="A bill is paid with an expenditure"):
        bills.paid_with(ctx, bill.id, income)
    inp = bills.payment_input(ctx, bill, date(2026, 10, 2))
    inp.category, inp.from_account = "12.1", setup["chf"].id
    tx = tx_svc.save(ctx, inp)
    assert bills.paid_with(ctx, bill.id, tx).status == BILL_PAID
    assert [a.bill_id for a in att_svc.list_for(ctx, tx.id)] == [bill.id]


def test_deleting_the_payment_opens_the_bill_again(ctx, setup):
    [bill] = bills.scan(ctx, "a.pdf", fx.pdf(scr())).added
    tx = bills.mark_paid(ctx, bill.id, date(2026, 10, 2), setup["chf"].id, category="12.1")
    other = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 2), type="expenditure", amount=5, currency="CHF", category="01.1",
            from_account=setup["chf"].id,
        ),
    )  # fmt: skip
    receipt = att_svc.add(ctx, other.id, "receipt.png", fx.png("x"))
    tx_svc.delete(ctx, [tx.id, other.id])
    back = bills.get(ctx, bill.id)
    assert (back.status, back.transaction_id) == (BILL_OPEN, "")
    [att] = att_svc.list_for_bill(ctx, bill.id)
    assert att.transaction_id == "" and bill_file(ctx, att).is_file()
    assert att_svc.get(ctx, receipt.id) is None  # an ordinary attachment goes with its transaction
    latest_undo(ctx)
    assert bills.get(ctx, bill.id).status == BILL_PAID and [a.id for a in att_svc.list_for(ctx, tx.id)] == [att.id]


def test_dismiss_reopen_and_delete(ctx, setup):
    [bill] = bills.scan(ctx, "a.pdf", fx.pdf(scr())).added
    assert bills.dismiss(ctx, bill.id).status == BILL_DISMISSED
    with pytest.raises(ValidationError, match="no longer open"):
        bills.dismiss(ctx, bill.id)
    assert bills.reopen(ctx, bill.id).status == BILL_OPEN
    tx = bills.mark_paid(ctx, bill.id, date(2026, 10, 2), setup["chf"].id, category="12.1")
    with pytest.raises(ValidationError, match="Delete its payment to open it again"):
        bills.reopen(ctx, bill.id)
    with pytest.raises(ValidationError, match="no longer in the list"):
        bills.reopen(ctx, "unknown")

    [open_one] = bills.scan(ctx, "b.png", fx.png(non())).added
    [open_att] = att_svc.list_for_bill(ctx, open_one.id)
    assert bills.delete(ctx, [bill.id, open_one.id, "unknown"]) == 2
    assert ctx.store.bills.all() == []
    # the payment keeps its file; the open bill's file record goes, its file stays while Undo can bring it back
    assert [a.bill_id for a in att_svc.list_for(ctx, tx.id)] == [bill.id]
    assert att_svc.get(ctx, open_att.id) is None and bill_file(ctx, open_att).is_file()
    assert att_svc.cleanup(ctx) == 0
    latest_undo(ctx)
    assert {b.id for b in ctx.store.bills.all()} == {bill.id, open_one.id}
    assert att_svc.get(ctx, open_att.id) is not None
    bills.delete(ctx, [open_one.id])
    ctx.store.history = History(ctx.store.data_dir / "other-history.jsonl")  # the old actions have dropped out
    assert att_svc.cleanup(ctx) == 1 and not bill_file(ctx, open_att).exists()
    # a deleted bill's paid file is the transaction's alone, and goes with it
    bills.delete(ctx, [bill.id])
    tx_svc.delete(ctx, [tx.id])
    assert ctx.store.attachments.all() == []


def test_add_to_bill_needs_the_bill(ctx):
    with pytest.raises(ValidationError, match="no longer in the list"):
        att_svc.add_to_bill(ctx, "unknown", "a.pdf", b"%PDF-1.4\n")


# ------------------------------------------------------------------ backups, export, history
def test_backup_and_restore_bring_back_bill_files(ctx, setup):
    [open_bill] = bills.scan(ctx, "open.pdf", fx.pdf(scr())).added
    [paid_bill] = bills.scan(ctx, "paid.png", fx.png(non())).added
    tx = bills.mark_paid(ctx, paid_bill.id, date(2026, 10, 2), setup["chf"].id, category="04.1")
    [open_att] = att_svc.list_for_bill(ctx, open_bill.id)
    [paid_att] = att_svc.list_for(ctx, tx.id)
    data = archive.create(ctx)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
    assert {f"attachments/{open_bill.id}/{open_att.stored}", f"attachments/{paid_bill.id}/{paid_att.stored}"} <= names
    assert "bills.csv" in names

    bills.delete(ctx, [open_bill.id])
    kept = bill_file(ctx, open_att).read_bytes()
    bill_file(ctx, open_att).unlink()
    archive.restore(ctx, archive.read(data))
    assert {b.id for b in ctx.store.bills.all()} == {open_bill.id, paid_bill.id}
    assert att_svc.read(ctx, att_svc.get(ctx, open_att.id)) == kept
    assert bill_file(ctx, open_att).is_file() and bill_file(ctx, paid_att).is_file()


@pytest.mark.parametrize(
    ("transaction_id", "bill_id"),
    [("", ""), ("", "../x"), ("abc", "a b"), ("../x", "abc")],
)
def test_backups_refuse_bill_records_with_unsafe_ids(ctx, transaction_id: str, bill_id: str):
    [bill] = bills.scan(ctx, "a.png", fx.png(scr())).added
    att = att_svc.list_for_bill(ctx, bill.id)[0]
    assert att_svc.ids_ok(att)
    files_ = archive.read(archive.create(ctx))
    row = f"{att.transaction_id},a.png,{att.stored}"
    assert row.encode() in files_["attachments.csv"]
    files_["attachments.csv"] = (
        files_["attachments.csv"]
        .replace(row.encode(), f"{transaction_id},a.png,{att.stored}".encode())
        .replace(f",{bill.id},".encode(), f",{bill_id},".encode())
    )
    with pytest.raises(ValidationError, match="can’t be read"):
        archive.check(files_)


def test_bills_in_the_json_export_and_the_history(ctx, setup):
    [bill] = bills.scan(ctx, "a.png", fx.png(scr())).added
    doc = json.loads(export_svc.everything_json(ctx))
    assert [b["id"] for b in doc["bills"]] == [bill.id] and doc["bills"][0]["status"] == "open"
    assert doc["attachments"][0]["bill_id"] == bill.id
    act = undo_svc.latest(ctx)
    lines = [undo_svc.describe_change(ctx, c) for c in act.changes]
    assert "Bill added: Robert Schneider AG · 199.95 CHF" in lines
    assert "Attachment added: a.png" in lines


def test_the_file_route_serves_a_bills_file(ctx):
    [bill] = bills.scan(ctx, "Rechnung.png", fx.png(scr())).added
    [att] = att_svc.list_for_bill(ctx, bill.id)
    context_module._ctx = ctx
    try:
        response = att_ui.serve(att.id)
    finally:
        context_module._ctx = None
    assert response.status_code == 200 and response.media_type == "image/png"
