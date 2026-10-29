"""Split transactions (F11): a category and tags per item line in the form, the rest line, the Transactions column,
the Budget page and the bulk edit hint."""

from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from notifications import notified
from test_budget_page import click as click_async
from test_budget_page import label_cell, ledger_row, open_budget
from test_bulk_edit_ui import select
from test_transaction_form import button, click, field, fill_expenditure, item_rows, put, save_button, type_toggle
from test_transactions_page import rows as grid_rows
from test_transactions_page import show_filters

from budget_app.domain import months
from budget_app.services import items as items_svc
from budget_app.services import tags as tag_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.layout import fmt
from budget_app.ui.widgets.bulk_edit import open_bulk_edit_sheet
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

PREV = months.previous_month()


def split_button(user: User, n: int) -> ui.button:
    """The split button of the n-th item line."""
    name = item_rows(user)[n][0]
    return next(c for c in name.parent_slot.children if isinstance(c, ui.button) and c.props["icon"] == "call_split")


def line_fields(user: User, label: str) -> list:
    """The visible pickers labelled `label` of the lines that have them open, in order."""
    found = [e for e in user.find(kind=ui.select).elements if e.props.get("label") == label]
    return sorted(found, key=lambda e: e.id)


def add_line(user: User, name: str, price: float) -> None:
    click(user, button(user, "Add Item"))
    line, _, unit = item_rows(user)[-1]
    put(user, line, name)
    put(user, unit, price)


@pytest.fixture
def split(ctx, setup):
    """Last month's receipt of 100 CHF: 60 groceries (the rest), a 25 detergent line and a 15 gift line."""
    y, m = map(int, PREV.split("-"))
    tx = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(y, m, 12),
            amount=100,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            payee="Migros",
        ),
    )
    gift = tag_svc.add(ctx, "Birthday")
    items_svc.replace(
        ctx,
        tx.id,
        [
            ItemInput("Bread", 1, 60),
            ItemInput("Detergent", 1, 25, "05.6"),
            ItemInput("Card", 1, 15, "09.1", [gift.id]),
        ],
    )
    return ctx.store.transactions.get(tx.id)


# ------------------------------------------------------------------ the form
async def test_a_line_gets_its_own_category_and_the_rest_is_shown(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    fill_expenditure(user, setup["chf"].id, amount=50)
    add_line(user, "Milk", 4.5)
    add_line(user, "Soap", 6.6)
    assert line_fields(user, "Category of this item") == []  # built only when a line opens it
    click(user, split_button(user, 1))
    [category] = line_fields(user, "Category of this item")
    put(user, category, "05.6")
    await user.should_see("Rest in 01.1 Groceries: 43.40")
    assert "text-primary" in split_button(user, 1).classes  # the line is marked as split
    [tags] = line_fields(user, "Tags of this item")
    put(user, tags, ["Household"])  # a new name becomes a tag when saved
    click(user, save_button(user))

    assert notified(user, "Transaction added")
    [tx] = ctx.store.transactions.all()
    stored = items_svc.items_for(ctx, tx.id)
    household = tag_svc.find(ctx, "Household")
    assert [(i.name, i.category, i.tags) for i in stored] == [("Milk", "", ""), ("Soap", "05.6", household.id)]

    # closing a line's pickers keeps what it has, and opening them again shows it
    add_line(user, "Card", 3)
    click(user, split_button(user, 0))
    put(user, line_fields(user, "Category of this item")[0], "09.1")
    click(user, split_button(user, 0))
    assert not line_fields(user, "Category of this item")
    assert "text-primary" in split_button(user, 0).classes  # still split
    click(user, split_button(user, 0))
    assert [c.value for c in line_fields(user, "Category of this item")] == ["09.1"]


async def test_lines_above_the_amount_are_refused_on_save(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    fill_expenditure(user, setup["chf"].id, amount=10)
    add_line(user, "Radio", 12)
    click(user, split_button(user, 0))
    put(user, line_fields(user, "Category of this item")[0], "09.1")
    await user.should_see(
        "The items with their own category or tags add up to 12.00 CHF, 2.00 CHF more than the amount"
    )
    click(user, save_button(user))
    assert notified(user, "2.00 CHF more than the amount")
    assert ctx.store.transactions.all() == []
    put(user, field(user, ui.number, "Amount"), 12)
    await user.should_see("Rest in 01.1 Groceries: 0.00")


async def test_income_is_not_split(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    add_line(user, "Bonus", 100)
    click(user, split_button(user, 0))
    put(user, line_fields(user, "Category of this item")[0], "09.1")
    put(user, type_toggle(user), "income")
    await user.should_see("Only expenditure is split: the items’ categories and tags are dropped when you save.")
    assert "invisible" in split_button(user, 0).classes
    put(user, field(user, ui.number, "Amount"), 100)
    put(user, field(user, ui.select, "Income category"), setup["salary"].id)
    put(user, field(user, ui.select, "To Account"), setup["chf"].id)
    click(user, save_button(user))
    [tx] = ctx.store.transactions.all()
    assert [(i.name, i.category) for i in items_svc.items_for(ctx, tx.id)] == [("Bonus", "")]


async def test_editing_a_split_shows_its_lines_and_duplicating_keeps_them(user: User, ctx, setup, split) -> None:
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=split)
    await user.should_see("Edit Transaction")
    categories = line_fields(user, "Category of this item")
    assert [c.value for c in categories] == ["05.6", "09.1"]  # lines with a category open by themselves
    await user.should_see("Rest in 01.1 Groceries: 60.00")
    put(user, categories[0], None)
    await user.should_see("Rest in 01.1 Groceries: 85.00")
    click(user, button(user, "Save Changes"))
    assert [i.category for i in items_svc.items_for(ctx, split.id)] == ["", "", "09.1"]

    click(user, button(user, "Duplicate"))
    await user.should_see("New Transaction")
    assert line_fields(user, "Category of this item")[-1].value == "09.1"


# ------------------------------------------------------------------ the pages
async def test_the_transactions_list_shows_and_finds_a_split(user: User, ctx, split) -> None:
    await user.open("/transactions")
    [row] = [r for r in grid_rows(user) if r["id"] == split.id]
    assert row["category"] == "01.1 Groceries + 2 more"
    assert row["tags"] == "Birthday"  # a line's tag shows with the transaction
    search = next(e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "Search")
    for text in ("recreational durables", "05.6", "household maintenance"):  # the categories of its lines
        put(user, search, text)
        assert [r["id"] for r in grid_rows(user)] == [split.id], text
    put(user, search, "furnishings")  # a division's name is not a line's category
    assert grid_rows(user) == []
    put(user, search, "")
    show_filters(user)
    put(user, field(user, ui.select, "Tags"), [tag_svc.find(ctx, "Birthday").id])  # a line's tag finds it too
    assert [r["id"] for r in grid_rows(user)] == [split.id]


def actual(user: User, code: str) -> str:
    """The Actual cell of a ledger row."""
    return next(c.text for c in ledger_row(user, code).default_slot.children if isinstance(c, ui.label))


async def test_the_budget_tree_counts_the_parts(user: User, ctx, split) -> None:
    await open_budget(user, PREV)
    assert [actual(user, code) for code in ("01", "05", "09")] == [fmt(60), fmt(25), fmt(15)]
    await click_async(user, label_cell(user, "09"))  # open the division: the line's own category
    assert actual(user, "09.1") == fmt(15)


async def test_bulk_edit_says_a_split_keeps_its_items(user: User, ctx, setup, split) -> None:
    other = tx_svc.save(
        ctx,
        TransactionInput(date=split.date, amount=20, currency="CHF", category="01.1", from_account=setup["chf"].id),
    )
    await user.open("/transactions")
    await select(user, split.id, other.id)
    with user:
        open_bulk_edit_sheet(ctx, [split.id, other.id])
    await user.should_see("1 row is split by its items: only the rest in its own category changes")
