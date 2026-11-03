"""Year in Review (F19): a year's income, spending, savings and wealth against the year before, on screen and as PDF.

A year picker (last year by default), the display currency, a metric strip with what was saved as the hero, the
year's months as a chart (from the first with income or spending), then spending by category, income and the biggest
payments, each with its change against the year before (▲/▼ like the Budget page's comparison, F18) when that year
has anything to compare with. The share menu saves the review as PDF,
with the chart as the Overview's *Save as PDF* renders it, or as Excel.
"""

from __future__ import annotations

import base64
import logging

import plotly.graph_objects as go
from nicegui import run, ui

from budget_app import i18n
from budget_app.domain import months, trends
from budget_app.domain import review as rules
from budget_app.domain.coicop import get_coicop
from budget_app.i18n import t
from budget_app.services import review as review_svc
from budget_app.services import review_export
from budget_app.services.context import Context
from budget_app.services.review import Figure, YearReview
from budget_app.ui.layout import error, fmt, fmt_signed, frame, metric, money_html, named, on_data_changed, save_file
from budget_app.ui.theme import POSITIVE, SERIES, chart_layout, figure_dict
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.selects import currency_select
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

log = logging.getLogger(__name__)
ROW_ACTION = "flat dense round size=sm"
IMAGE = {"format": "png", "width": 1200, "height": 600, "scale": 2}  # the Overview's chart image


def change_label(change: trends.Change, is_expenditure: bool) -> tuple[str, str]:
    """The change against the year before as text and its colour class: red when spending rose or income fell."""
    if change.empty:
        return "—", "text-3"
    ratio = change.ratio
    if ratio is None:
        text = t("new")
    elif abs(ratio) < 0.005:
        text = "0 %"
    else:
        text = ("▲ " if ratio > 0 else "▼ ") + trends.percent(ratio)
    worse = change.delta > rules.EPS if is_expenditure else change.delta < -rules.EPS
    return text, "text-neg" if worse else "text-2"


def previous_label(figure: Figure, review: YearReview) -> str:
    """'2025: 1'200.00 CHF', or that nothing was recorded then."""
    if abs(figure.previous) < rules.EPS:
        return t("Nothing in {year}", year=str(review.year - 1))
    return f"{review.year - 1}: {fmt(figure.previous, review.currency)}"


def savings_label(review: YearReview) -> str:
    rate = review_export.percent(review.savings_rate)
    if review.savings_rate is None:
        return t("No income recorded")
    text = t("{rate} savings rate", rate=rate)
    if review.previous_savings_rate is not None:
        text += f" · {review.year - 1}: {review_export.percent(review.previous_savings_rate)}"
    return text


def wealth_label(review: YearReview) -> str:
    """How wealth moved over the year, or why it can't be said."""
    if review.wealth_end is None:
        return t("No balances recorded")
    if review.wealth_change is None:
        if not review.compared:  # nothing to compare with: when, not what's missing
            return t("End of {month}", month=months.label(review.wealth_month))
        return t("No balances at the end of {month}", month=months.label(review.period.before))
    text = t(
        "{change} since the end of {month}",
        change=fmt_signed(review.wealth_change) or fmt(0),
        month=months.label(review.period.before),
    )
    if review.previous_wealth_change is not None:
        text += f" · {review.year - 1}: {fmt_signed(review.previous_wealth_change) or fmt(0)}"
    return text


def flows_figure(review: YearReview) -> go.Figure:
    """Income and expenditure per month of the year, from the first month with either, as bars in the app's
    colours."""
    fig = go.Figure()
    shown = review.active_months
    x = [m.month for m in shown]
    fig.add_bar(x=x, y=[round(m.income, 2) for m in shown], name=t("Income"), marker_color=POSITIVE)
    fig.add_bar(x=x, y=[round(m.expenditure, 2) for m in shown], name=t("Expenditure"), marker_color=SERIES[0])
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category", tickvals=x, ticktext=[months.label(m) for m in x])
    return chart_layout(fig, review.currency)


# ------------------------------------------------------------------ rows
def _figure_row(line: Figure, review: YearReview, total: float, is_expenditure: bool) -> None:
    with ui.element("div").classes("group-row py-2"):
        with ui.column().classes("gap-0.5 flex-1 min-w-0"):
            name = ui.label(line.label).classes("w-full truncate")
            if is_expenditure and (code := get_coicop().tooltip(line.key)):
                name.props(f'title="{code}"')  # the name alone; the official code on hover
            parts = [previous_label(line, review)] if review.compared else []
            if is_expenditure and line.amount > rules.EPS:
                parts.append(t("{pct} of spending", pct=trends.percent(rules.share(line.amount, total))))
            if parts:
                ui.label(" · ".join(parts)).classes("caption w-full truncate")
        with ui.column().classes("items-end gap-0 shrink-0"):
            ui.label(fmt(line.amount, review.currency)).classes("num font-medium whitespace-nowrap")
            if review.compared:
                text, colour = change_label(line.change, is_expenditure)
                lbl = ui.label(text).classes(f"caption whitespace-nowrap {colour}")
                lbl._props["title"] = (fmt_signed(line.change.delta) or fmt(0)) + f" {review.currency}"


def _group(title: str, lines: list[Figure], review: YearReview, is_expenditure: bool) -> None:
    total = review.expenditure.amount if is_expenditure else review.income.amount
    with ui.column().classes("w-full gap-0"):
        with ui.row().classes("w-full items-end gap-2"):
            ui.label(title).classes("group-header")
            if review.compared:
                ui.space()
                ui.label(f"{review.year} · {review.year - 1}").classes("caption pb-1.5 pr-1")
        with ui.element("div").classes("group"):
            if not lines:
                with ui.element("div").classes("group-row"):
                    ui.label(t("Nothing recorded")).classes("text-2")
            for line in lines:
                _figure_row(line, review, total, is_expenditure)


def _biggest(ctx: Context, review: YearReview, refresh) -> None:
    coicop = get_coicop()

    def show(tx) -> None:
        open_transaction_sheet(ctx, refresh, editing=tx)

    with ui.column().classes("w-full gap-0"):
        ui.label(t("Biggest Expenses")).classes("group-header")
        with ui.element("div").classes("group"):
            if not review.biggest:
                with ui.element("div").classes("group-row"):
                    ui.label(t("Nothing recorded")).classes("text-2")
            for item in review.biggest:
                with ui.element("div").classes("group-row py-2"):
                    with ui.column().classes("gap-0.5 flex-1 min-w-0"):
                        ui.label(item.name or coicop.label(item.category)).classes("w-full truncate")
                        caption = ui.label(f"{i18n.date_label(item.date)} · {coicop.label(item.category)}")
                        caption.classes("caption w-full truncate")
                        if code := coicop.tooltip(item.category):
                            caption.props(f'title="{code}"')
                    tx = ctx.store.transactions.get(item.transaction_id)
                    if tx is not None:  # before the amount, so the amounts line up with the lists above
                        named(
                            ui.button(icon="o_open_in_new", on_click=lambda tx=tx: show(tx))
                            .props(ROW_ACTION)
                            .classes("text-primary row-actions"),
                            t("Show the transaction"),
                        )
                    ui.label(fmt(item.amount, review.currency)).classes("num font-medium whitespace-nowrap")


# ------------------------------------------------------------------ the page
def page() -> None:
    with frame(t("Year in Review"), "/review") as (ctx, actions):
        state: dict = {
            "year": review_svc.default_year(ctx),
            "currency": ctx.store.settings.display_currency,
            "plot": None,
        }

        def current() -> YearReview:
            return review_svc.review(ctx, state["year"], state["currency"])

        async def chart_image() -> bytes | None:
            """The chart as the browser renders it; the PDF does without when that fails."""
            plot = state["plot"]
            if plot is None:
                return None
            try:
                url = await plot.run_plot_method("toImage", IMAGE, timeout=5)
                return base64.b64decode(str(url).split(",", 1)[1])
            except Exception as exc:  # a missing picture must not stop the report
                log.info("Year in review without its chart: %s", exc)
                return None

        async def export(kind: str) -> None:
            review = current()
            try:
                if kind == "pdf":
                    data = await run.io_bound(review_export.pdf, review, await chart_image())
                else:
                    data = await run.io_bound(review_export.xlsx, review)
            except Exception as exc:  # reported to the user, like the statement export
                error(t("Could not create the file: {error}", error=exc))
                return
            await save_file(data, review_export.file_name(review.year, kind))

        def set_year(e) -> None:
            state["year"] = e.value
            content.refresh()

        def set_currency(e) -> None:
            if e.value:
                state["currency"] = e.value
                content.refresh()

        with actions:
            ui.select(
                {y: str(y) for y in review_svc.years(ctx)}, value=state["year"], label=t("Year"), on_change=set_year
            ).props("dense outlined options-dense").classes("w-28")
            currency_select(ctx, state["currency"], on_change=set_currency)
            with (
                named(ui.button(icon="ios_share").props("flat round").classes("text-primary"), t("Export")),
                ui.menu().props("auto-close"),
            ):
                ui.menu_item(t("Save as PDF…"), on_click=lambda: export("pdf"))
                ui.menu_item(t("Export as Excel…"), on_click=lambda: export("xlsx"))

        @ui.refreshable
        def content() -> None:
            review = current()
            cur = review.currency
            period = review_export.period_label(review)
            if not review.has_data:
                state["plot"] = None
                with ui.element("div").classes("group"):
                    empty_state(
                        t("Nothing recorded in {year}", year=str(review.year)),
                        t("A year’s income, spending, savings and wealth, against the year before."),
                    )
                return
            with ui.element("div").classes("surface flex flex-col gap-3"):
                with ui.row().classes("w-full items-baseline gap-2"):
                    ui.label(period).classes("section-title")
                    if review.compared:
                        ui.label(review_export.comparison_label(review)).classes("caption")
                with ui.element("div").classes("metrics").style("--n: 4"):
                    for label, figure in ((t("Income"), review.income), (t("Expenditure"), review.expenditure)):
                        metric(label, figure.amount, cur, previous_label(figure, review) if review.compared else "")
                    with ui.element("div").classes("metric"):
                        ui.label(t("Saved")).classes("metric-label")
                        ui.html(money_html(review.saved.amount, cur)).classes(
                            "metric-value hero " + ("text-neg" if review.saved.amount < -rules.EPS else "")
                        )
                        ui.label(savings_label(review)).classes("metric-sub")
                    metric(
                        t("Wealth"),
                        review.wealth_end,
                        cur,
                        wealth_label(review),
                    )
            state["plot"] = None
            if review.active_months:  # a year of balances alone has no months to chart
                with ui.element("div").classes("surface flex flex-col gap-3"):
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(t("Income and Expenditure")).classes("section-title")
                        ui.label(t("Month by month, in {currency}", currency=cur)).classes("caption")
                    state["plot"] = (
                        ui.plotly(figure_dict(flows_figure(review))).classes("w-full").style("height: 320px")
                    )
            _group(t("Spending by Category"), review.expenditure_lines, review, True)
            _group(t("Income"), review.income_lines, review, False)
            _biggest(ctx, review, content.refresh)

        content()
        on_data_changed(content.refresh)
