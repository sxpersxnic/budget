"""Categories (F37): the COICOP 2018 tree with codes and names in the interface language, and a search.

The whole tree is one Quasar QTree that is built once. Searching sets the tree's `filter` prop
(Quasar hides what does not match and keeps the ancestors of every match) and its `expanded`
prop (so the matches are open), instead of rebuilding ~600 elements on every keystroke.

Each node carries what its row shows; F40 adds the COICOP notes to `_node` (and to its search
text), and the row template shows them.
"""

from __future__ import annotations

from datetime import date

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import ADV, PSEUDO_NAMES, TAX, Category, Coicop, fold, get_coicop
from budget_app.i18n import t, tn
from budget_app.services import aggregates
from budget_app.services.context import Context
from budget_app.ui.layout import fmt, frame

# The row: code, name (divisions bold), number of subcategories, amount spent this year.
ROW = """
<div class="cat-node" :class="'lvl-' + props.node.level">
  <span class="code">{{ props.node.id }}</span>
  <span class="name">{{ props.node.name }}</span>
  <span class="caption" v-if="props.node.sub">{{ props.node.sub }}</span>
  <span class="spent" v-if="props.node.spent">{{ props.node.spent }}</span>
</div>
"""
# The query reaches the browser already folded (lower case, no accents), like every node's `search`.
FILTER_METHOD = "(node, filter) => node.search.includes(filter)"


def spent_this_year(ctx: Context, today: date | None = None) -> dict[str, float]:
    """Expenditure this calendar year per COICOP code, every level summed up, in the display currency."""
    year = (today or date.today()).year
    month_set = set(months.month_range(f"{year}-01", f"{year}-12"))
    direct = aggregates.actual_expenditure(ctx, month_set, ctx.store.settings.display_currency)
    return aggregates.rollup(direct)


def _node(coicop: Coicop, cat: Category, spent: dict[str, float]) -> dict:
    name = cat.display_name
    n = len(cat.children)
    amount = spent.get(cat.code, 0.0)
    node: dict = {
        "id": cat.code,
        "label": f"{cat.code} {name}",
        "name": name,
        "level": cat.level,
        "search": fold(f"{cat.code} {name}"),
        "sub": tn("{n} subcategory", "{n} subcategories", n) if n else "",
        "spent": fmt(amount) if abs(amount) >= 0.005 else "",
    }
    if n:
        node["children"] = [_node(coicop, coicop.categories[c], spent) for c in cat.children]
    return node


def tree_nodes(coicop: Coicop, spent: dict[str, float]) -> list[dict]:
    return [_node(coicop, coicop.categories[code], spent) for code in coicop.divisions]


def matches(coicop: Coicop, nodes: list[dict], query: str) -> tuple[list[str], list[str]]:
    """(codes matching the folded `query`, codes to expand so every match shows)."""
    found: list[str] = []

    def walk(items: list[dict]) -> None:
        for node in items:
            if query in node["search"]:
                found.append(node["id"])
            walk(node.get("children", []))

    walk(nodes)
    expand = {anc for code in found for anc in coicop.ancestors(code)[1:]}
    return found, sorted(expand)


def page() -> None:
    with frame(
        t("Categories"),
        "/categories",
        subtitle=t("COICOP 2018, the UN classification of spending by purpose"),
    ) as (ctx, actions):
        coicop = get_coicop()
        spent = spent_this_year(ctx)
        nodes = tree_nodes(coicop, spent)
        total = sum(1 for c in coicop.categories.values() if c.code not in PSEUDO_NAMES)
        state: dict = {"before": []}  # what the user had open before searching

        with actions:
            search = (
                ui.input(placeholder=t("Search code or name"))
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
            tree.add_slot("default-header", ROW)

        ui.label(
            t(
                "Besides COICOP, expenditure can be booked as {taxes}. The part of a payment made for "
                "someone else counts as {advances} until it is paid back.",
                taxes=t(PSEUDO_NAMES[TAX]),
                advances=t(PSEUDO_NAMES[ADV]),
            )
        ).classes("footnote px-1")

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
            found, expand = matches(coicop, nodes, query)
            tree.set_filter(query)
            tree.props["expanded"] = expand
            count.set_text(tn("{n} match", "{n} matches", len(found)))

        search.on_value_change(lambda e: apply(e.value))
