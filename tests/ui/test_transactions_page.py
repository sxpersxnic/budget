"""The transactions page: filters, search, selection, edit and delete; plus the shared grid helpers."""

import asyncio
import re
from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app import i18n
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets import grid as grid_mod

SELECTED = re.compile(r".*getSelectedRows")


# ------------------------------------------------------------------ helpers
def field(user: User, kind: type, label: str):
    found = [e for e in user.find(kind=kind).elements if e.props.get("label") == label]
    assert len(found) == 1, f"expected one {kind.__name__} labelled {label!r}, found {len(found)}"
    return found[0]


def type_filter_shown(user: User) -> bool:
    try:
        return any(e.props.get("label") == "Type" for e in user.find(kind=ui.select).elements)
    except AssertionError:  # no visible select at all
        return False


def put(user: User, element, value) -> None:
    with user:
        element.value = value


def button(user: User, text: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.text == text]
    assert found, f"no visible button {text!r}"
    return max(found, key=lambda b: b.id)  # the newest: page actions after the sidebar, sheets after the page


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def the_grid(user: User) -> ui.aggrid:
    [grid] = user.find(kind=ui.aggrid).elements
    return grid


def rows(user: User) -> list[dict]:
    return the_grid(user).options["rowData"]


async def settle() -> None:
    """Let the async click handlers (they ask the browser for the selection) finish."""
    for _ in range(5):
        await asyncio.sleep(0.01)


def show_filters(user: User) -> None:
    click(user, button(user, "Filters"))


async def select(user: User, *ids: str) -> None:
    """Select grid rows the way the browser reports them."""
    user.javascript_rules[SELECTED] = lambda _: [{"id": i} for i in ids]
    UserInteraction(user, {the_grid(user)}, None).trigger("selectionChanged")
    await user.should_see(f"{len(ids)} selected")


@pytest.fixture
def shop(ctx, setup):
    """A grocery receipt with items, an exchange and an advance with its repayment, besides the demo data."""
    receipt = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 3),
            amount=23.4,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            comment="Saturday market",
        ),
    )
    items_svc.replace(ctx, receipt.id, [ItemInput("Sourdough", 1, 8.4), ItemInput("Goat cheese", 2, 7.5)])
    exchange = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 16),
            type="exchange",
            amount=100,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=110.5,
        ),
    )
    advance = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 12),
            amount=90,
            currency="EUR",
            category="11.1",
            from_account=setup["eur"].id,
            is_advance=True,
            advance_mode="percent",
            advance_value=50,
            debtor="Clara",
        ),
    )
    repay = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 14),
            type="income",
            amount=45,
            currency="EUR",
            to_account=setup["eur"].id,
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    return {"receipt": receipt, "exchange": exchange, "advance": advance, "repay": repay}


# ------------------------------------------------------------------ listing
async def test_empty_list(user: User) -> None:
    await user.open("/transactions")
    await user.should_see("0 transactions")
    assert rows(user) == []


async def test_rows_show_category_accounts_and_advances(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    total = len(ctx.store.transactions.all())
    await user.should_see(f"{total} transactions")
    by_id = {r["id"]: r for r in rows(user)}

    receipt = by_id[shop["receipt"].id]
    assert receipt["category"] == "01.1 Food" and receipt["from"] == "Bank (CHF)"
    assert receipt["items"] == 2 and receipt["type"] == "Expenditure" and receipt["comment"] == "Saturday market"
    exchange = by_id[shop["exchange"].id]
    assert exchange["category"] == "Currency exchange" and exchange["to"] == "Euro account (EUR) (+110.50 EUR)"
    assert exchange["items"] is None
    assert by_id[shop["advance"].id]["advance"] == "45.00 · Clara"
    repay = by_id[shop["repay"].id]
    assert repay["advance"] == "repaid by Clara" and repay["category"] == "Advance repayments"
    salary = next(r for r in rows(user) if r["_type"] == "income" and not r["advance"])
    assert salary["category"] == "Salary" and salary["to"] == "Bank (CHF)"


# ------------------------------------------------------------------ filters
async def test_filters_narrow_the_list(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    total = len(ctx.store.transactions.all())
    assert not type_filter_shown(user)
    show_filters(user)

    put(user, field(user, ui.select, "Type"), "income")
    assert {r["_type"] for r in rows(user)} == {"income"}
    await user.should_see(f"{len(rows(user))} of {total} transactions")
    put(user, field(user, ui.select, "Type"), None)

    put(user, field(user, ui.select, "Account"), demo["eur"].id)
    spread = shop["exchange"].linked_id  # the exchange's spread booking (F44) is on the EUR account too
    expected = {shop["exchange"].id, shop["advance"].id, shop["repay"].id} | ({spread} if spread else set())
    assert {r["id"] for r in rows(user)} == expected
    put(user, field(user, ui.select, "Account"), None)

    put(user, field(user, ui.input, "From"), "2026-10-03")
    put(user, field(user, ui.input, "To"), "2026-10-14")
    assert sorted(r["date"] for r in rows(user)) == ["2026-10-03", "2026-10-12", "2026-10-14"]
    put(user, field(user, ui.number, "Min amount"), 40)
    put(user, field(user, ui.number, "Max amount"), 60)
    assert [r["id"] for r in rows(user)] == [shop["repay"].id]
    put(user, field(user, ui.number, "Min amount"), 1000)
    assert rows(user) == []
    await user.should_see(f"0 of {total} transactions")

    for label in ("Min amount", "Max amount"):
        put(user, field(user, ui.number, label), None)
    for label in ("From", "To"):
        put(user, field(user, ui.input, label), "")
    await user.should_see(f"{total} transactions")

    show_filters(user)  # the button toggles the filter row
    assert not type_filter_shown(user)


async def test_search_finds_text_debtors_and_item_names(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    search = next(e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "Search")
    put(user, search, "goat")
    assert [r["id"] for r in rows(user)] == [shop["receipt"].id]  # found by what was bought
    put(user, search, "  CLARA ")
    assert {r["id"] for r in rows(user)} == {shop["advance"].id, shop["repay"].id}
    put(user, search, "market")
    assert [r["id"] for r in rows(user)] == [shop["receipt"].id]
    put(user, search, "no such thing")
    assert rows(user) == []
    put(user, search, None)
    assert len(rows(user)) == len(ctx.store.transactions.all())


async def test_the_grid_sorts_by_date_with_every_column_sortable(user: User, demo) -> None:
    await user.open("/transactions")
    options = the_grid(user).options
    date_col = next(c for c in options["columnDefs"] if c["field"] == "date")
    assert date_col["sort"] == "desc" and options["defaultColDef"]["sortable"]
    assert options["rowSelection"]["mode"] == "multiRow"


# ------------------------------------------------------------------ selection
async def test_delete_selected_after_confirmation(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    before = len(ctx.store.transactions.all())
    await select(user, shop["receipt"].id, shop["exchange"].id)

    click(user, button(user, "Delete"))
    await user.should_see("Delete 2 transactions?")
    await user.should_see("This can’t be undone.")
    click(user, button(user, "Cancel"))
    assert len(ctx.store.transactions.all()) == before

    click(user, button(user, "Delete"))
    click(user, button(user, "Delete"))  # the sheet's button is the newest
    gone = 3 if shop["exchange"].linked_id else 2  # the exchange's spread booking goes with it (F44)
    assert notified(user, f"Deleted {gone} transactions")
    assert ctx.store.transactions.get(shop["receipt"].id) is None
    assert items_svc.items_for(ctx, shop["receipt"].id) == []  # the receipt's items go with it
    assert len(rows(user)) == before - gone
    await user.should_see(f"{before - gone} transactions")


async def test_an_advance_with_repayments_cannot_be_deleted_alone(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    await select(user, shop["advance"].id)
    click(user, button(user, "Delete"))
    await user.should_see("Delete 1 transaction?")
    click(user, button(user, "Delete"))
    assert notified(user, "Some selected advance payments have repayments")
    assert ctx.store.transactions.get(shop["advance"].id) is not None


async def test_delete_without_a_selection_does_nothing(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    await select(user, shop["receipt"].id)
    user.javascript_rules[SELECTED] = lambda _: []  # deselected in the meantime
    click(user, button(user, "Delete"))
    await settle()
    await user.should_not_see("Delete 0 transactions?")
    assert ctx.store.transactions.get(shop["receipt"].id) is not None


async def test_edit_needs_exactly_one_selected_row(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    await select(user, shop["receipt"].id, shop["exchange"].id)
    click(user, button(user, "Edit"))
    await settle()
    await user.should_not_see("Edit Transaction")
    assert notified(user, "Select exactly one transaction to edit")

    await select(user, shop["receipt"].id)
    click(user, button(user, "Edit"))
    await user.should_see("Edit Transaction")
    put(user, field(user, ui.number, "Amount"), 25.9)
    click(user, button(user, "Save Changes"))
    assert ctx.store.transactions.get(shop["receipt"].id).amount == 25.9
    assert next(r for r in rows(user) if r["id"] == shop["receipt"].id)["amount"] == 25.9  # the list refreshed


async def test_double_click_opens_the_editor(user: User, ctx, demo, shop) -> None:
    await user.open("/transactions")
    grid = the_grid(user)
    UserInteraction(user, {grid}, None).trigger("rowDoubleClicked", {"data": {"id": "gone"}})
    await user.should_not_see("Edit Transaction", retries=1)  # a row deleted elsewhere opens nothing
    UserInteraction(user, {grid}, None).trigger("rowDoubleClicked", {"data": {"id": shop["exchange"].id}})
    await user.should_see("Edit Transaction")
    assert field(user, ui.number, "Amount Received").value == 110.5


async def test_new_transaction_from_the_page_refreshes_the_list(user: User, ctx, setup) -> None:
    await user.open("/transactions")
    await user.should_see("0 transactions")
    click(user, button(user, "New Transaction"))
    await user.should_see("Add Transaction")
    put(user, field(user, ui.number, "Amount"), 12)
    put(user, field(user, ui.select, "Category"), "01.1")
    put(user, field(user, ui.select, "From Account"), setup["chf"].id)
    click(user, button(user, "Add Transaction"))
    await user.should_see("1 transaction")
    assert len(rows(user)) == 1


async def test_import_button_opens_the_import_page(user: User) -> None:
    await user.open("/transactions")
    click(user, button(user, "Import…"))
    await user.should_see("Import Transactions")


# ------------------------------------------------------------------ grid helpers
def test_column_helpers_format_in_the_browser() -> None:
    month = grid_mod.month_col("month", "Month", width=90)
    assert month["width"] == 90 and "-01T00:00" in month[":valueFormatter"]
    money = grid_mod.money_col("amount", "Amount")
    assert money["type"] == "rightAligned" and "p.data.currency" in money[":valueFormatter"]
    assert money["cellClassRules"] == {"amt-in": "data._type === 'income'"}
    assert "toLocaleDateString" in grid_mod.date_col("d", "Date")[":valueFormatter"]


async def test_the_grid_speaks_german(user: User) -> None:
    await user.open("/transactions")
    try:
        i18n.set_language("de")
        with user:
            grid = grid_mod.make_grid([grid_mod.date_col("date", "Datum")])
    finally:
        i18n.set_language("en")
    assert grid.options["localeText"]["noRowsToShow"] == "Keine Einträge"
    assert "Nothing matches" not in grid.options["overlayNoRowsTemplate"]


async def test_confirm_with_a_custom_action(user: User) -> None:
    await user.open("/transactions")
    done = []
    with user:
        grid_mod.confirm("Archive everything?", lambda: done.append(True), action="Archive")
    await user.should_see("Archive everything?")
    click(user, button(user, "Archive"))
    assert done == [True]
