"""Start page: wealth KPIs and a chart builder (several charts side by side)."""

from __future__ import annotations

import base64
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from nicegui import ui

from budget_app.domain import months
from budget_app.domain.models import ASSET_CLASSES
from budget_app.i18n import N_, t
from budget_app.services import export as export_svc
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context
from budget_app.ui.layout import (
    data_month_range,
    error,
    fmt,
    frame,
    metric,
    money_html,
    month_options,
    on_data_changed,
    save_file,
    segmented,
)
from budget_app.ui.theme import CHART_GRID, NEGATIVE, NEUTRAL, POSITIVE, SERIES, chart_layout, figure_dict
from budget_app.ui.widgets.selects import currency_select

CHART_TYPES = {
    "wealth": N_("Wealth Over Time"),
    "flows": N_("Income and Expenditure"),
    "waterfall": N_("Change per Account"),
    "fx": N_("Exchange Rates"),
    "classes": N_("Wealth by Asset Class"),
}
CHART_HINTS = {
    "wealth": N_("Recorded balances, then the budget projection"),
    "flows": N_("Monthly totals or chosen categories"),
    "waterfall": N_("How each account moved between two month ends"),
    "fx": N_("ECB rates against the display currency"),
    "classes": N_("Month-end balances by asset class"),
}


class ChartCard:
    """One chart with its own controls and downloads."""

    def __init__(self, ctx: Context, kind: str, currency: dict, container, on_remove):
        self.ctx, self.kind, self.currency = ctx, kind, currency
        self.data = pd.DataFrame()
        first, last = data_month_range(ctx, back=12)
        cm = months.current_month()
        with container, ui.element("div").classes("surface flex flex-col gap-3") as self.card:
            with ui.row().classes("w-full items-start gap-2 no-wrap"):
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(t(CHART_TYPES[kind])).classes("section-title")
                    ui.label(t(CHART_HINTS[kind])).classes("caption")
                ui.space()
                with (
                    ui.button(icon="more_horiz").props("flat round dense").classes("text-primary"),
                    ui.menu().props("auto-close"),
                ):
                    ui.menu_item(t("Save Image…"), on_click=lambda: self.save_image("png"))
                    ui.menu_item(t("Save as PDF…"), on_click=lambda: self.save_image("pdf"))
                    ui.menu_item(t("Export Data as CSV…"), on_click=lambda: self.download("csv"))
                    ui.menu_item(t("Export Data as Excel…"), on_click=lambda: self.download("xlsx"))
                    ui.separator()
                    ui.menu_item(t("Remove Chart"), on_click=lambda: on_remove(self)).classes("text-negative")
            with ui.row().classes("w-full items-start gap-2 flex-wrap"):
                opts = month_options(first, last)
                if kind == "flows":
                    self.start = (
                        ui.select(
                            opts,
                            value=months.add_months(cm, -6) if months.add_months(cm, -6) in opts else first,
                            label=t("From"),
                        )
                        .props("dense outlined options-dense")
                        .classes("w-32")
                    )
                    self.end = (
                        ui.select(opts, value=cm, label=t("To")).props("dense outlined options-dense").classes("w-32")
                    )
                    self.style = segmented({"bar": t("Bars"), "line": t("Lines")}, "bar")
                    self.budget = ui.switch(t("Budget")).props("dense")
                    self.series = ui.select(
                        reports.series_keys(ctx),
                        value=["EXP:TOTAL", "INC:TOTAL"],
                        label=t("Categories"),
                        multiple=True,
                        with_input=True,
                    )
                    self.series.props("dense outlined use-chips options-dense").classes("w-full")
                    controls = [self.start, self.end, self.style, self.budget, self.series]
                elif kind == "classes":
                    prev = months.previous_month()
                    self.start = (
                        ui.select(
                            opts,
                            value=months.add_months(prev, -11) if months.add_months(prev, -11) in opts else first,
                            label=t("From"),
                        )
                        .props("dense outlined options-dense")
                        .classes("w-32")
                    )
                    self.end = (
                        ui.select(opts, value=prev if prev in opts else last, label=t("To"))
                        .props("dense outlined options-dense")
                        .classes("w-32")
                    )
                    controls = [self.start, self.end]
                elif kind == "waterfall":
                    prev = months.previous_month()
                    self.start = (
                        ui.select(
                            opts,
                            value=months.add_months(prev, -1) if months.add_months(prev, -1) in opts else first,
                            label=t("From end of"),
                        )
                        .props("dense outlined options-dense")
                        .classes("w-36")
                    )
                    self.end = (
                        ui.select(opts, value=prev if prev in opts else last, label=t("To end of"))
                        .props("dense outlined options-dense")
                        .classes("w-36")
                    )
                    controls = [self.start, self.end]
                elif kind == "fx":
                    self.start = (
                        ui.select(
                            opts,
                            value=months.add_months(cm, -12) if months.add_months(cm, -12) in opts else first,
                            label=t("From"),
                        )
                        .props("dense outlined options-dense")
                        .classes("w-32")
                    )
                    self.end = (
                        ui.select(opts, value=cm if cm in opts else last, label=t("To"))
                        .props("dense outlined options-dense")
                        .classes("w-32")
                    )
                    self.step = segmented({"day": t("Daily"), "month": t("Month End")}, "day")
                    self.index = ui.switch(t("Index (start = 100)")).props("dense")
                    self.fx_currencies = ui.select(
                        ctx.fx.currencies(),
                        value=self._default_fx_currencies(),
                        label=t("Currencies"),
                        multiple=True,
                        with_input=True,
                    )
                    self.fx_currencies.props("dense outlined use-chips options-dense").classes("w-full")
                    controls = [self.start, self.end, self.step, self.index, self.fx_currencies]
                else:
                    controls = []
            self.plot = ui.plotly(figure_dict(go.Figure())).classes("w-full").style("height: 320px")
        for c in controls:
            c.on_value_change(self.render)
        self.render()

    # ------------------------------------------------------------------ figures
    def render(self) -> None:
        cur = self.currency["value"]
        try:
            fig = getattr(self, f"_fig_{self.kind}")(cur)
        except Exception as exc:
            error(t("Chart error: {error}", error=exc))
            return
        self.plot.update_figure(figure_dict(fig))

    def _fig_wealth(self, cur: str) -> go.Figure:
        series = wealth_svc.wealth_series(self.ctx, cur)
        self.data = pd.DataFrame(
            [{"month": m, "wealth": round(v, 2), "type": "projected" if p else "actual"} for m, v, p in series]
        )
        fig = go.Figure()
        actual = [(m, v) for m, v, p in series if not p]
        projected = [(m, v) for m, v, p in series if p]
        recorded_name = t("Recorded")
        if actual and projected:
            projected.insert(0, actual[-1])  # connect the projection to the last actual value
        if actual:
            fig.add_scatter(
                x=[m for m, _ in actual],
                y=[v for _, v in actual],
                name=recorded_name,
                mode="lines",
                line=dict(color=SERIES[0], width=2.5, shape="spline", smoothing=0.4),
                fill="tozeroy",
                fillcolor="rgba(10,132,255,0.08)",
            )
        if projected:
            fig.add_scatter(
                x=[m for m, _ in projected],
                y=[v for _, v in projected],
                name=t("Projected"),
                mode="lines",
                line=dict(color=SERIES[0], width=2, dash="dot"),
            )
        fig = chart_layout(fig, cur)
        values = [v for _, v, _ in series]
        if values and min(values) > 0:  # wealth rarely nears zero; a zero baseline flattens the line
            lo, hi = min(values), max(values)
            pad = max((hi - lo) * 0.15, hi * 0.02)
            fig.update_yaxes(range=[max(lo - pad, 0), hi + pad])
            fig.update_traces(fill=None, selector=dict(name=recorded_name))
        return fig

    def _fig_flows(self, cur: str) -> go.Figure:
        start, end = sorted([self.start.value, self.end.value])
        keys = self.series.value or []
        rows = reports.monthly_series(self.ctx, start, end, cur, keys, include_budget=bool(self.budget.value))
        self.data = pd.DataFrame(rows)
        fig = go.Figure()
        cols = [c for c in self.data.columns if c != "month"]
        budget_cols = {reports.budget_label(c) for c in cols}  # names match the report, in any language
        for i, col in enumerate(cols):
            is_budget = col in budget_cols
            color = SERIES[(i // 2 if self.budget.value else i) % len(SERIES)]
            if self.style.value == "bar":
                fig.add_bar(
                    x=self.data["month"],
                    y=self.data[col],
                    name=col,
                    marker_color=color,
                    opacity=0.35 if is_budget else 1.0,
                )
            else:
                fig.add_scatter(
                    x=self.data["month"],
                    y=self.data[col],
                    name=col,
                    mode="lines",
                    line=dict(color=color, width=2, dash="dot" if is_budget else "solid"),
                )
        fig.update_layout(barmode="group")
        fig.update_xaxes(
            type="category",
            tickvals=list(self.data["month"]) if not self.data.empty else None,
            ticktext=[months.label(m) for m in self.data["month"]] if not self.data.empty else None,
        )
        return chart_layout(fig, cur)

    def _default_fx_currencies(self) -> list[str]:
        """The currencies of the user's accounts, apart from the display currency (else EUR or USD)."""
        view = self.currency["value"]
        own = sorted({a.currency for a in self.ctx.store.accounts.all()} - {view})
        return own or (["EUR"] if view != "EUR" else ["USD"])

    def _fig_fx(self, cur: str) -> go.Figure:
        """One line per currency: the price of one unit in the display currency (F38)."""
        start, end = sorted([self.start.value, self.end.value])
        first, last = date.fromisoformat(f"{start}-01"), months.last_day(end)
        indexed = bool(self.index.value)
        fig = go.Figure()
        table: dict[str, dict] = {}
        for i, code in enumerate(c for c in (self.fx_currencies.value or []) if c != cur):
            points = self.ctx.fx.history(code, cur, first, last, month_end=self.step.value == "month")
            if not points:
                continue
            base = points[0][1]
            ys = [round(v / base * 100, 2) if indexed else round(v, 4) for _, v in points]
            for (d, _), y in zip(points, ys, strict=True):
                table.setdefault(d.isoformat(), {"date": d.isoformat()})[code] = y
            fig.add_scatter(
                x=[d.isoformat() for d, _ in points],
                y=ys,
                name=code,
                mode="lines",
                line=dict(color=SERIES[i % len(SERIES)], width=2),
                hovertemplate=f"{code}: %{{y}}" + ("" if indexed else f" {cur}") + "<extra></extra>",
            )
        self.data = pd.DataFrame(sorted(table.values(), key=lambda r: r["date"]))
        fig = chart_layout(fig, "" if indexed else cur)
        fig.update_yaxes(tickformat=",~r" if not indexed else ",~f")  # rates need their decimals
        return fig

    def _fig_classes(self, cur: str) -> go.Figure:
        """Stacked bars: each month's wealth per asset class (F21)."""
        start, end = sorted([self.start.value, self.end.value])
        month_list = months.month_range(start, end)
        per_month = {m: wealth_svc.by_class(self.ctx, m, cur) for m in month_list}
        self.data = pd.DataFrame(
            [{"month": m} | {t(ASSET_CLASSES[k]): round(v, 2) for k, v in per_month[m].items()} for m in month_list]
        )
        fig = go.Figure()
        for i, key in enumerate(ASSET_CLASSES):
            ys = [round(per_month[m].get(key, 0.0), 2) for m in month_list]
            if any(abs(y) > 0.005 for y in ys):
                fig.add_bar(x=month_list, y=ys, name=t(ASSET_CLASSES[key]), marker_color=SERIES[i % len(SERIES)])
        fig.update_layout(barmode="relative")
        fig.update_xaxes(type="category", tickvals=month_list, ticktext=[months.label(m) for m in month_list])
        return chart_layout(fig, cur)

    def _fig_waterfall(self, cur: str) -> go.Figure:
        start, end = sorted([self.start.value, self.end.value])
        changes = reports.account_changes(self.ctx, start, end, cur)
        self.data = pd.DataFrame(changes)
        w0 = sum(c["start"] for c in changes)
        w1 = sum(c["end"] for c in changes)
        x = (
            [t("Wealth {month}", month=months.label(start))]
            + [c["account"] for c in changes]
            + [t("Wealth {month}", month=months.label(end))]
        )
        y = [w0] + [c["change"] for c in changes] + [w1]
        fig = go.Figure(
            go.Waterfall(
                x=x,
                y=y,
                measure=["absolute"] + ["relative"] * len(changes) + ["total"],
                connector=dict(line=dict(color=CHART_GRID, width=1)),
                increasing=dict(marker=dict(color=POSITIVE)),
                decreasing=dict(marker=dict(color=NEGATIVE)),
                totals=dict(marker=dict(color=NEUTRAL)),
            )
        )
        fig = chart_layout(fig, cur)
        fig.update_layout(hovermode="closest", showlegend=False)
        levels = [w0]
        for c in changes:
            levels.append(levels[-1] + c["change"])
        lo, hi = min(levels), max(levels)
        if lo > 0 and hi > 0:  # zoom in on the changes instead of starting at zero
            pad = max((hi - lo) * 0.25, hi * 0.01)
            fig.update_yaxes(range=[max(lo - pad, 0), hi + pad])
        return fig

    # ------------------------------------------------------------------ export
    def file_name(self, ext: str) -> str:
        return f"{self.kind}_chart_{self.currency['value']}.{ext}"

    async def download(self, kind: str) -> None:
        data = export_svc.frame_csv(self.data) if kind == "csv" else export_svc.frame_xlsx(self.data, self.kind)
        await save_file(data, self.file_name(kind))

    async def save_image(self, kind: str) -> None:
        """PNG straight from the chart in the page; the PDF wraps that image with a title."""
        try:
            url = await self.plot.run_plot_method(
                "toImage", {"format": "png", "width": 1200, "height": 600, "scale": 2}, timeout=15
            )
            data = base64.b64decode(str(url).split(",", 1)[1])
            if kind == "pdf":
                title = t(CHART_TYPES[self.kind])
                data = export_svc.chart_pdf(data, title, t("Amounts in {currency}", currency=self.currency["value"]))
        except Exception as exc:
            error(t("Could not create the image: {error}", error=exc))
            return
        await save_file(data, self.file_name(kind))


def class_breakdown(sums: dict[str, float], cur: str) -> None:
    """Net worth by asset class (F21): one bar split by the classes' shares, and the amount of each."""
    positive = sum(v for v in sums.values() if v > 0)
    keys = list(ASSET_CLASSES)
    with ui.element("div").classes("surface flex flex-col gap-3"):
        with ui.row().classes("w-full items-baseline gap-2"):
            ui.label(t("By Asset Class")).classes("section-title")
            ui.label(t("End of {month}", month=months.label(months.previous_month()))).classes("caption")
        if positive > 0:
            with ui.element("div").classes("class-bar"):
                for key, value in sums.items():
                    if value > 0:
                        color = SERIES[keys.index(key) % len(SERIES)]
                        seg = ui.element("div").classes("class-seg")
                        seg.style(f"flex: {value / positive:.4f} 1 0; background: {color}")
                        seg.tooltip(t(ASSET_CLASSES[key]))
        with ui.element("div").classes("class-legend"):
            for key, value in sums.items():
                color = SERIES[keys.index(key) % len(SERIES)]
                with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                    ui.element("span").classes("class-dot").style(f"background: {color}")
                    ui.label(t(ASSET_CLASSES[key])).classes("flex-1 truncate text-[13px]")
                    share = f" · {value / positive * 100:.0f} %" if positive > 0 and value > 0 else ""
                    ui.label(fmt(value, cur) + share).classes("num text-[13px] text-2 whitespace-nowrap")


def page() -> None:
    with frame(t("Overview"), "/") as (ctx, actions):
        currency = {"value": ctx.store.settings.display_currency}
        charts: list[ChartCard] = []

        @ui.refreshable
        def kpis() -> None:
            cur = currency["value"]
            horizon = ctx.horizon()
            try:
                current = wealth_svc.current_wealth(ctx, cur)
                projected = wealth_svc.projected_wealth(ctx, horizon, cur)
            except Exception as exc:
                error(t("Cannot compute wealth: {error}", error=exc))
                return
            change = projected - current
            with ui.element("div").classes("surface"), ui.element("div").classes("metrics").style("--n: 2"):
                metric(
                    t("Wealth"),
                    current,
                    cur,
                    t("End of {month}", month=months.label(months.previous_month())),
                    hero=True,
                )
                with ui.element("div").classes("metric"):
                    ui.label(t("Projected for {month}", month=months.label(horizon))).classes("metric-label")
                    ui.html(money_html(projected, cur)).classes("metric-value")
                    ui.label(
                        t("{amount} by the end of the plan", amount=f"{'+' if change >= 0 else ''}{fmt(change, cur)}")
                    ).classes("metric-sub " + ("text-pos" if change >= 0 else "text-neg"))
            if wealth_svc.uses_classes(ctx):
                class_breakdown(wealth_svc.by_class(ctx, months.previous_month(), cur), cur)

        def set_currency(e) -> None:
            currency["value"] = e.value
            kpis.refresh()
            for c in charts:
                c.render()

        def refresh_all() -> None:
            kpis.refresh()
            for c in charts:
                c.render()

        with actions:
            currency_select(ctx, currency["value"], on_change=set_currency, label=t("Currency"))
            with ui.button(t("Add Chart"), icon="add").props("unelevated"), ui.menu().props("auto-close"):
                for key, title in CHART_TYPES.items():
                    with ui.menu_item(on_click=lambda k=key: add(k)), ui.column().classes("gap-0 py-1"):
                        ui.label(t(title))
                        ui.label(t(CHART_HINTS[key])).classes("caption")
        kpis()
        grid = ui.element("div").classes("w-full grid grid-cols-1 xl:grid-cols-2 gap-5")

        def remove(chart: ChartCard) -> None:
            charts.remove(chart)
            grid.remove(chart.card)

        def add(k: str, focus: bool = True) -> None:
            """Add a chart; one the user adds goes first and scrolls into view, so it can't land out of sight."""
            before = len(grid.default_slot.children)
            try:
                chart = ChartCard(ctx, k, currency, grid, remove)
            except Exception as exc:
                for half_built in grid.default_slot.children[before:]:
                    grid.remove(half_built)
                error(t("Chart error: {error}", error=exc))
                return
            charts.append(chart)
            if focus:
                chart.card.move(grid, target_index=0)
                chart.card.classes("just-added")
                ui.run_javascript(
                    f'getHtmlElement({chart.card.id}).scrollIntoView({{behavior: "smooth", block: "nearest"}})'
                )

        add("wealth", focus=False)
        add("flows", focus=False)
        on_data_changed(refresh_all)
