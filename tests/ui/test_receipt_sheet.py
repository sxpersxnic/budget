"""The Receipt sheet (F04): a receipt dropped on the Transactions page becomes a prefilled expenditure with its file."""

import asyncio
import io
from datetime import date

from nicegui import ElementFilter, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from PIL import Image, ImageDraw

from budget_app.domain.receipts import TextBox
from budget_app.services import attachments as att_svc
from budget_app.services import items as items_svc
from budget_app.services import ocr
from budget_app.services import payees as payee_svc
from budget_app.ui.widgets.selects import PayeeInput

TODAY = date.today()
ROWS = [
    "Coop Pronto Zürich HB",
    "Kassenbon 4711",
    f"{TODAY:%d.%m.%Y} 18:42",
    "Bio Milch 1L  1.95",
    "Zopf  4.50",
    "TOTAL CHF  6.45",
]


def photo() -> bytes:
    img = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(img).text((10, 10), "receipt", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def fake(rows: list[str]):
    def read(image):
        return [TextBox(text, 10.0, 10.0 + 40 * i, 30.0) for i, text in enumerate(rows)]

    return read


async def settle() -> None:
    for _ in range(8):
        await asyncio.sleep(0.02)


def newest(user: User, kind: type, label: str | None = None, text: str | None = None):
    found = [
        e
        for e in user.find(kind=kind).elements
        if (label is None or e.props.get("label") == label) and (text is None or getattr(e, "text", None) == text)
    ]
    assert found, f"no {kind.__name__} {label or text!r}"
    return max(found, key=lambda e: e.id)


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


async def open_sheet(user: User) -> None:
    await user.open("/transactions")
    with user:  # in the page's More menu; menus are closed in the simulation, so their items count as hidden
        [item] = [
            i
            for i in ElementFilter(kind=ui.menu_item, local_scope=False)
            if any(getattr(e, "text", None) == "Receipt…" for e in i.descendants())
        ]
    click(user, item)
    await user.should_see("Drop a receipt (PDF or photo)")


async def drop(user: User, name: str, data: bytes) -> None:
    element = newest(user, ui.upload)
    await element.handle_uploads([SmallFileUpload(name, "application/octet-stream", data)])
    await settle()


async def test_a_receipt_fills_the_form_and_is_saved_with_the_transaction(user: User, ctx, setup, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "recognizer", lambda: fake(ROWS))
    payee_svc.ensure(ctx, "Coop Pronto Zürich HB")
    await open_sheet(user)
    await user.should_see("It is read on your computer")
    await drop(user, "IMG_0042.png", photo())
    await user.should_see(
        f"Read from the receipt: Coop Pronto Zürich HB · {TODAY:%-d %b %Y} · 6.45 CHF. Check it before you save."
    )
    assert newest(user, ui.number, "Amount").value == 6.45
    assert newest(user, PayeeInput, "Payee").value == "Coop Pronto Zürich HB"
    assert newest(user, ui.input, "Date").value == TODAY.isoformat()
    # the receipt is shown beside the form, and its file waits under Attachments
    assert [i for i in user.find(kind=ui.image).elements if "receipt-image" in i.classes]
    await user.should_see("IMG_0042.png")
    assert [e for e in user.find(kind=ui.label).elements if e.text.endswith("· Attached when you save")]
    # its lines are the item list, adding up to the amount
    await user.should_see("2 items · matches the amount")
    lines = sorted(
        (e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "e.g. Milk"), key=lambda e: e.id
    )
    names = [e.value for e in lines]
    assert names == ["Bio Milch 1L", "Zopf"]

    newest(user, ui.select, "Category").set_value("01.1")
    newest(user, ui.select, "From Account").set_value(setup["chf"].id)
    click(user, newest(user, ui.button, text="Add Transaction"))
    await settle()
    assert notified(user, "Transaction added")
    [tx] = ctx.store.transactions.all()
    assert (tx.amount, tx.payee, tx.category, tx.date) == (6.45, "Coop Pronto Zürich HB", "01.1", TODAY)
    [att] = att_svc.list_for(ctx, tx.id)
    assert att.name == "IMG_0042.png" and att_svc.exists(ctx, att)
    assert [(i.name, i.unit_price) for i in items_svc.items_for(ctx, tx.id)] == [("Bio Milch 1L", 1.95), ("Zopf", 4.5)]


async def test_without_the_engine_the_file_is_attached_and_the_user_types_the_rest(
    user: User, ctx, setup, monkeypatch
) -> None:
    monkeypatch.setattr(ocr, "available", lambda: False)
    monkeypatch.setattr(ocr, "recognizer", lambda: None)
    await open_sheet(user)
    await user.should_see("Reading receipts automatically isn’t available on this computer.")
    await drop(user, "bon.png", photo())
    await user.should_see(
        "Reading receipts isn’t available on this computer. Type the amount, date and payee from the receipt."
    )
    assert newest(user, ui.number, "Amount").value is None
    assert newest(user, ui.input, "Date").value == TODAY.isoformat()
    await user.should_see("bon.png")
    assert [i for i in user.find(kind=ui.image).elements if "receipt-image" in i.classes]


async def test_an_unreadable_receipt_still_opens_the_form(user: User, ctx, setup, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "recognizer", lambda: fake([]))
    await open_sheet(user)
    await drop(user, "blurry.png", photo())
    await user.should_see("Nothing could be read from this receipt. Type the amount, date and payee from it.")
    assert newest(user, ui.number, "Amount").value is None


async def test_an_engine_failure_is_reported_and_the_form_opens(user: User, ctx, setup, monkeypatch) -> None:
    def broken(image):
        raise RuntimeError("boom")

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "recognizer", lambda: broken)
    await open_sheet(user)
    await drop(user, "bon.png", photo())
    assert user.notify.contains("“bon.png” couldn’t be read. Type the amount, date and payee.")
    await user.should_see("Nothing could be read from this receipt.")
    assert newest(user, ui.button, text="Add Transaction")


async def test_files_that_are_not_receipts_are_refused(user: User, ctx, setup, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "recognizer", lambda: fake(ROWS))
    await open_sheet(user)
    await drop(user, "contract.docx", b"PK\x03\x04" + b"\x00" * 26)
    assert user.notify.contains("“contract.docx” is not a PDF or a photo. Drop a PDF, JPEG or PNG of the receipt.")
    await user.should_see("Drop a receipt (PDF or photo)")  # the drop zone stays
    UserInteraction(user, {newest(user, ui.upload)}, None).trigger("rejected")
    assert user.notify.contains("Only PDF files and photos up to 20 MB")


async def test_cancel_closes_without_saving(user: User, ctx, setup, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "recognizer", lambda: fake(ROWS))
    await open_sheet(user)
    await drop(user, "bon.png", photo())
    click(user, newest(user, ui.button, text="Cancel"))
    await settle()
    assert ctx.store.transactions.all() == [] and ctx.store.attachments.all() == []
