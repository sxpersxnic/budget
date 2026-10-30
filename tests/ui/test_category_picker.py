"""The category picker (F08): everyday names, search words, recently used categories first; COICOP notes (F40).

The simulated client runs no JavaScript, so `offered` applies the browser's filter (`category_select.js`):
every typed word must occur in the option's `search` text or its `note_search` words, which the page sends
with each option; matches by name come first.
"""

from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app import i18n
from budget_app.config import RESOURCES_DIR
from budget_app.domain import coicop
from budget_app.domain.coicop import TAX, fold
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.selects import CategorySelect


def pickers(user: User, label: str = "Category") -> list[CategorySelect]:
    with user:
        found = [
            e for e in user.client.elements.values() if isinstance(e, CategorySelect) and e.props.get("label") == label
        ]
    return sorted(found, key=lambda e: e.id)


def picker(user: User, label: str = "Category") -> CategorySelect:
    found = pickers(user, label)
    assert len(found) == 1, f"expected one category picker, found {len(found)}"
    return found[0]


def options(sel: CategorySelect) -> list[dict]:
    return [o | {"code": sel._values[o["value"]]} for o in sel.props["options"]]


def offered(sel: CategorySelect, typed: str) -> list[str]:
    """The codes the dropdown keeps when the user types `typed` (the browser filter, applied here): the options
    whose `search` text has every word first, then those that need their `note_search` words (F40)."""
    words = fold(typed).split()
    by_name, by_notes = [], []
    for o in options(sel):
        text, more = o.get("search", fold(o["label"])), o.get("note_search", "")
        if all(w in text for w in words):
            by_name.append(o["code"])
        elif all(w in text or w in more for w in words):
            by_notes.append(o["code"])
    return by_name + by_notes


def spend(ctx, setup, day: date, category: str) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=day, type="expenditure", amount=20, currency="CHF", category=category, from_account=setup["chf"].id
        ),
    )


async def test_typing_a_shop_or_brand_offers_its_category(user: User, setup) -> None:
    await user.open("/add-transaction")
    sel = picker(user)
    assert offered(sel, "migros")[0] == "01.1"
    assert offered(sel, "Netflix") == ["08.3.9.2"]
    assert "07.3.1" in offered(sel, "SBB")
    assert offered(sel, "krankenkasse") == ["12.1.2"]
    assert offered(sel, "steuern") == [TAX]
    assert "01.1.1" in offered(sel, "céréal")  # official names, without accents
    assert offered(sel, "")[:2] == ["01", "01.1"]  # nothing typed: everything, in COICOP order


async def test_options_show_the_everyday_name_code_and_official_name(user: User, setup) -> None:
    await user.open("/add-transaction")
    sel = picker(user)
    by_code = {o["code"]: o for o in options(sel)}
    assert by_code["01.1"]["label"] == "01.1 Groceries" and by_code["01.1"]["caption"] == "Food"
    assert by_code["01.1.1"]["label"] == "01.1.1 Bread and cereals"
    assert by_code["01.1.1"]["caption"] == "Cereals and cereal products" and by_code["01.1.1"]["level"] == 3
    assert by_code["01.1.1.1"]["label"] == "01.1.1.1 Cereals" and by_code["01.1.1.1"]["caption"] == ""  # no other name
    assert sel.props["recent-title"] == "Recently used"
    assert sel.props["use-input"] is True
    assert type(sel).component.path == RESOURCES_DIR / "category_select.js"


async def test_choosing_an_everyday_name_saves_the_coicop_code(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    with user:
        field = next(
            e for e in user.client.elements.values() if isinstance(e, ui.number) and e.props["label"] == "Amount"
        )
        field.value = 12.5
        account = next(
            e for e in user.client.elements.values() if isinstance(e, ui.select) and e.props["label"] == "From Account"
        )
        account.value = setup["chf"].id
    sel = picker(user)
    UserInteraction(user, {sel}, None).click()  # opens the dropdown
    UserInteraction(user, {sel}, "01.1 Groceries").click()  # picks the option by its label
    assert sel.value == "01.1"
    user.find("Add Transaction").click()
    await user.should_see("Transaction added")
    assert [tx.category for tx in ctx.store.transactions.all()] == ["01.1"]


async def test_recent_categories_come_first(user: User, ctx, setup) -> None:
    spend(ctx, setup, date(2026, 10, 1), "04.1")
    spend(ctx, setup, date(2026, 10, 5), "11.1.1")
    spend(ctx, setup, date(2026, 10, 3), "04.1")
    await user.open("/add-transaction")
    first = options(picker(user))[:3]
    assert [(o["code"], o["recent"]) for o in first] == [("11.1.1", True), ("04.1", True), ("01", False)]
    assert [o["code"] for o in options(picker(user))].count("04.1") == 1
    assert offered(picker(user), "migros")[0] == "11.1.1"  # a recent match is offered first


async def test_budget_forms_use_the_picker_with_recents(user: User, ctx, demo) -> None:
    spend(ctx, demo, date(2026, 10, 2), "09.4.6.2")
    await user.open("/add-budget")
    assert options(picker(user))[0]["code"] == "09.4.6.2"
    assert "08.3.9.2" in offered(picker(user), "spotify")

    await user.open("/budget-entries")
    entry = next(b for b in ctx.store.budget.all() if b.category == "01.1")
    with user:
        grid = next(e for e in user.client.elements.values() if isinstance(e, ui.aggrid))
    UserInteraction(user, {grid}, None).trigger("rowDoubleClicked", {"data": {"id": entry.id}})
    sel = pickers(user)[-1]  # the edit sheet's
    assert sel.value == "01.1"
    assert options(sel)[0]["code"] == "09.4.6.2"
    assert "07.3.1" in offered(sel, "sbb")


async def test_typing_what_a_category_includes_offers_it(user: User, setup) -> None:
    """F40: the notes' words are searched after the names; what a category excludes is not."""
    await user.open("/add-transaction")
    sel = picker(user)
    assert offered(sel, "teff") == ["01.1.1.1"]  # only in the UN's notes
    bread = offered(sel, "bread")
    assert bread[:2] == ["01.1.1", "01.1.1.3"] and len(bread) > 2  # names first, then categories whose notes say it
    assert "07.3.6" in offered(sel, "porters") and "07.3.1" not in offered(sel, "funicular")  # 07.3.1 excludes it
    by_code = {o["code"]: o for o in options(sel)}
    assert "teff" in by_code["01.1.1.1"]["note_search"] and "teff" not in by_code["01.1.1.1"]["search"]
    assert "planting" not in by_code["01.1.1.1"]["note_search"]  # excludes: "seeds for planting"


async def test_the_picker_loads_the_notes_once_for_hovering(user: User, setup) -> None:
    await user.open("/add-transaction")
    sel = picker(user)
    assert sel.props["notes-url"] == f"/coicop-notes/en?v={coicop.notes_version('en')}"
    assert sel.props["notes-labels"] == {
        "includes": "Includes",
        "also_includes": "Also includes",
        "excludes": "Excludes",
        "unofficial": "Unofficial translation",
        "show": "Show what this category includes",
    }
    response = await user.http_client.get(sel.props["notes-url"])
    assert response.status_code == 200 and "immutable" in response.headers["cache-control"]
    data = response.json()
    assert data["translated"] is False and data["notes"]["01.1.1.1"]["includes"][0] == "wheat"
    german = (await user.http_client.get("/coicop-notes/de")).json()
    assert german["translated"] is True and german["notes"]["01.1.1.1"]["includes"][0] == "Weizen"
    unknown = (await user.http_client.get("/coicop-notes/xx")).json()
    assert unknown == data  # an unknown language gets English


async def test_german_names_when_the_language_is_german(user: User, setup) -> None:
    i18n.set_language("de")
    try:
        await user.open("/add-transaction")
        sel = picker(user, "Kategorie")
        by_code = {o["code"]: o for o in options(sel)}
        assert by_code["01.1"]["label"] == "01.1 Lebensmittel"
        assert by_code["01.1"]["caption"] == "Nahrungsmittel"
        assert by_code["12.1.2"]["label"] == "12.1.2 Krankenkasse"
        assert by_code[TAX]["label"] == "Steuern"
        assert sel.props["recent-title"] == "Kürzlich verwendet"
        assert offered(sel, "lebensmittel")[0] == "01.1"
        assert offered(sel, "Öffentlicher Verkehr")[0] == "07.3"
        assert offered(sel, "hirse") == ["01.1.1.1"]  # the German notes
        assert sel.props["notes-url"].startswith("/coicop-notes/de?v=")
        assert sel.props["notes-labels"]["unofficial"] == "Inoffizielle Übersetzung"
    finally:
        i18n.set_language("en")
