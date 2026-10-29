"""F18. The Overview's *Notable this month* card: a short list of what stands out, each linking to what explains it.

The rules live in `services.trends.notable` and their thresholds in `domain.trends`; this module only shows them.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from nicegui import ui

from budget_app.domain import months
from budget_app.domain import trends as rules
from budget_app.domain.coicop import get_coicop
from budget_app.i18n import date_label, t
from budget_app.services import trends
from budget_app.services.context import Context
from budget_app.ui.layout import fmt

ICONS = {  # (icon, colour class)
    trends.OVER_BUDGET: ("o_error_outline", "text-neg"),
    trends.PACE: ("o_speed", "notable-warn"),
    trends.ABOVE_USUAL: ("o_trending_up", "notable-warn"),
    trends.LARGE: ("o_receipt_long", "text-primary"),
}


def link(item: trends.Item, month: str) -> str:
    """Budgets open the Budget page at the category; spending opens the Transactions it is made of."""
    query = urlencode({"month": month, "category": item.category})
    page = "/budget" if item.kind in (trends.OVER_BUDGET, trends.PACE) else "/transactions"
    return f"{page}?{query}"


def texts(item: trends.Item, notable: trends.Notable) -> tuple[str, str]:
    """The title and the line below it."""
    cur, label = notable.currency, get_coicop().label(item.category)
    if item.kind == trends.OVER_BUDGET:
        over = fmt(item.amount - item.reference)
        return label, t("{amount} over the {budget} budget", amount=over, budget=fmt(item.reference, cur))
    if item.kind == trends.PACE:
        projected = fmt(item.projected)
        return label, t(
            "On pace for {amount} of the {budget} budget", amount=projected, budget=fmt(item.reference, cur)
        )
    if item.kind == trends.ABOVE_USUAL:
        if item.reference > rules.EPS:
            return label, t("Usually {amount} a month", amount=fmt(item.reference, cur))
        return label, t("Nothing in the {n} months before", n=notable.history_months)
    parts = [date_label(item.day) if item.day else "", label]
    if item.reference > rules.EPS:
        parts.append(t("{pct} of a usual month", pct=rules.percent(item.amount / item.reference)))
    return item.name or label, " · ".join(p for p in parts if p)


def notable_card(ctx: Context, currency: str, today: date | None = None) -> None:
    """The card, or nothing when nothing stands out (or nothing is recorded yet)."""
    notable = trends.notable(ctx, currency, today)
    short = notable.short_history and notable.has_data
    if not notable.items and not short:
        return
    with ui.element("div").classes("surface flex flex-col gap-1 notable"):
        with ui.row().classes("w-full items-baseline gap-2"):
            ui.label(t("Notable This Month")).classes("section-title")
            ui.label(t("{month} so far", month=months.label(notable.month))).classes("caption")
        for item in notable.items:
            title, sub = texts(item, notable)
            icon, colour = ICONS[item.kind]
            with ui.link(target=link(item, notable.month)).classes("notable-row"):
                ui.icon(icon, size="20px").classes(colour)
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(title).classes("n-title")
                    ui.label(sub).classes("n-sub")
                ui.label(fmt(item.amount, currency)).classes("n-amount")
                ui.icon("chevron_right", size="18px").classes("text-3")
        if short:
            ui.label(
                t(
                    "Comparisons with your usual spending start once {n} months are recorded.",
                    n=rules.MIN_HISTORY_MONTHS,
                )
            ).classes("caption pt-1")
