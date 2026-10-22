"""Tags in the interface (F10): the Tags page (add, edit, merge, delete, budgets), the Tags field of the transaction
form, the recurring sheet and bulk edit, the Transactions page's column, filter and search, and the chart."""

from datetime import date

import pytest
from nicegui import ui
from nicegui.events import GenericEventArguments
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_bulk_edit_ui import select
from test_faster_entry_ui import menu_items
from test_overview_page import card, menu_item, open_overview, traces, values
from test_payees_page import checkboxes, click, icon_buttons, newest, put
from test_transactions_page import rows as grid_rows
from test_transactions_page import show_filters

from budget_app.domain.models import Tag
from budget_app.services import tags as tag_svc
from budget_app.services import templates as tpl_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.widgets.bulk_edit import open_bulk_edit_sheet
from budget_app.ui.widgets.tags import ColourPicker, TagSelect, tag_select


def spend(setup, amount=20.0, tags=(), **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        type="expenditure",
        amount=amount,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
        tags=list(tags),
    )
    return TransactionInput(**(base | kw))


def swatch_button(user: User, colour: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.props.get("aria-label") == colour]
    return max(found, key=lambda b: b.id)  # the open sheet's


def type_new(user: User, field: TagSelect, name: str) -> None:
    """Type a new name into a tag field and press Enter, the way Quasar hands it over."""
    with user:
        field.set_value([*field.value, field._handle_new_value(name)])


@pytest.fixture
def tagged(ctx, setup):
    """Holiday (with a budget for September) on two expenditures, Work on income, Unused on nothing."""
    holiday = tag_svc.add(ctx, "Holiday", "green")
    work = tag_svc.add(ctx, "Work", "blue")
    unused = tag_svc.add(ctx, "Unused")
    tag_svc.set_budget(ctx, holiday.id, 100, "CHF", date(2026, 9, 1), date(2026, 9, 30))
    hotel = tx_svc.save(ctx, spend(setup, 80, [holiday.id], payee="Hotel"))
    train = tx_svc.save(ctx, spend(setup, 45, [holiday.id, work.id], category="07.3"))
    plain = tx_svc.save(ctx, spend(setup, 12))
    pay = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type="income",
            amount=300,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            tags=[work.id],
        ),
    )
    return setup | {
        "holiday": holiday,
        "work": work,
        "unused": unused,
        "hotel": hotel,
        "train": train,
        "plain": plain,
        "pay": pay,
    }


# ------------------------------------------------------------------ the Tags page
async def test_the_page_lists_tags_with_totals_and_budgets(user: User, ctx, setup) -> None:
    await user.open("/tags")
    await user.should_see("No tags yet. Add one here or on a transaction.")
    assert ("/tags", "Tags", "o_sell") in layout.NAV
    assert [p for p, _, _ in layout.NAV].index("/tags") == [p for p, _, _ in layout.NAV].index("/payees") + 1


async def test_tags_with_their_numbers(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    await user.should_see("3 tags")
    await user.should_see("2 transactions · spent 125.00 CHF")
    await user.should_see("2 transactions · spent 45.00 CHF · received 300.00 CHF")
    await user.should_see("No transactions")
    await user.should_see("125.00 of 100.00 CHF · 25.00 CHF over budget · 1 Sep 2026 – 30 Sep 2026")
    over = next(lb for lb in user.find(kind=ui.label).elements if "over budget" in lb.text)
    assert "text-neg" in over.classes
    chips = sorted((lb for lb in user.find(kind=ui.label).elements if "tag-chip" in lb.classes), key=lambda e: e.id)
    assert [(c.text, c.classes[-2]) for c in chips[:2]] == [("Holiday", "tag-green"), ("Unused", "tag-grey")]

    put(user, newest(user, ui.input), "WOR")
    await user.should_see("1 of 3 tags")
    await user.should_not_see("Holiday")
    put(user, newest(user, ui.input), "nothing")
    await user.should_see("No tag matches.")


async def test_a_chip_opens_the_transactions_with_that_tag(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    chip = next(lb for lb in user.find(kind=ui.label).elements if lb.text == "Holiday" and "tag-chip" in lb.classes)
    UserInteraction(user, {chip}, None).trigger("click")
    await user.should_see("Filters")  # the Transactions page
    assert {r["id"] for r in grid_rows(user)} == {tagged["hotel"].id, tagged["train"].id}
    assert newest(user, TagSelect, "Tags").value == [tagged["holiday"].id]


async def test_add_and_edit_a_tag(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    click(user, newest(user, ui.button, text="New Tag"))
    name = newest(user, ui.input, "Name")
    put(user, name, "holiday")
    click(user, newest(user, ui.button, text="Add Tag"))
    assert notified(user, "A tag called “Holiday” already exists")
    put(user, name, "Renovation")
    swatch = swatch_button(user, "Orange")
    click(user, swatch)
    assert "selected" in swatch.classes
    UserInteraction(user, {name}, None).trigger("keydown.enter")
    assert notified(user, "Saved “Renovation”")
    renovation = tag_svc.find(ctx, "Renovation")
    assert renovation.colour == "orange"
    await user.should_see("4 tags")

    click(user, icon_buttons(user, "o_edit")[0])  # Holiday
    await user.should_see("Transactions keep the tag; only its name and colour change.")
    put(user, newest(user, ui.input, "Name"), "Holiday Italy")
    click(user, swatch_button(user, "Pink"))
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Saved “Holiday Italy”")
    holiday = ctx.store.tags.get(tagged["holiday"].id)
    assert (holiday.name, holiday.colour) == ("Holiday Italy", "pink")
    assert undo_svc.latest(ctx).label == "Edit a tag"  # name and colour: one step

    click(user, icon_buttons(user, "o_edit")[0])
    put(user, newest(user, ui.input, "Name"), "Work")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "A tag called “Work” already exists")


async def test_merge_selected_tags(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    boxes = checkboxes(user)  # Holiday, Unused, Work
    click(user, boxes[0])
    assert not newest(user, ui.button, text="Merge…").enabled
    click(user, boxes[2])
    await user.should_see("2 selected")
    click(user, newest(user, ui.button, text="Merge…"))
    keep = newest(user, ui.radio)
    assert keep.value == tagged["work"].id  # the most used; between equals the shorter name
    put(user, keep, tagged["work"].id)
    click(user, newest(user, ui.button, text="Merge"))
    assert notified(user, "Merged 2 tags into “Work”")
    assert ctx.store.transactions.get(tagged["hotel"].id).tags == tagged["work"].id
    assert ctx.store.transactions.get(tagged["train"].id).tags == tagged["work"].id
    await user.should_see("2 tags")

    click(user, checkboxes(user)[0])
    click(user, checkboxes(user)[1])
    click(user, newest(user, ui.button, text="Merge…"))
    ctx.store.tags.delete([tagged["unused"].id])  # gone in the meantime
    click(user, newest(user, ui.button, text="Merge"))
    assert notified(user, "Select at least two tags to merge")
    click(user, newest(user, ui.button, text="Clear Selection"))
    await user.should_not_see("2 selected")
    click(user, checkboxes(user)[0])
    click(user, checkboxes(user)[0])
    await user.should_not_see("1 selected")


async def test_delete_takes_the_tag_off_its_transactions(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    click(user, icon_buttons(user, "o_delete")[0])  # Holiday
    await user.should_see("Delete “Holiday”?")
    await user.should_see("2 transactions lose this tag; nothing else changes.")
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "Deleted Holiday")
    await user.should_see("2 tags")
    assert ctx.store.transactions.get(tagged["hotel"].id).tags == ""
    assert ctx.store.transactions.get(tagged["train"].id).tags == tagged["work"].id

    click(user, icon_buttons(user, "o_delete")[0])  # Unused: no detail
    await user.should_not_see("lose this tag")
    ctx.store.tags.delete([tagged["unused"].id])
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "Tag not found")


async def test_set_change_and_remove_a_budget(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    savings = icon_buttons(user, "o_savings")
    click(user, savings[2])  # Work
    await user.should_see("Budget for “Work”")
    put(user, newest(user, ui.number, "Amount"), 500)
    put(user, newest(user, ui.input, "Until"), "2026-08-31")
    put(user, newest(user, ui.input, "From"), "2026-09-01")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "The end must not be before the start")
    put(user, newest(user, ui.input, "Until"), "")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Budget saved")
    work = ctx.store.tags.get(tagged["work"].id)
    assert (work.budget, work.budget_currency, work.budget_start, work.budget_end) == (500, "CHF", "2026-09-01", "")
    await user.should_see("45.00 of 500.00 CHF · 455.00 CHF left · from 1 Sep 2026")

    click(user, icon_buttons(user, "o_savings")[0])  # Holiday has one: it can be removed
    click(user, newest(user, ui.button, text="Remove Budget"))
    assert notified(user, "Budget removed")
    assert not ctx.store.tags.get(tagged["holiday"].id).has_budget

    tag_svc.set_budget(ctx, tagged["unused"].id, 50, "CHF", end=date(2026, 12, 31))
    await user.open("/tags")
    await user.should_see("0.00 of 50.00 CHF · 50.00 CHF left · until 31 Dec 2026")
    click(user, icon_buttons(user, "o_savings")[1])  # Unused
    put(user, newest(user, ui.number, "Amount"), None)
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Budget removed")
    tag_svc.set_budget(ctx, tagged["unused"].id, 50, "CHF")
    await user.open("/tags")
    await user.should_see("0.00 of 50.00 CHF · 50.00 CHF left · All time")


# ------------------------------------------------------------------ the transaction form
async def test_the_form_picks_tags_and_creates_new_ones_on_save(user: User, ctx, tagged) -> None:
    await user.open("/add-transaction")
    field = newest(user, TagSelect, "Tags")
    assert field.options == {
        tagged["holiday"].id: "Holiday",
        tagged["unused"].id: "Unused",
        tagged["work"].id: "Work",
    }
    kinds = newest(user, ui.toggle)  # the type; a revaluation adds a toggle of its own
    for kind in ("income", "transfer", "exchange", "revaluation", "expenditure"):
        put(user, kinds, kind)
        assert field.visible  # every type has tags
    put(user, newest(user, ui.number, "Amount"), 9.5)
    put(user, newest(user, ui.select, "Category"), "01.1")
    put(user, newest(user, ui.select, "From Account"), tagged["chf"].id)
    put(user, field, [tagged["work"].id])
    type_new(user, field, "Lunch")
    model = field._value_to_model_value(field.value)
    assert [(m["label"], m["colour"]) for m in model] == [("Work", "blue"), ("Lunch", "new")]
    assert tag_svc.find(ctx, "Lunch") is None  # not before saving
    click(user, newest(user, ui.button, text="Add Transaction"))
    assert notified(user, "Transaction added")
    lunch = tag_svc.find(ctx, "Lunch")
    saved = next(tx for tx in ctx.store.transactions.all() if tx.amount == 9.5)
    assert saved.tags == f"{tagged['work'].id}|{lunch.id}"
    assert field.value == [] and lunch.id in field.options  # reset, and knows the new tag
    assert {o["colour"] for o in field.props["options"]} == {"green", "grey", "blue"}


async def test_editing_shows_the_tags_and_templates_carry_them(user: User, ctx, tagged) -> None:
    tpl_svc.save(ctx, "Train", spend(tagged, 45, [tagged["holiday"].id], category="07.3"))
    await user.open("/add-transaction")
    train = next(m for m in menu_items(user) if "Train" in [getattr(e, "text", "") for e in m.descendants()])
    click(user, train)
    assert newest(user, TagSelect, "Tags").value == [tagged["holiday"].id]

    await user.open("/transactions")
    [grid] = user.find(kind=ui.aggrid).elements
    UserInteraction(user, {grid}, None).trigger("rowDoubleClicked", {"data": {"id": tagged["train"].id}})
    await user.should_see("Edit Transaction")
    field = newest(user, TagSelect, "Tags")
    assert field.value == [tagged["holiday"].id, tagged["work"].id]
    put(user, field, [tagged["work"].id])
    click(user, newest(user, ui.button, text="Save Changes"))
    assert notified(user, "Changes saved")
    assert ctx.store.transactions.get(tagged["train"].id).tags == tagged["work"].id


async def test_the_recurring_sheet_has_tags(user: User, ctx, tagged) -> None:
    await user.open("/recurring")
    click(user, newest(user, ui.button, text="New Recurring Transaction"))
    put(user, newest(user, ui.number, "Amount"), 49.9)
    put(user, newest(user, ui.select, "Category"), "08.3")
    put(user, newest(user, ui.select, "From Account"), tagged["chf"].id)
    type_new(user, newest(user, TagSelect, "Tags"), "Phone")
    click(user, newest(user, ui.button, text="Add Recurring Transaction"))
    assert notified(user, "Recurring transaction added")
    [series] = ctx.store.recurring.all()
    assert series.tags == tag_svc.find(ctx, "Phone").id


# ------------------------------------------------------------------ bulk edit
async def test_bulk_edit_adds_and_removes_tags(user: User, ctx, tagged) -> None:
    await user.open("/transactions")
    await select(user, tagged["hotel"].id, tagged["train"].id, tagged["plain"].id)
    click(user, newest(user, ui.button, text="Edit"))
    await user.should_see("Edit 3 Transactions")
    add, remove = newest(user, TagSelect, "Add Tags"), newest(user, TagSelect, "Remove Tags")
    assert set(remove.options) == {tagged["holiday"].id, tagged["work"].id}  # the tags the rows have
    type_new(user, add, "Italy")
    put(user, remove, [tagged["holiday"].id])
    await user.should_see("Changes 3 transactions")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Changed 3 transactions")
    italy = tag_svc.find(ctx, "Italy")
    assert ctx.store.transactions.get(tagged["hotel"].id).tags == italy.id
    assert ctx.store.transactions.get(tagged["train"].id).tags == f"{tagged['work'].id}|{italy.id}"
    assert ctx.store.transactions.get(tagged["plain"].id).tags == italy.id


async def test_bulk_edit_without_tags_and_with_a_spread(user: User, ctx, tagged) -> None:
    exchange = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 16),
            type="exchange",
            amount=100,
            currency="CHF",
            from_account=tagged["chf"].id,
            to_account=tagged["eur"].id,
            to_amount=110.5,
        ),
    )
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [tagged["plain"].id, exchange.id, exchange.linked_id])
    await user.should_see("1 of 3 rows is a spread booking or booked on the Investments page and keeps its tags")
    assert not sheet.remove_tags.enabled  # no row has a tag yet
    with user:
        sheet = open_bulk_edit_sheet(ctx, [exchange.linked_id])
    assert sheet.add_tags is None and sheet.remove_tags is None


# ------------------------------------------------------------------ the Transactions page
async def test_transactions_show_filter_and_search_tags(user: User, ctx, tagged) -> None:
    await user.open("/transactions")
    by_id = {r["id"]: r for r in grid_rows(user)}
    assert by_id[tagged["train"].id]["tags"] == "Holiday, Work"
    assert by_id[tagged["train"].id]["_tags"] == [["Holiday", "green"], ["Work", "blue"]]
    assert by_id[tagged["plain"].id]["_tags"] == []
    [grid] = user.find(kind=ui.aggrid).elements
    column = next(c for c in grid.options["columnDefs"] if c["field"] == "tags")
    assert column["headerName"] == "Tags" and "createElement" in column[":cellRenderer"]

    search = next(i for i in user.find(kind=ui.input).elements if i.props.get("placeholder") == "Search")
    put(user, search, "holiday")
    assert {r["id"] for r in grid_rows(user)} == {tagged["hotel"].id, tagged["train"].id}
    put(user, search, "")

    show_filters(user)
    field = newest(user, TagSelect, "Tags")
    put(user, field, [tagged["work"].id])
    assert {r["id"] for r in grid_rows(user)} == {tagged["train"].id, tagged["pay"].id}
    put(user, field, [tagged["work"].id, tagged["holiday"].id])  # any of them
    assert len(grid_rows(user)) == 3
    await user.should_see("3 of 4 transactions")

    tag_svc.delete(ctx, [tagged["work"].id])  # elsewhere; the filter forgets it on the next refresh
    with user:
        layout.data_changed()
    assert field.value == [tagged["holiday"].id]
    assert tagged["work"].id not in field.options


async def test_an_unknown_tag_in_the_address_shows_everything(user: User, ctx, tagged) -> None:
    await user.open("/transactions?tag=nothing")
    assert len(grid_rows(user)) == 4
    await user.open(f"/transactions?tag={tagged['work'].id}")
    assert {r["id"] for r in grid_rows(user)} == {tagged["train"].id, tagged["pay"].id}


# ------------------------------------------------------------------ the chart
async def test_the_spending_by_tag_chart(user: User, ctx, tagged) -> None:
    await open_overview(user)
    await menu_item(user, "Spending by Tag")
    chart = card(user, "Spending by Tag")
    [bars] = traces(user, chart)
    assert bars["x"] == ["Holiday", "Work"]  # most spent first; tags without spending left out
    assert values(bars["y"]) == [125, 45]
    assert bars["marker"]["color"] == ["#30b94d", "#0a84ff"]


async def test_the_chart_without_tagged_spending(user: User, ctx, setup) -> None:
    await open_overview(user)
    await menu_item(user, "Spending by Tag")
    chart = card(user, "Spending by Tag")
    assert traces(user, chart) == []
    await user.should_see("Spending by Tag")


# ------------------------------------------------------------------ widgets
async def test_the_widgets(user: User, ctx, tagged) -> None:
    await user.open("/tags")
    with user:
        picker = ColourPicker("gold")
        field = tag_select(ctx, [tagged["work"].id, "gone"], label="Only", allow_new=False, only=[tagged["work"].id])
    assert picker.value == "grey"
    assert field.options == {tagged["work"].id: "Work"} and field.value == [tagged["work"].id]
    assert "new-value-mode" not in field.props
    assert Tag("x").colour == "grey"


async def test_typing_the_name_of_a_tag_picks_it(user: User, ctx, tagged) -> None:
    await user.open("/add-transaction")
    field = newest(user, TagSelect, "Tags")
    labels = list(field.options.values())
    work = {"value": labels.index("Work"), "label": "Work"}

    def browser_sends(*args) -> list:
        with user:
            return field._event_args_to_value(GenericEventArguments(sender=field, client=field.client, args=list(args)))

    assert browser_sends(work, "work") == [tagged["work"].id]  # once, ignoring case
    assert browser_sends("HOLIDAY", "Lunch") == [tagged["holiday"].id, "Lunch"]
    assert field.options["Lunch"] == "Lunch"  # a new name until saved
    assert browser_sends() == []


async def test_the_bulk_placeholder_goes_once_a_tag_is_chosen(user: User, ctx, tagged) -> None:
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [tagged["hotel"].id, tagged["plain"].id])
    assert "placeholder" in sheet.add_tags.props
    put(user, sheet.add_tags, [tagged["work"].id])
    assert "placeholder" not in sheet.add_tags.props
    put(user, sheet.add_tags, [])
    assert "placeholder" in sheet.add_tags.props
