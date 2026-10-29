"""Categories page (F37): the COICOP tree, its search and the interface language."""

from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app import i18n
from budget_app.domain.coicop import fold, get_coicop
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui.pages import categories


def the_tree(user: User) -> ui.tree:
    trees = user.find(ui.tree).elements
    assert len(trees) == 1
    return next(iter(trees))


def all_nodes(nodes: list[dict]) -> list[dict]:
    return [n for node in nodes for n in (node, *all_nodes(node.get("children", [])))]


async def test_the_page_lists_every_coicop_category(user: User) -> None:
    await user.open("/categories")
    await user.should_see("Categories")
    await user.should_see("01 Food and non-alcoholic beverages")
    await user.should_see("12 Insurance and financial services")
    await user.should_see("602 categories")
    await user.should_see("Taxes", kind=ui.label)  # the app's own categories, in a note
    tree = the_tree(user)
    nodes = {n["id"]: n for n in all_nodes(tree.props["nodes"])}
    assert len(nodes) == 602 and "TAX" not in nodes and "ADV" not in nodes
    deep = nodes["01.1.1.1"]
    assert deep["label"] == "01.1.1.1 Cereals" and deep["level"] == 4 and deep["sub"] == ""
    assert nodes["01"]["level"] == 1 and nodes["01"]["sub"] == "3 subcategories"
    assert nodes["01.1.1"]["sub"] == "6 subcategories"
    # the official name first, and the everyday name the rest of the app shows beside it (F08)
    assert (nodes["01.1"]["name"], nodes["01.1"]["everyday"]) == ("Food", "Groceries")
    assert deep["everyday"] == "" and nodes["06"]["everyday"] == ""  # none, or the same as the official name
    assert ":filter-method" in tree.props and tree.props["expanded"] == []


async def test_the_sidebar_links_to_the_page(user: User) -> None:
    await user.open("/settings")  # a tab of Settings (UX review before 1.0)
    links = [link for link in user.find(ui.link).elements if link.props.get("href") == "/categories"]
    assert len(links) == 1 and "active" not in links[0].classes
    await user.open("/categories")
    links = [link for link in user.find(ui.link).elements if link.props.get("href") == "/categories"]
    assert "active" in links[0].classes


def filtered(tree: ui.tree) -> list[str]:
    """The codes the browser keeps: the filter lists them between spaces (`categories.FILTER_METHOD`)."""
    return (tree.props.get("filter") or "").split()


async def test_search_narrows_and_opens_the_matches(user: User) -> None:
    await user.open("/categories")
    tree = the_tree(user)
    tree.props["expanded"] = ["04"]  # the user had a division open
    search = user.find(ui.input)
    search.type("BREAKFAST CÉRÉAL")  # case and accents do not matter; every word must match
    await user.should_see("1 match")
    assert filtered(tree) == ["01.1.1.4"]
    assert tree.props["expanded"] == ["01", "01.1", "01.1.1"]
    await user.should_see("01.1.1.4 Breakfast cereals")
    assert "' ' + node.id + ' '" in tree.props[":filter-method"]
    assert not any("search" in node for node in categories.all_nodes(tree.props["nodes"]))  # searched in Python

    search.clear()
    await user.should_see("602 categories")
    assert not tree.props["filter"]
    assert tree.props["expanded"] == ["04"]  # back to what was open before searching


async def test_search_finds_a_category_by_its_everyday_name(user: User) -> None:
    await user.open("/categories")
    tree = the_tree(user)
    user.find(ui.input).type("groceries")
    await user.should_see("2 matches")
    assert filtered(tree) == ["01.1", "07.4.9"]  # its everyday name; and deliveries, whose notes mention groceries
    assert "groceries" not in categories.search_index(get_coicop())["07.4.9"][0]


async def test_search_finds_what_a_category_includes(user: User) -> None:
    await user.open("/categories")
    tree = the_tree(user)
    search = user.find(ui.input)
    search.type("teff")  # only in the notes of 01.1.1.1
    await user.should_see("1 match")
    assert filtered(tree) == ["01.1.1.1"]
    search.clear().type("funicular")  # 07.3.6 includes it, 07.3.1 excludes it
    await user.should_see("matches")
    assert "07.3.6" in filtered(tree) and "07.3.1" not in filtered(tree)


async def test_search_by_code_and_without_matches(user: User) -> None:
    await user.open("/categories")
    tree = the_tree(user)
    search = user.find(ui.input)
    search.type("07.2.2")
    await user.should_see("matches")
    found, expand = categories.matches(get_coicop(), categories.search_index(get_coicop()), "07.2.2")
    assert found[0] == "07.2.2" and all(code.startswith("07.2.2") for code in found)
    assert filtered(tree) == found
    assert tree.props["expanded"] == expand == ["07", "07.2", "07.2.2"]
    search.clear().type("zzz")
    await user.should_see("0 matches")
    assert tree.props["filter"].strip() == "" and tree.props["filter"]  # filtering, nothing kept
    assert tree.props["expanded"] == []
    assert tree.props["no-results-label"] == "No category matches."


async def test_each_category_opens_its_notes(user: User) -> None:
    await user.open("/categories")
    await user.should_see("The notes are those of the UN's COICOP 2018 publication", kind=ui.label)
    tree = the_tree(user)
    nodes = {n["id"]: n for n in categories.all_nodes(tree.props["nodes"])}
    assert nodes["01.1.1.1"]["has_notes"] and not nodes["01"]["has_notes"]
    assert "notes" not in nodes["01.1.1.1"]  # only open nodes carry their notes
    tree.props["expanded"] = ["01"]
    UserInteraction(user, {tree}, None).trigger("notes", "01.1.1.1")
    nodes = {n["id"]: n for n in categories.all_nodes(tree.props["nodes"])}
    notes = nodes["01.1.1.1"]["notes"]
    assert [p["head"] for p in notes["parts"]] == ["Includes", "Also includes", "Excludes"]
    assert notes["parts"][0]["items"][:2] == ["wheat", "rice, including parboiled rice"]
    assert notes["foot"] == ""  # the UN's own English
    assert tree.props["expanded"] == ["01"]  # opening notes keeps the tree as it was
    assert "notes" not in nodes["01.1.2.1"]  # the others stay closed
    UserInteraction(user, {tree}, None).trigger("notes", "01.1.1.1")  # closes again
    assert "notes" not in {n["id"]: n for n in categories.all_nodes(tree.props["nodes"])}["01.1.1.1"]
    UserInteraction(user, {tree}, None).trigger("notes", "nope")  # unknown codes are ignored


def test_notes_view_leaves_out_empty_parts() -> None:
    view = categories.notes_view("12.1.2", "en")
    assert view is not None and [p["head"] for p in view["parts"]] == ["Includes", "Also includes"]
    assert categories.notes_view("01", "en") is None


async def test_the_page_speaks_german(user: User) -> None:
    i18n.set_language("de")
    try:
        await user.open("/categories")
        await user.should_see("Kategorien")
        await user.should_see("01 Nahrungsmittel und alkoholfreie Getränke")
        await user.should_see("602 Kategorien")
        user.find(ui.input).type("getranke")  # finds "Getränke" without the umlaut
        await user.should_see("Treffer")
        tree = the_tree(user)
        assert "01" in tree.props["expanded"]
        await user.should_see("01.1 Nahrungsmittel")
        user.find(ui.input).clear().type("hirse")  # the German notes
        await user.should_see("1 Treffer")
        assert filtered(tree) == ["01.1.1.1"]
        UserInteraction(user, {tree}, None).trigger("notes", "01.1.1.1")
        node = next(n for n in categories.all_nodes(tree.props["nodes"]) if n["id"] == "01.1.1.1")
        assert [p["head"] for p in node["notes"]["parts"]] == ["Umfasst", "Umfasst auch", "Umfasst nicht"]
        assert node["notes"]["parts"][0]["items"][0] == "Weizen"
        assert node["notes"]["foot"] == "Inoffizielle Übersetzung"
    finally:
        i18n.set_language("en")


async def test_each_category_shows_this_years_spending(user: User, setup, ctx) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date.today(),
            type="expenditure",
            amount=1234.5,
            currency="CHF",
            category="01.1.1.1",
            from_account=setup["chf"].id,
        ),
    )
    await user.open("/categories")
    await user.should_see(f"Spent in {date.today().year}, CHF")
    nodes = {n["id"]: n for n in all_nodes(the_tree(user).props["nodes"])}
    assert nodes["01.1.1.1"]["spent"] == nodes["01"]["spent"] == "1'234.50"
    assert nodes["02"]["spent"] == ""


def test_spending_counts_only_this_year(ctx, setup) -> None:
    for day in (date(2025, 12, 31), date(2026, 1, 1)):
        tx_svc.save(
            ctx,
            TransactionInput(
                date=day,
                type="expenditure",
                amount=10,
                currency="CHF",
                category="07.2",
                from_account=setup["chf"].id,
            ),
        )
    assert categories.spent_this_year(ctx, today=date(2026, 6, 1)) == {"07": 10.0, "07.2": 10.0}


def test_fold_ignores_case_and_accents() -> None:
    assert fold("Getränke À LA Carte") == "getranke a la carte"
    assert fold("Œuvre") == "œuvre"
