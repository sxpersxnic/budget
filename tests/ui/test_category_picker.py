"""The category picker (F08): everyday names, search words, recently used categories first.

The simulated client runs no JavaScript, so `offered` applies the browser's filter (`category_select.js`):
every typed word must occur in the option's `search` text, which the page sends with each option.
"""

from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app import i18n
from budget_app.config import RESOURCES_DIR
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
    """The codes the dropdown keeps when the user types `typed` (the browser filter, applied here)."""
    words = fold(typed).split()
    return [o["code"] for o in options(sel) if all(w in o.get("search", fold(o["label"])) for w in words)]


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
    assert by_code["01.1"]["label"] == "Groceries · 01.1" and by_code["01.1"]["caption"] == "Food"
    assert by_code["01.1.1"]["label"] == "01.1.1 Cereals and cereal products" and by_code["01.1.1"]["caption"] == ""
    assert by_code["01.1.1"]["level"] == 3
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
    UserInteraction(user, {sel}, "Groceries · 01.1").click()  # picks the option by its label
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


async def test_the_import_page_uses_the_picker_with_income_categories(user: User, ctx, setup) -> None:
    spend(ctx, setup, date(2026, 10, 2), "07.2.2")
    await user.open("/import")
    sel = picker(user)
    codes = [o["code"] for o in options(sel)]
    assert codes[0] == "07.2.2" and codes[-1] == setup["salary"].id
    assert offered(sel, "migros")[0] == "01.1"
    assert offered(sel, "salary") == [setup["salary"].id]
    assert sel.options[setup["salary"].id] == "Income · Salary"


async def test_german_names_when_the_language_is_german(user: User, setup) -> None:
    i18n.set_language("de")
    try:
        await user.open("/add-transaction")
        sel = picker(user, "Kategorie")
        by_code = {o["code"]: o for o in options(sel)}
        assert by_code["01.1"]["label"] == "Lebensmittel · 01.1"
        assert by_code["01.1"]["caption"] == "Nahrungsmittel"
        assert by_code["12.1.2"]["label"] == "Krankenkasse · 12.1.2"
        assert by_code[TAX]["label"] == "Steuern"
        assert sel.props["recent-title"] == "Kürzlich verwendet"
        assert offered(sel, "lebensmittel")[0] == "01.1"
        assert offered(sel, "Öffentlicher Verkehr") == ["07.3"]
    finally:
        i18n.set_language("en")
