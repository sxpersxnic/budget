"""Shared page frame (task bar), formatting and notification helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

from nicegui import run, ui

from budget_app.domain import months
from budget_app.services.context import Context, get_ctx

NAV = [
    ("/", "Overview", "dashboard"),
    ("/budget", "Budget overview", "account_balance_wallet"),
    ("/add-transaction", "Add transaction", "add_card"),
    ("/transactions", "Transactions", "receipt_long"),
    ("/add-budget", "Add budget entry", "playlist_add"),
    ("/budget-entries", "Budget entries", "list_alt"),
    ("/settings", "Settings", "settings"),
]


def fmt(value: float | None, currency: str = "") -> str:
    """Swiss-style number formatting: 1'234.50 CHF."""
    if value is None:
        return "–"
    text = f"{value:,.2f}".replace(",", "'")
    return f"{text} {currency}".strip()


async def refresh_rates(ctx: Context) -> None:
    """Download the ECB file if it has not been downloaded today."""
    svc, settings = ctx.fx_service, ctx.store.settings
    if svc is None:
        return
    downloaded = await run.io_bound(svc.refresh_if_stale, settings.last_fx_download)
    if downloaded:
        settings.last_fx_download = date.today().isoformat()
        ctx.store.save_settings()


@contextmanager
def frame(title: str, path: str):
    ctx = get_ctx()
    ui.colors(primary="#2f5d8a", secondary="#5b8cbf", accent="#c77d2e", positive="#2e7d4f", negative="#b3261e")
    ui.query("body").style("background-color: #f4f6f9")
    with ui.header().classes("bg-white text-slate-800 shadow-sm px-4 py-1 items-center gap-1 flex-wrap"):
        ui.icon("savings", size="28px").classes("text-primary")
        ui.label("Budget").classes("text-lg font-bold mr-4 text-primary")
        for target, label, icon in NAV:
            btn = ui.button(label, icon=icon, on_click=lambda t=target: ui.navigate.to(t)).props("flat no-caps dense")
            btn.classes("px-2 " + ("text-primary bg-blue-50 font-semibold" if target == path else "text-slate-600"))
        ui.space()
        latest = ctx.fx.latest_date()
        ui.label(f"ECB rates: {latest.isoformat() if latest else 'none'}").classes("text-xs text-slate-400")
    if ctx.fx_service and ctx.fx_service.last_error:
        with ui.row().classes("w-full bg-amber-50 text-amber-900 px-4 py-2 text-sm items-center"):
            ui.icon("warning")
            ui.label(ctx.fx_service.last_error)
    with ui.column().classes("w-full max-w-7xl mx-auto px-4 py-4 gap-4"):
        ui.label(title).classes("text-2xl font-semibold text-slate-800")
        yield ctx


def card(title: str | None = None):
    c = ui.card().classes("w-full shadow-sm")
    with c:
        if title:
            ui.label(title).classes("text-lg font-semibold text-slate-700")
    return c


def error(message: str) -> None:
    ui.notify(message, type="negative", position="top")


def success(message: str) -> None:
    ui.notify(message, type="positive", position="top")


def month_options(start: str, end: str) -> dict[str, str]:
    return {m: months.label(m) for m in months.month_range(start, end)}


def data_month_range(ctx: Context, back: int = 24) -> tuple[str, str]:
    """A sensible month range for selectors: earliest data (or `back` months ago) to the horizon."""
    candidates = [months.add_months(months.current_month(), -back)]
    candidates += [months.month_of(t.date) for t in ctx.store.transactions.all()]
    candidates += [e.month for e in ctx.store.account_entries.all()]
    candidates += [b.month for b in ctx.store.budget.all()]
    return min(candidates), max(ctx.horizon(), months.current_month())


def date_input(label: str, value: str = "", on_change=None) -> ui.input:
    """Text input with a date picker popup (value 'YYYY-MM-DD')."""
    with ui.input(label, value=value, on_change=on_change).props("dense outlined") as inp:
        with (
            ui.menu().props("no-parent-event") as menu,
            ui.date().bind_value(inp),
            ui.row().classes("justify-end"),
        ):
            ui.button("Close", on_click=menu.close).props("flat")
        with inp.add_slot("append"):
            ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
    return inp


def parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None
