"""The Bills page (F03): dropping QR bills, the Open list with its badge, paying through the prefilled sheet."""

import asyncio
from datetime import date, timedelta

import qr_fixtures as fx
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified, undo_toasts

from budget_app.domain import qrbill
from budget_app.domain.models import BILL_DISMISSED, BILL_OPEN, BILL_PAID
from budget_app.services import attachments as att_svc
from budget_app.services import bills as bills_svc
from budget_app.services import rules as rules_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.pages import bills as bills_page
from budget_app.ui.widgets import attachments as att_ui
from budget_app.ui.widgets.selects import PayeeInput

TODAY = date.today()


def swico_due_in(days: int) -> str:
    """Bill information whose invoice date and 30 days net make the bill due in `days` days."""
    issued = TODAY + timedelta(days=days - 30)
    return f"//S1/10/R-17/11/{issued:%y%m%d}/40/0:30"


def scr(amount: str = "199.95") -> str:
    return fx.payload(iban="CH5800791123000889012", amount=amount, reference_type="SCOR", reference="RF18539007547034")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


def newest(user: User, kind: type, label: str | None = None, text: str | None = None):
    found = [
        e
        for e in user.find(kind=kind).elements
        if (label is None or e.props.get("label") == label) and (text is None or getattr(e, "text", None) == text)
    ]
    assert found, f"no {kind.__name__} {label or text!r}"
    return max(found, key=lambda e: e.id)  # closed sheets stay in the simulation: the newest is the open one


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def icon_buttons(user: User, icon: str) -> list[ui.button]:
    return sorted((b for b in user.find(kind=ui.button).elements if b.props.get("icon") == icon), key=lambda b: b.id)


async def drop(user: User, *named: tuple[str, bytes]) -> None:
    element = newest(user, ui.upload)
    await element.handle_uploads([SmallFileUpload(name, "application/octet-stream", data) for name, data in named])
    await settle()


def bills_badge(user: User) -> ui.badge:
    """The count on the Bills tab (hidden too, which `find` leaves out)."""
    return layout._due_badges[user.client]["/bills"]


def history_for(ctx, setup, payee: str, times: int = 2) -> None:
    for k in range(times):
        tx_svc.save(
            ctx,
            TransactionInput(
                date=date(2026, 9, 1 + k),
                type="expenditure",
                amount=80,
                currency="CHF",
                category="04.4.1",
                from_account=setup["chf"].id,
                payee=payee,
            ),
        )


# ------------------------------------------------------------------ dropping files
async def test_dropping_a_pdf_and_a_photo_lists_the_bills(user: User, ctx, setup) -> None:
    await user.open("/bills")
    await user.should_see("Drop QR bills (PDF or photo)")
    await user.should_see("No open bills.")
    assert not bills_badge(user).visible
    await drop(
        user,
        ("Rechnung.pdf", fx.pdf(fx.payload(bill_info=swico_due_in(5)))),
        ("Spende.png", fx.png(scr(amount=""))),
    )
    assert notified(user, "Added the bill of Robert Schneider AG")
    [due_soon, undated] = bills_svc.open_bills(ctx)
    assert due_soon.amount == 1949.75 and undated.amount == 0
    await user.should_see("1'949.75 CHF")
    await user.should_see("Amount to fill in (CHF)")
    due_label = newest(user, ui.label, text=f"Due on {(TODAY + timedelta(days=5)).strftime('%-d %b %Y')}")
    assert "due-soon" in due_label.classes
    await user.should_see("No due date")
    await user.should_see("Auftrag vom 15.06.2020 · 21 00000 00003 13947 14300 09017")
    # the file opens from the row, served by the attachment route
    [att] = att_svc.list_for_bill(ctx, due_soon.id)
    assert [link for link in user.find(kind=ui.link).elements if link.props.get("href") == att_ui.url(att)]
    badge = bills_badge(user)
    assert badge.visible and badge.text == "1"  # due within a week; the one without a date isn't counted


async def test_files_without_a_usable_bill_are_explained(user: User, ctx, setup) -> None:
    await user.open("/bills")
    await drop(user, ("letter.pdf", fx.pdf(pages_before=1)))
    assert user.notify.contains("letter.pdf: No QR bill found in this file")
    await drop(user, ("a.png", fx.png(scr())))
    await drop(user, ("again.png", fx.png(scr())))
    assert user.notify.contains("again.png: This bill is already in the list")
    await drop(user, ("photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16))
    assert user.notify.contains("is a HEIC photo, which can’t be read yet")
    # several bills at once: the new one is added, the known one counted, a broken one explained
    bad = fx.payload(iban="CH4431999123000889013")
    await drop(user, ("all.pdf", fx.pdf(scr(), fx.payload(), bad)))
    assert notified(user, "Added the bill of Robert Schneider AG · 1 was already in the list")
    assert user.notify.contains("The creditor’s IBAN CH4431999123000889013 is invalid")
    await drop(user, ("two.pdf", fx.pdf(scr(amount="1.00"), scr(amount="2.00"))))
    assert notified(user, "Added 2 bills")
    assert len(bills_svc.open_bills(ctx)) == 4
    UserInteraction(user, {newest(user, ui.upload)}, None).trigger("rejected")
    assert user.notify.contains("Only PDF files and photos up to 20 MB")


# ------------------------------------------------------------------ paying
async def test_mark_paid_opens_the_prefilled_sheet_and_books_the_bill(user: User, ctx, setup) -> None:
    history_for(ctx, setup, "Robert Schneider AG")
    [bill] = bills_svc.scan(ctx, "Rechnung.pdf", fx.pdf(fx.payload(bill_info=swico_due_in(-2)))).added
    await user.open("/bills")
    due_label = newest(user, ui.label, text=f"Overdue since {(TODAY - timedelta(days=2)).strftime('%-d %b %Y')}")
    assert "due-late" in due_label.classes
    assert bills_badge(user).text == "1"
    click(user, newest(user, ui.button, text="Mark Paid…"))
    await user.should_see("Pay Bill")
    assert newest(user, ui.number, "Amount").value == 1949.75
    assert newest(user, PayeeInput, "Payee").value == "Robert Schneider AG"
    assert newest(user, ui.textarea, "Note").value == "Auftrag vom 15.06.2020 · Ref. 21 00000 00003 13947 14300 09017"
    # the category is suggested with its reason, filled in only on a click (F07)
    category = newest(user, ui.select, "Category")
    assert category.value is None
    await user.should_see("Used 2 times for this payee · Bank (CHF)")
    # the bill's file is listed under Attachments, to go with the payment
    await user.should_see("Rechnung.pdf")
    assert [e for e in user.find(kind=ui.label).elements if e.text.endswith("· Attached when you save")]
    click(user, newest(user, ui.button, text=f"Use {rules_svc.category_label(ctx, 'expenditure', '04.4.1')}"))
    assert category.value == "04.4.1"
    click(user, newest(user, ui.button, text="Mark Paid"))
    await settle()
    assert notified(user, "Bill paid")

    paid = bills_svc.get(ctx, bill.id)
    assert paid.status == BILL_PAID
    tx = ctx.store.transactions.get(paid.transaction_id)
    assert (tx.amount, tx.category, tx.payee, tx.from_account) == (
        1949.75,
        "04.4.1",
        "Robert Schneider AG",
        setup["chf"].id,
    )
    assert [a.bill_id for a in att_svc.list_for(ctx, tx.id)] == [bill.id]
    await user.should_see("No open bills.")
    await user.should_see("Paid and Dismissed · 1 bill")
    assert not bills_badge(user).visible

    # one Undo takes back the payment and opens the bill again
    toast = max(undo_toasts(user), key=lambda n: n.id)
    UserInteraction(user, {toast}, None).trigger("undo")
    assert bills_svc.get(ctx, bill.id).status == BILL_OPEN and ctx.store.transactions.get(tx.id) is None


async def test_the_sheet_refuses_another_type_and_a_bill_gone_meanwhile(user: User, ctx, setup) -> None:
    [bill] = bills_svc.scan(ctx, "a.png", fx.png(scr())).added
    await user.open("/bills")
    click(user, newest(user, ui.button, text="Mark Paid…"))
    await user.should_see("Pay Bill")
    with user:
        newest(user, ui.select, "Category").value = "12.1"
        newest(user, ui.select, "From Account").value = setup["chf"].id
    newest(user, ui.toggle).set_value("income")
    with user:
        newest(user, ui.select, "Income category").value = setup["salary"].id
        newest(user, ui.select, "To Account").value = setup["chf"].id
    click(user, newest(user, ui.button, text="Mark Paid"))
    await settle()
    assert user.notify.contains("A bill is paid with an expenditure")
    newest(user, ui.toggle).set_value("expenditure")
    bills_svc.dismiss(ctx, bill.id)  # elsewhere, in the meantime
    click(user, newest(user, ui.button, text="Mark Paid"))
    await settle()
    assert user.notify.contains("This bill is no longer open")
    assert ctx.store.transactions.all() == []  # nothing was booked

    bills_svc.reopen(ctx, bill.id)
    await user.open("/bills")
    bills_svc.delete(ctx, [bill.id])
    click(user, newest(user, ui.button, text="Mark Paid…"))
    assert user.notify.contains("This bill is no longer in the list")


async def test_a_paid_bill_shows_its_payment(user: User, ctx, setup) -> None:
    [bill] = bills_svc.scan(ctx, "a.png", fx.png(scr())).added
    tx = bills_svc.mark_paid(ctx, bill.id, date(2026, 10, 2), setup["chf"].id, category="12.1")
    await user.open("/bills")
    await user.should_see("Paid and Dismissed · 1 bill")
    await user.should_see("Paid on 2 Oct 2026 · 199.95 CHF")
    click(user, newest(user, ui.button, text="Show Payment"))
    await user.should_see("Edit Transaction")
    tx_svc.delete(ctx, [tx.id])  # its payment gone, the bill is open again
    bills_svc.dismiss(ctx, bill.id)
    other = bills_svc.scan(ctx, "b.png", fx.png(scr(amount="5.00"))).added[0]
    bills_svc.mark_paid(ctx, other.id, date(2026, 10, 2), setup["chf"].id, category="12.1")
    ctx.store.transactions.delete([bills_svc.get(ctx, other.id).transaction_id])  # gone without the service
    await user.open("/bills")
    await user.should_see("Paid and Dismissed · 2 bills")
    await user.should_see("Paid")


# ------------------------------------------------------------------ dismiss, reopen, due date, delete
async def test_dismiss_reopen_due_date_and_delete(user: User, ctx, setup) -> None:
    [bill] = bills_svc.scan(ctx, "a.png", fx.png(scr())).added
    await user.open("/bills")
    # the due date, through the row's calendar menu
    [picker] = list(user.find(kind=ui.date).elements)
    in_three = TODAY + timedelta(days=3)
    with user:
        picker.value = in_three.isoformat()
    click(user, newest(user, ui.button, text="Set"))
    await settle()
    assert bills_svc.get(ctx, bill.id).due_date == in_three
    assert notified(user, f"Robert Schneider AG: Due on {in_three.strftime('%-d %b %Y')}")
    assert bills_badge(user).text == "1"
    click(user, newest(user, ui.button, text="No Due Date"))
    await settle()
    assert bills_svc.get(ctx, bill.id).due == ""
    assert notified(user, "Robert Schneider AG: No due date")
    with user:
        newest(user, ui.date).value = TODAY.isoformat()
    click(user, newest(user, ui.button, text="Set"))
    await settle()
    await user.should_see("Due today")

    [dismiss] = icon_buttons(user, "o_do_not_disturb_on")
    click(user, dismiss)
    await settle()
    assert bills_svc.get(ctx, bill.id).status == BILL_DISMISSED
    assert notified(user, "Dismissed the bill of Robert Schneider AG")
    await user.should_see("Paid and Dismissed · 1 bill")
    await user.should_see("Dismissed")
    click(user, newest(user, ui.button, text="Reopen"))
    await settle()
    assert bills_svc.get(ctx, bill.id).status == BILL_OPEN
    assert notified(user, "Reopened the bill of Robert Schneider AG")

    bills_svc.dismiss(ctx, bill.id)  # elsewhere: the row's actions explain what happened
    click(user, icon_buttons(user, "o_do_not_disturb_on")[-1])
    await settle()
    assert user.notify.contains("This bill is no longer open")

    await user.open("/bills")
    click(user, icon_buttons(user, "o_delete")[-1])
    await user.should_see("Delete this bill?")
    await user.should_see("Its file is removed too. Nothing is booked.")
    click(user, newest(user, ui.button, text="Delete"))
    await settle()
    assert bills_svc.get(ctx, bill.id) is None
    assert notified(user, "Deleted the bill of Robert Schneider AG")


async def test_deleting_a_paid_bill_keeps_its_payment(user: User, ctx, setup) -> None:
    [bill] = bills_svc.scan(ctx, "a.png", fx.png(scr())).added
    tx = bills_svc.mark_paid(ctx, bill.id, date(2026, 10, 2), setup["chf"].id, category="12.1")
    await user.open("/bills")
    click(user, icon_buttons(user, "o_delete")[-1])
    await user.should_see("Its payment stays, with the file.")
    click(user, newest(user, ui.button, text="Delete"))
    await settle()
    assert ctx.store.bills.all() == [] and att_svc.list_for(ctx, tx.id)


async def test_in_the_app_window_the_file_opens_in_the_system_viewer(user: User, ctx, setup, monkeypatch) -> None:
    [bill] = bills_svc.scan(ctx, "Rechnung.png", fx.png(scr())).added
    opened = []
    monkeypatch.setattr(att_svc, "system_open", opened.append)
    monkeypatch.setattr(app.native, "main_window", Window())
    await user.open("/bills")
    click(user, newest(user, ui.button, text="Rechnung.png"))
    [att] = att_svc.list_for_bill(ctx, bill.id)
    assert opened == [att_svc.path(ctx, att)]
    att_svc.path(ctx, att).write_bytes(b"MZ not a png")  # changed on disk: refused
    click(user, newest(user, ui.button, text="Rechnung.png"))
    assert user.notify.contains("isn’t opened")


class Window:
    """Stands in for the app window: whatever the page asks of it does nothing."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def test_due_texts() -> None:
    bill = bills_svc.from_payload(qrbill.parse(scr()))
    assert bills_page.due_text(bill, TODAY) == ("No due date", "text-2")
    bill.due = (TODAY + timedelta(days=30)).isoformat()
    assert bills_page.due_text(bill, TODAY)[1] == "text-2"
    bill.due = (TODAY + timedelta(days=7)).isoformat()
    assert bills_page.due_text(bill, TODAY)[1] == "due-soon"
