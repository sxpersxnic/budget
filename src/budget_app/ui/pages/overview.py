"""Start page: wealth KPIs and a chart builder (several charts side by side)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from nicegui import ui

from budget_app.domain import months
from budget_app.services import export as export_svc
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context
from budget_app.ui.layout import card, data_month_range, error, fmt, frame, month_options
from budget_app.ui.widgets.selects import currency_select

CHART_TYPES = {
    "wealth": "Wealth over time (actual & projected)",
    "flows": "Income & expenditure per month",
    "waterfall": "Change per account (waterfall)",
}
PALETTE = ["#2f5d8a", "#c77d2e", "#2e7d4f", "#8e4b9e", "#b3261e", "#4f9fb8", "#7a6c2b", "#5b5b5b"]


def _layout(fig: go.Figure, title: str, currency: str) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        margin=dict(l=50, r=20, t=50, b=40),
        height=380,
        paper_bgcolor="white",
        plot_bgcolor="white",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.18),
        yaxis=dict(title=currency, gridcolor="#e5e9ef", separatethousands=True),
        xaxis=dict(gridcolor="#f0f2f5"),
        font=dict(family="Inter, system-ui, sans-serif"),
    )
    return fig


class ChartCard:
    """One chart with its own controls and downloads."""

    def __init__(self, ctx: Context, kind: str, currency: dict, container, on_remove):
        self.ctx, self.kind, self.currency = ctx, kind, currency
        self.data = pd.DataFrame()
        first, last = data_month_range(ctx, back=12)
        cm = months.current_month()
        with container, ui.card().classes("shadow-sm w-full") as self.card:
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.label(CHART_TYPES[kind]).classes("font-semibold text-slate-700")
                ui.space()
                ui.button(icon="image", on_click=self.download_png).props("flat dense round").tooltip(
                    "Download image (PNG)"
                )
                ui.button("CSV", on_click=lambda: self.download("csv")).props("flat dense no-caps")
                ui.button("Excel", on_click=lambda: self.download("xlsx")).props("flat dense no-caps")
                ui.button(icon="close", on_click=lambda: on_remove(self)).props("flat dense round").tooltip(
                    "Remove chart"
                )
            with ui.row().classes("w-full items-start gap-2 flex-wrap"):
                opts = month_options(first, last)
                if kind == "flows":
                    self.start = (
                        ui.select(
                            opts,
                            value=months.add_months(cm, -6) if months.add_months(cm, -6) in opts else first,
                            label="From",
                        )
                        .props("dense outlined")
                        .classes("w-36")
                    )
                    self.end = ui.select(opts, value=cm, label="To").props("dense outlined").classes("w-36")
                    self.style = ui.toggle({"bar": "Bar", "line": "Line"}, value="bar").props("dense no-caps")
                    self.budget = ui.checkbox("Show budget")
                    self.series = ui.select(
                        reports.series_keys(ctx),
                        value=["EXP:TOTAL", "INC:TOTAL"],
                        label="Categories",
                        multiple=True,
                        with_input=True,
                    )
                    self.series.props("dense outlined use-chips").classes("w-full")
                    controls = [self.start, self.end, self.style, self.budget, self.series]
                elif kind == "waterfall":
                    prev = months.previous_month()
                    self.start = (
                        ui.select(
                            opts,
                            value=months.add_months(prev, -1) if months.add_months(prev, -1) in opts else first,
                            label="From end of",
                        )
                        .props("dense outlined")
                        .classes("w-36")
                    )
                    self.end = (
                        ui.select(opts, value=prev if prev in opts else last, label="To end of")
                        .props("dense outlined")
                        .classes("w-36")
                    )
                    controls = [self.start, self.end]
                else:
                    controls = []
            self.plot = ui.plotly(go.Figure()).classes("w-full")
        for c in controls:
            c.on_value_change(self.render)
        self.render()

    # ------------------------------------------------------------------ figures
    def render(self) -> None:
        cur = self.currency["value"]
        try:
            fig = getattr(self, f"_fig_{self.kind}")(cur)
        except Exception as exc:
            error(f"Chart error: {exc}")
            return
        self.plot.update_figure(fig)

    def _fig_wealth(self, cur: str) -> go.Figure:
        series = wealth_svc.wealth_series(self.ctx, cur)
        self.data = pd.DataFrame(
            [{"month": m, "wealth": round(v, 2), "type": "projected" if p else "actual"} for m, v, p in series]
        )
        fig = go.Figure()
        actual = [(m, v) for m, v, p in series if not p]
        projected = [(m, v) for m, v, p in series if p]
        if actual and projected:
            projected.insert(0, actual[-1])  # connect the projection to the last actual value
        if actual:
            fig.add_scatter(
                x=[m for m, _ in actual],
                y=[v for _, v in actual],
                name="Actual wealth",
                mode="lines+markers",
                line=dict(color=PALETTE[0], width=2.5),
            )
        if projected:
            fig.add_scatter(
                x=[m for m, _ in projected],
                y=[v for _, v in projected],
                name="Projected wealth",
                mode="lines",
                line=dict(color=PALETTE[1], width=2, dash="dash"),
            )
        return _layout(fig, "Wealth", cur)

    def _fig_flows(self, cur: str) -> go.Figure:
        start, end = sorted([self.start.value, self.end.value])
        keys = self.series.value or []
        rows = reports.monthly_series(self.ctx, start, end, cur, keys, include_budget=bool(self.budget.value))
        self.data = pd.DataFrame(rows)
        fig = go.Figure()
        cols = [c for c in self.data.columns if c != "month"]
        for i, col in enumerate(cols):
            is_budget = col.endswith("(budget)")
            color = PALETTE[(i // 2 if self.budget.value else i) % len(PALETTE)]
            if self.style.value == "bar":
                fig.add_bar(
                    x=self.data["month"],
                    y=self.data[col],
                    name=col,
                    marker_color=color,
                    opacity=0.45 if is_budget else 1.0,
                )
            else:
                fig.add_scatter(
                    x=self.data["month"],
                    y=self.data[col],
                    name=col,
                    mode="lines+markers",
                    line=dict(color=color, dash="dot" if is_budget else "solid"),
                )
        fig.update_layout(barmode="group")
        fig.update_xaxes(
            type="category",
            tickvals=list(self.data["month"]) if not self.data.empty else None,
            ticktext=[months.label(m) for m in self.data["month"]] if not self.data.empty else None,
        )
        return _layout(fig, f"Income & expenditure {months.label(start)} – {months.label(end)}", cur)

    def _fig_waterfall(self, cur: str) -> go.Figure:
        start, end = sorted([self.start.value, self.end.value])
        changes = reports.account_changes(self.ctx, start, end, cur)
        self.data = pd.DataFrame(changes)
        w0 = sum(c["start"] for c in changes)
        w1 = sum(c["end"] for c in changes)
        x = [f"Wealth {months.label(start)}"] + [c["account"] for c in changes] + [f"Wealth {months.label(end)}"]
        y = [w0] + [c["change"] for c in changes] + [w1]
        fig = go.Figure(
            go.Waterfall(
                x=x,
                y=y,
                measure=["absolute"] + ["relative"] * len(changes) + ["total"],
                connector=dict(line=dict(color="#c5cdd8")),
                increasing=dict(marker=dict(color=PALETTE[2])),
                decreasing=dict(marker=dict(color=PALETTE[4])),
                totals=dict(marker=dict(color=PALETTE[0])),
            )
        )
        fig = _layout(fig, "Change per account", cur)
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
    def download(self, kind: str) -> None:
        name = f"{self.kind}_chart_{self.currency['value']}"
        if kind == "csv":
            ui.download.content(export_svc.frame_csv(self.data), f"{name}.csv")
        else:
            ui.download.content(export_svc.frame_xlsx(self.data, self.kind), f"{name}.xlsx")

    def download_png(self) -> None:
        self.plot.run_plot_method(
            "downloadImage",
            {"format": "png", "filename": f"{self.kind}_chart", "width": 1200, "height": 600, "scale": 2},
        )


def page() -> None:
    with frame("Overview", "/") as ctx:
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
                error(f"Cannot compute wealth: {exc}")
                return
            with ui.row().classes("w-full gap-4 flex-wrap"):
                for title, value, sub in (
                    ("Current wealth", current, f"end of {months.label(months.previous_month())}"),
                    ("Projected wealth", projected, f"end of {months.label(horizon)} (budget horizon)"),
                    ("Projected change", projected - current, "until the budget horizon"),
                ):
                    with ui.card().classes("shadow-sm min-w-[14rem] flex-1"):
                        ui.label(title).classes("text-xs uppercase tracking-wide text-slate-500")
                        ui.label(fmt(value, cur)).classes("text-3xl font-semibold text-slate-800")
                        ui.label(sub).classes("text-xs text-slate-400")

        def set_currency(e) -> None:
            currency["value"] = e.value
            kpis.refresh()
            for c in charts:
                c.render()

        with ui.row().classes("items-center gap-3"):
            currency_select(ctx, currency["value"], on_change=set_currency, label="Currency")
        kpis()

        with card("Charts"):
            with ui.row().classes("items-center gap-2"):
                kind = (
                    ui.select(CHART_TYPES, value="wealth", label="Chart type").props("dense outlined").classes("w-80")
                )
                ui.button("Add chart", icon="add_chart", on_click=lambda: add(kind.value))
            grid = ui.element("div").classes("w-full grid grid-cols-1 xl:grid-cols-2 gap-4")

        def remove(chart: ChartCard) -> None:
            charts.remove(chart)
            grid.remove(chart.card)

        def add(k: str) -> None:
            charts.append(ChartCard(ctx, k, currency, grid, remove))

        add("wealth")
        add("flows")
