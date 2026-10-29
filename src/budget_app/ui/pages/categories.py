"""Categories (F37): the COICOP 2018 tree with codes and names in the interface language, and a search.

The whole tree is one Quasar QTree that is built once. Searching sets the tree's `filter` prop to the codes
that match (Quasar hides the other nodes and keeps the ancestors of every match) and its `expanded` prop (so
the matches are open), instead of rebuilding ~600 elements on every keystroke. The search runs in Python, so
the nodes carry no search text.

F40: a category with COICOP notes has an info button beside its name. It opens the notes below the row:
what the category includes, also includes and excludes. Only open nodes carry their notes, which keeps the
tree small. The search also finds a category by what it includes (not by what it excludes).
"""

from __future__ import annotations

from datetime import date
from html import escape

from nicegui import ui

from budget_app import i18n
from budget_app.domain import months
from budget_app.domain.coicop import (
    ADV,
    NOTE_FIELDS,
    PSEUDO_NAMES,
    TAX,
    Category,
    Coicop,
    fold,
    get_coicop,
    match_words,
    note_words,
    notes,
)
from budget_app.i18n import N_, t, tn
from budget_app.services import aggregates
from budget_app.services.context import Context
from budget_app.ui.layout import fmt, frame
from budget_app.ui.pages.settings import income_group

# The row: code, name (divisions bold) with the notes button, number of subcategories, amount spent this year;
# below it the notes once opened. Clicks inside the notes neither open nor close the node.
ROW = """
<div class="cat-item">
  <div class="cat-node" :class="'lvl-' + props.node.level">
    <span class="code">{{ props.node.id }}</span>
    <span class="name">{{ props.node.name }}</span>
    <q-btn v-if="props.node.has_notes" flat round dense size="sm" class="notes-btn"
      :icon="props.node.notes ? 'expand_less' : 'info_outline'" aria-label="%(show)s" title="%(show)s"
      :aria-expanded="props.node.notes ? 'true' : 'false'"
      @click.stop="$parent.$emit('notes', props.node.id)" @keypress.stop />
    <span class="caption" v-if="props.node.sub">{{ props.node.sub }}</span>
    <span class="spent" v-if="props.node.spent">{{ props.node.spent }}</span>
  </div>
  <div class="cat-notes" v-if="props.node.notes" @click.stop @keypress.stop>
    <div class="cat-notes__part" v-for="part in props.node.notes.parts" :key="part.head">
      <div class="cat-notes__head">{{ part.head }}</div>
      <ul><li v-for="(item, i) in part.items" :key="i">{{ item }}</li></ul>
    </div>
    <div class="cat-notes__foot" v-if="props.node.notes.foot">{{ props.node.notes.foot }}</div>
  </div>
</div>
"""
# The filter is the matching codes, each between spaces (" 01 01.1 "); the browser keeps those nodes.
FILTER_METHOD = "(node, filter) => filter.includes(' ' + node.id + ' ')"
NOTE_HEADINGS = {"includes": N_("Includes"), "also_includes": N_("Also includes"), "excludes": N_("Excludes")}


def spent_this_year(ctx: Context, today: date | None = None) -> dict[str, float]:
    """Expenditure this calendar year per COICOP code, every level summed up, in the display currency."""
    year = (today or date.today()).year
    month_set = set(months.month_range(f"{year}-01", f"{year}-12"))
    direct = aggregates.actual_expenditure(ctx, month_set, ctx.store.settings.display_currency)
    return aggregates.rollup(direct)


def _node(coicop: Coicop, cat: Category, spent: dict[str, float], lang: str) -> dict:
    name = cat.display_name
    n = len(cat.children)
    amount = spent.get(cat.code, 0.0)
    node: dict = {
        "id": cat.code,
        "label": f"{cat.code} {name}",
        "name": name,
        "level": cat.level,
        "sub": tn("{n} subcategory", "{n} subcategories", n) if n else "",
        "spent": fmt(amount) if abs(amount) >= 0.005 else "",
        "has_notes": notes(cat.code, lang) is not None,
    }
    if n:
        node["children"] = [_node(coicop, coicop.categories[c], spent, lang) for c in cat.children]
    return node


def tree_nodes(coicop: Coicop, spent: dict[str, float], lang: str | None = None) -> list[dict]:
    lang = lang or i18n.language()
    return [_node(coicop, coicop.categories[code], spent, lang) for code in coicop.divisions]


def all_nodes(nodes: list[dict]) -> list[dict]:
    """Every node of the tree, parents before their children."""
    return [n for node in nodes for n in (node, *all_nodes(node.get("children", [])))]


def search_index(coicop: Coicop, lang: str | None = None) -> dict[str, tuple[str, str]]:
    """Code -> (folded code and names, in the interface language and in English; the words of its notes)."""
    lang = lang or i18n.language()
    return {
        cat.code: (fold(f"{cat.code} {cat.display_name} {cat.name}"), note_words(cat.code, lang))
        for cat in coicop.categories.values()
        if cat.code not in PSEUDO_NAMES
    }


def matches(coicop: Coicop, index: dict[str, tuple[str, str]], query: str) -> tuple[list[str], list[str]]:
    """(codes where every word of the folded `query` occurs in the names or notes, codes to expand so they show)."""
    found = [code for code, (names, words) in index.items() if match_words(query, names, words)[0]]
    expand = {anc for code in found for anc in coicop.ancestors(code)[1:]}
    return found, sorted(expand)


def notes_view(code: str, lang: str | None = None) -> dict | None:
    """What an open node shows: the headings with their items, and the "unofficial translation" line."""
    note = notes(code, lang or i18n.language())
    if note is None:
        return None
    parts = [{"head": t(NOTE_HEADINGS[name]), "items": list(note.items(name))} for name in NOTE_FIELDS]
    foot = t("Unofficial translation") if note.translated else ""
    return {"parts": [p for p in parts if p["items"]], "foot": foot}


def page() -> None:
    with frame(
        t("Categories"),
        "/categories",
        subtitle=t("COICOP 2018, the UN classification of spending by purpose"),
    ) as (ctx, actions):
        coicop = get_coicop()
        spent = spent_this_year(ctx)
        nodes = tree_nodes(coicop, spent)
        index = search_index(coicop)
        total = len(index)
        state: dict = {"before": []}  # what the user had open before searching

        with actions:
            search = (
                ui.input(placeholder=t("Search code, name or notes"))
                .props("dense outlined clearable debounce=150")
                .classes("search w-64 max-sm:w-full")
            )
            with search.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")

        with ui.element("div").classes("surface"):
            with ui.row().classes("w-full items-baseline pb-1"):
                count = ui.label(tn("{n} category", "{n} categories", total)).classes("caption")
                ui.space()
                if spent:
                    currency = ctx.store.settings.display_currency
                    ui.label(t("Spent in {year}, {currency}", year=date.today().year, currency=currency)).classes(
                        "caption"
                    )
            tree = ui.tree(nodes, node_key="id", label_key="label").props("no-transition").classes("cat-tree w-full")
            tree.props["no-results-label"] = t("No category matches.")
            tree.props[":filter-method"] = FILTER_METHOD
            tree.props["expanded"] = []
            tree.add_slot("default-header", ROW % {"show": escape(t("Show what this category includes"))})
        by_code = {node["id"]: node for node in all_nodes(tree.props["nodes"])}  # the tree's own (observed) copies

        ui.label(
            t(
                "Besides COICOP, expenditure can be booked as {taxes}. The part of a payment made for "
                "someone else counts as {advances} until it is paid back.",
                taxes=t(PSEUDO_NAMES[TAX]),
                advances=t(PSEUDO_NAMES[ADV]),
            )
        ).classes("footnote px-1")
        ui.label(
            t(
                "The notes are those of the UN's COICOP 2018 publication; translations of them are the app's own "
                "and unofficial."
            )
        ).classes("footnote px-1")

        def toggle_notes(code: str) -> None:
            node = by_code.get(code)
            if node is None:
                return
            if node.pop("notes", None) is None:  # the tree's nodes are observed: a change updates the tree
                node["notes"] = notes_view(code)

        def apply(value: str | None) -> None:
            query = fold((value or "").strip())
            searching = bool(tree.props.get("filter"))
            if not query:
                tree.set_filter("")
                tree.props["expanded"] = list(state["before"])
                count.set_text(tn("{n} category", "{n} categories", total))
                return
            if not searching:
                state["before"] = list(tree.props.get("expanded") or [])
            found, expand = matches(coicop, index, query)
            tree.set_filter(f" {' '.join(found)} ")
            tree.props["expanded"] = expand
            count.set_text(tn("{n} match", "{n} matches", len(found)))

        search.on_value_change(lambda e: apply(e.value))
        tree.on("notes", lambda e: toggle_notes(str(e.args)))
        income_group(ctx)  # what income is booked under; expenditure follows COICOP above
