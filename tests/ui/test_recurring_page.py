"""The Recurring page (F05): the Due list, booking and skipping, the series sheet, and Make Recurring."""

import asyncio
import re
from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app.domain import months
from budget_app.services import recurring as rec_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout

RENT = "04.1 Actual rental payments made for housing"
SELECTED = re.compile(r".*getSelectedRows")


# ------------------------------------------------------------------ helpers
def first_of(months_back: int) -> date:
    """The 1st of the month `months_back` months before this one."""
    return months.first_day(months.add_months(months.current_month(), -months_back))


def rent(setup, **kw) -> RecurringInput:
    base = dict(
        type="expenditure",
        amount=1900,
        currency="CHF",
        category="04.1",
        from_account=setup["chf"].id,
        start=first_of(2),
    )
    base.update(kw)
    return RecurringInput(**base)


def top_dialog(user: User) -> ui.dialog:
    with user:
        dialogs = [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]
    assert dialogs, "expected an open sheet"
    return max(dialogs, key=lambda d: d.id)


def in_dialog(user: User, kind: type, label: str | None = None, text: str | None = None) -> list:
    return [
        e
        for e in top_dialog(user).descendants()
        if isinstance(e, kind)
        and (label is None or e.props.get("label") == label)
        and (text is None or getattr(e, "text", None) == text)
    ]


def dialog_texts(user: User) -> list[str]:
    return [e.text for e in top_dialog(user).descendants() if isinstance(e, ui.label)]


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def set_value(user: User, element, value) -> None:
    with user:
        element.value = value


def page_buttons(user: User, text: str) -> list[ui.button]:
    return sorted((b for b in user.find(kind=ui.button).elements if b.text == text), key=lambda b: b.id)


def row_icon(user: User, icon: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.props.get("icon") == icon]
    assert found, f"no {icon} button"
    return found[0]


def due_amounts(user: User) -> list[ui.number]:
    return [n for n in user.find(kind=ui.number).elements if "suffix" in n.props and not n.props.get("label")]


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


# ------------------------------------------------------------------ the page
async def test_empty_page(user: User, ctx) -> None:
    await user.open("/recurring")
    await user.should_see("Nothing is due.")
    await user.should_see("No recurring transactions yet.")
    await user.should_not_see("Book All")


async def test_due_occurrences_are_listed_with_the_series(user: User, ctx, setup) -> None:
    rec_svc.save(ctx, rent(setup, end=first_of(-6), comment="Flat"))
    ended = rec_svc.save(ctx, rent(setup, amount=20, category="08.3", start=first_of(5), end=first_of(5), auto=True))
    rec_svc.skip(ctx, ended.id, first_of(5))
    tx_svc.save(ctx, rec_svc._template(rent(setup)))  # an unrelated booking does not count
    await user.open("/recurring")
    await user.should_see("Due")
    await user.should_see("Book All")
    assert len(page_buttons(user, "Book")) == 1  # the later occurrences wait for the first
    await user.should_see("after the one on")
    await user.should_see("Flat")
    await user.should_see("ended")
    await user.should_see("books automatically")
    await user.should_see("Every month")
    assert rec_svc.due_count(ctx, date.today()) == 3


async def test_book_one_with_a_changed_amount(user: User, ctx, setup) -> None:
    series = rec_svc.save(ctx, rent(setup))
    await user.open("/recurring")
    [amount] = due_amounts(user)
    set_value(user, amount, 1875.25)
    click(user, page_buttons(user, "Book")[0])
    await user.should_see("last booked on")
    [tx] = ctx.store.transactions.all()
    assert (tx.amount, tx.date, tx.recurring_id) == (1875.25, first_of(2), series.id)
    assert notified(user, f"Booked {RENT}")


async def test_a_failed_booking_is_reported(user: User, ctx, setup) -> None:
    rec_svc.save(ctx, rent(setup))
    await user.open("/recurring")
    [amount] = due_amounts(user)
    set_value(user, amount, 0)
    click(user, page_buttons(user, "Book")[0])
    await user.should_see("Amount must be a positive number")
    assert ctx.store.transactions.all() == []


async def test_skip_one(user: User, ctx, setup) -> None:
    series = rec_svc.save(ctx, rent(setup))
    await user.open("/recurring")
    click(user, page_buttons(user, "Skip")[0])
    await settle()
    assert ctx.store.transactions.all() == []
    assert ctx.store.recurring.get(series.id).next_due == first_of(1)
    assert notified(user, "Skipped")


async def test_skipping_a_stale_row_is_refused(user: User, ctx, setup) -> None:
    series = rec_svc.save(ctx, rent(setup))
    await user.open("/recurring")
    rec_svc.skip(ctx, series.id, first_of(2))  # behind the page's back
    click(user, page_buttons(user, "Skip")[0])
    await user.should_see("already been booked or skipped")


async def test_book_all_with_an_edit_and_a_failure(user: User, ctx, setup) -> None:
    rec_svc.save(ctx, rent(setup))
    broken = rec_svc.save(ctx, rent(setup, amount=10, category="08.3", start=first_of(1)))
    stored = ctx.store.recurring.get(broken.id)
    stored.from_account = "gone"
    ctx.store.recurring.update(stored)
    await user.open("/recurring")
    amounts = due_amounts(user)
    set_value(user, amounts[0], 1000)
    click(user, page_buttons(user, "Book All")[0])
    await settle()
    booked = ctx.store.transactions.all()
    assert len(booked) == 3 and len({t.recurring_id for t in booked}) == 1
    assert notified(user, "Booked 3 transactions")
    assert user.notify.contains("1 could not be booked")
    assert sorted(t.amount for t in booked) == [1000, 1900, 1900]  # the amount changed in the list


async def test_the_sidebar_counts_due_occurrences(user: User, ctx, setup) -> None:
    rec_svc.save(ctx, rent(setup))
    await user.open("/")
    badges = [b for b in user.find(kind=ui.badge).elements if "nav-badge" in b.classes]
    assert [b.text for b in badges] == ["3"]
    await user.open("/recurring")
    click(user, page_buttons(user, "Book")[0])
    await settle()
    assert [b.text for b in user.find(kind=ui.badge).elements if "nav-badge" in b.classes] == ["2"]


# ------------------------------------------------------------------ the sheet
async def test_add_a_series_in_the_sheet(user: User, ctx, setup) -> None:
    await user.open("/recurring")
    click(user, page_buttons(user, "New Recurring Transaction")[-1])
    await user.should_see("Book automatically")
    [save] = in_dialog(user, ui.button, text="Add Recurring Transaction")
    assert not save.enabled
    [amount] = in_dialog(user, ui.number, "Amount")
    set_value(user, amount, 89)
    [category] = in_dialog(user, ui.select, "Category")
    set_value(user, category, "08.3")
    [acc] = in_dialog(user, ui.select, "From Account")
    set_value(user, acc, setup["chf"].id)
    [start] = in_dialog(user, ui.input, "First Date")
    set_value(user, start, first_of(-1).isoformat())
    [recur] = in_dialog(user, ui.select, "Repeat")
    set_value(user, recur, 3)
    assert save.enabled
    assert any(text.startswith("Next on ") for text in dialog_texts(user))
    click(user, save)
    await settle()
    [series] = ctx.store.recurring.all()
    assert (series.amount, series.category, series.recur_months, series.start) == (89, "08.3", 3, first_of(-1))
    assert notified(user, "Recurring transaction added")
    assert ctx.store.transactions.all() == []


async def test_an_automatic_series_books_what_is_due_at_once(user: User, ctx, setup) -> None:
    await user.open("/recurring")
    click(user, page_buttons(user, "New Recurring Transaction")[-1])
    for label, value in (("Amount", 1900), ("Category", "04.1"), ("From Account", setup["chf"].id)):
        [el] = in_dialog(user, ui.number if label == "Amount" else ui.select, label)
        set_value(user, el, value)
    [start] = in_dialog(user, ui.input, "First Date")
    set_value(user, start, first_of(1).isoformat())
    [auto] = in_dialog(user, ui.switch)
    set_value(user, auto, True)
    click(user, in_dialog(user, ui.button, text="Add Recurring Transaction")[0])
    await settle()
    assert len(ctx.store.transactions.all()) == 2
    assert notified(user, "2 transactions booked")


async def test_income_and_transfer_fields(user: User, ctx, setup) -> None:
    await user.open("/recurring")
    click(user, page_buttons(user, "New Recurring Transaction")[-1])
    [typ] = in_dialog(user, ui.toggle)
    [from_acc] = in_dialog(user, ui.select, "From Account")
    [to_acc] = in_dialog(user, ui.select, "To Account")
    [income] = in_dialog(user, ui.select, "Income category")
    assert (from_acc.visible, to_acc.visible, income.visible) == (True, False, False)
    set_value(user, typ, "income")
    assert (from_acc.visible, to_acc.visible, income.visible) == (False, True, True)
    set_value(user, typ, "transfer")
    assert (from_acc.visible, to_acc.visible, income.visible) == (True, True, False)
    [amount] = in_dialog(user, ui.number, "Amount")
    set_value(user, amount, 100)
    assert "Select the From account" in dialog_texts(user)
    [cancel] = in_dialog(user, ui.button, text="Cancel")
    click(user, cancel)


async def test_edit_a_series(user: User, ctx, setup) -> None:
    series = rec_svc.save(ctx, rent(setup))
    rec_svc.book(ctx, series.id, first_of(2))
    await user.open("/recurring")
    click(user, row_icon(user, "o_edit"))
    await user.should_see("Edit Recurring Transaction")
    await user.should_see("Changes apply from the next occurrence on")
    [amount] = in_dialog(user, ui.number, "Amount")
    set_value(user, amount, 2000)
    [until] = in_dialog(user, ui.input, "Until")
    set_value(user, until, first_of(3).isoformat())
    assert "Until must not be before the first date" in dialog_texts(user)
    set_value(user, until, first_of(2).isoformat())
    assert "This series has ended" in dialog_texts(user)
    set_value(user, until, "")
    click(user, in_dialog(user, ui.button, text="Save Changes")[0])
    await settle()
    assert ctx.store.recurring.get(series.id).amount == 2000
    assert [t.amount for t in ctx.store.transactions.all()] == [1900]  # the booking stays
    assert notified(user, "Changes saved")


async def test_save_errors_are_shown(user: User, ctx, setup) -> None:
    series = rec_svc.save(ctx, rent(setup))
    await user.open("/recurring")
    click(user, row_icon(user, "o_edit"))
    rec_svc.delete(ctx, [series.id])  # gone before saving
    click(user, in_dialog(user, ui.button, text="Save Changes")[0])
    await user.should_see("Recurring transaction not found")


async def test_delete_a_series(user: User, ctx, setup) -> None:
    rec_svc.save(ctx, rent(setup))
    await user.open("/recurring")
    click(user, row_icon(user, "o_delete"))
    await user.should_see("Delete this recurring transaction?")
    click(user, in_dialog(user, ui.button, text="Delete")[0])
    await settle()
    assert ctx.store.recurring.all() == []
    assert notified(user, f"Deleted {RENT}")


# ------------------------------------------------------------------ from the transactions list
async def select(user: User, *ids: str) -> None:
    user.javascript_rules[SELECTED] = lambda _: [{"id": i} for i in ids]
    [grid] = user.find(kind=ui.aggrid).elements
    UserInteraction(user, {grid}, None).trigger("selectionChanged")
    await user.should_see(f"{len(ids)} selected")


def expense(setup, **kw) -> TransactionInput:
    base = dict(
        date=first_of(0), type="expenditure", amount=89, currency="CHF", category="08.3", from_account=setup["chf"].id
    )
    base.update(kw)
    return TransactionInput(**base)


async def test_make_recurring_from_a_transaction(user: User, ctx, setup) -> None:
    tx = tx_svc.save(ctx, expense(setup))
    await user.open("/transactions")
    await select(user, tx.id)
    click(user, page_buttons(user, "Make Recurring…")[0])
    await user.should_see("New Recurring Transaction")
    [start] = in_dialog(user, ui.input, "First Date")
    assert start.value == tx.date.isoformat()
    click(user, in_dialog(user, ui.button, text="Add Recurring Transaction")[0])
    await settle()
    [series] = ctx.store.recurring.all()
    assert ctx.store.transactions.get(tx.id).recurring_id == series.id
    assert series.next_due == months.first_day(months.add_months(months.current_month(), 1))
    [grid] = user.find(kind=ui.aggrid).elements
    assert grid.options["rowData"][0]["recurring"] == "Every month"


async def test_make_recurring_refuses_what_cannot_repeat(user: User, ctx, setup) -> None:
    advance = tx_svc.save(ctx, expense(setup, is_advance=True, advance_value=50, debtor="Clara"))
    plain = tx_svc.save(ctx, expense(setup, amount=5))
    await user.open("/transactions")
    await select(user, advance.id, plain.id)
    click(user, page_buttons(user, "Make Recurring…")[0])
    await user.should_see("Select exactly one transaction to make recurring")
    await select(user, advance.id)
    click(user, page_buttons(user, "Make Recurring…")[0])
    await user.should_see("without an advance payment can repeat")
    series = rec_svc.save(ctx, rec_svc.from_transaction(ctx.store.transactions.get(plain.id)), first_booked=plain.id)
    await select(user, plain.id)
    click(user, page_buttons(user, "Make Recurring…")[0])
    await user.should_see("already belongs to a recurring transaction")
    assert ctx.store.recurring.all() == [series]


# ------------------------------------------------------------------ automatic booking at startup
async def test_book_recurring_books_automatic_series(ctx, setup) -> None:
    rec_svc.save(ctx, rent(setup, auto=True))
    assert layout.book_recurring(ctx) == 3
    assert layout.book_recurring(ctx) == 0


async def test_a_page_without_the_badge_is_left_alone(user: User, ctx) -> None:
    await user.open("/recurring")
    layout._due_badges.clear()
    with user:
        layout.refresh_due_badge()  # nothing to update, nothing fails
