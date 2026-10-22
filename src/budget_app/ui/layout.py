"""Shared page frame (sidebar), formatting, dialogs and notification helpers."""

from __future__ import annotations

import html
import logging
import weakref
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from nicegui import app, run, ui
from nicegui.client import Client
from nicegui.events import KeyEventArguments

from budget_app import config, i18n
from budget_app.domain import months
from budget_app.i18n import N_, t
from budget_app.services import bills as bills_svc
from budget_app.services import recurring as recurring_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import Context, ValidationError, get_ctx
from budget_app.ui import theme

log = logging.getLogger(__name__)

# Places only. Creating things ("Add transaction", "Add budget entry") is an action, offered where it is needed.
NAV = [
    ("/", N_("Overview"), "o_space_dashboard"),
    ("/budget", N_("Budget"), "o_donut_large"),
    ("/transactions", N_("Transactions"), "o_receipt_long"),
    ("/recurring", N_("Recurring"), "o_event_repeat"),
    ("/bills", N_("Bills"), "o_request_quote"),
    ("/budget-entries", N_("Budget Plan"), "o_event_note"),
    ("/categories", N_("Categories"), "o_account_tree"),
    ("/payees", N_("Payees"), "o_storefront"),
    ("/tags", N_("Tags"), "o_sell"),
    ("/investments", N_("Investments"), "o_show_chart"),
]
SETTINGS = ("/settings", N_("Settings"), "o_settings")
# Routes that are not in the sidebar highlight their parent.
NAV_PARENT = {
    "/add-transaction": "/transactions",
    "/import": "/transactions",
    "/add-budget": "/budget-entries",
    "/history": "/settings",
    "/rules": "/payees",
}

_data_listeners: weakref.WeakKeyDictionary[Client, Callable[[], object]] = weakref.WeakKeyDictionary()
# per page: the sidebar badges by route (due recurring transactions, bills due soon)
_due_badges: weakref.WeakKeyDictionary[Client, dict[str, ui.badge]] = weakref.WeakKeyDictionary()
BADGES = {
    "/recurring": N_("Due recurring transactions"),
    "/bills": N_("Bills due within a week or overdue"),
}


def fmt(value: float | None, currency: str = "") -> str:
    """Swiss-style number formatting: 1'234.50 CHF."""
    if value is None:
        return "–"
    value = round(value, 2) + 0.0  # −0.004 shows as 0.00, not −0.00
    text = f"{value:,.2f}".replace(",", "'").replace("-", "−")
    return f"{text} {currency}".strip()


def fmt_signed(value: float | None) -> str:
    if value is None or abs(value) < 0.005:
        return ""
    return ("+" if value > 0 else "") + fmt(value)


def money_html(value: float | None, currency: str) -> str:
    """Amount with the currency code set smaller and lighter, so the number reads first."""
    return f'{html.escape(fmt(value))}<span class="cur">{html.escape(currency)}</span>'


async def refresh_rates(ctx: Context) -> None:
    """Download the ECB file if it has not been downloaded today, then value transactions with it."""
    svc, settings = ctx.fx_service, ctx.store.settings
    if svc is None:
        return
    downloaded = await run.io_bound(svc.refresh_if_stale, settings.last_fx_download)
    if downloaded:
        settings.last_fx_download = date.today().isoformat()
        ctx.store.save_settings()
    # also on the first run after an update: entries saved by older versions used the latest rate
    changed = await run.io_bound(tx_svc.revalue_to_transaction_dates, ctx)
    if changed:
        log.info("Valued %d transactions at the rate of their date", changed)


def book_recurring(ctx: Context) -> int:
    """Book what recurring transactions that book automatically have due (F05), after the rate check."""
    booking = recurring_svc.book_automatic(ctx, date.today())
    if booking.booked:
        log.info("Booked %d recurring transactions", len(booking.booked))
    return len(booking.booked)


# ------------------------------------------------------------------ data change notification
def on_data_changed(callback: Callable[[], object]) -> None:
    """Let the current page refresh itself when a sheet opened from the sidebar saves something."""
    _data_listeners[ui.context.client] = callback


def data_changed() -> None:
    callback = _data_listeners.get(ui.context.client)
    if callback:
        callback()
    refresh_due_badge()


def refresh_due_badge() -> None:
    """Show the number of due recurring transactions beside "Recurring" and of the open bills due within a week
    or overdue beside "Bills" (F03) in the sidebar."""
    badges = _due_badges.get(ui.context.client, {})
    counts = {"/recurring": recurring_svc.due_count, "/bills": bills_svc.due_count}
    for target, badge in badges.items():
        n = counts[target](get_ctx(), date.today())
        badge.set_text(str(n))
        badge.set_visibility(n > 0)


# ------------------------------------------------------------------ frame
def _nav_item(target: str, label: str, icon: str, active: bool) -> None:
    with (
        ui.link(target=target)
        .classes("nav-item" + (" active" if active else ""))
        .props('aria-current="page"' if active else "")
    ):
        ui.icon(icon)
        ui.label(t(label))
        if target in BADGES:
            ui.space()
            badge = ui.badge().props("rounded").classes("nav-badge")
            badge.tooltip(t(BADGES[target]))
            _due_badges.setdefault(ui.context.client, {})[target] = badge
            refresh_due_badge()


def _new_transaction() -> None:
    from budget_app.ui.widgets.transaction_form import open_transaction_sheet  # noqa: PLC0415 - avoids an import cycle

    open_transaction_sheet(get_ctx(), on_saved=data_changed)


def _page_shortcut(e: KeyEventArguments) -> None:
    """N opens a new transaction on every page (F06)."""
    mods = e.modifiers
    plain = not (mods.ctrl or mods.meta or mods.alt)
    if e.action.keydown and not e.action.repeat and e.key.name in ("n", "N") and plain:
        _new_transaction()


@contextmanager
def frame(title: str, path: str, subtitle: str | None = None, narrow: bool = False, wide: bool = False):
    """Page skeleton. Yields (ctx, actions): `actions` is the row beside the title for page-level buttons.

    On small screens the large title gives way to the compact header, and the actions wrap below it.
    """
    ctx = get_ctx()
    theme.apply_colors()
    ui.page_title(f"{title} – Budget")
    ui.keyboard(on_key=_page_shortcut)  # ignores keys typed into fields
    current = NAV_PARENT.get(path, path)
    with (
        ui.left_drawer().props("width=232 :breakpoint=1023").classes("sidebar") as drawer,
        ui.column().classes("w-full h-full gap-0 px-3 pt-4 pb-3 no-wrap"),
    ):
        with ui.row().classes("items-center gap-2 px-2 pb-4"):
            ui.icon("savings", size="22px").classes("text-primary")
            ui.label("Budget").classes("text-[15px] font-semibold")
        ui.button(t("New Transaction"), icon="add", on_click=_new_transaction).props("unelevated no-wrap").classes(
            "w-full bg-primary text-white mb-4"
        )
        for target, label, icon in NAV:
            _nav_item(target, label, icon, target == current)
        ui.space()
        _nav_item(*SETTINGS, SETTINGS[0] == current)
        latest = ctx.fx.latest_date()
        with ui.column().classes("gap-0 px-2 pt-3"):
            ui.label(f"Budget {config.app_version()}").classes("nav-footer")
            ui.label(
                t("ECB rates of {date}", date=i18n.date_label(latest)) if latest else t("No exchange rates yet")
            ).classes("nav-footer")
    with ui.header().classes("mobile-bar lg:hidden px-2 py-1 items-center gap-1"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense").classes("text-primary")
        ui.label(title).classes("font-semibold text-[15px]")
        ui.space()
        ui.button(icon="add", on_click=_new_transaction).props("flat round dense").classes("text-primary").tooltip(
            t("New Transaction")
        )
    if ctx.fx_service and ctx.fx_service.last_error:
        with ui.row().classes("w-full px-8 pt-4 max-sm:px-4"), ui.element("div").classes("callout"):
            ui.icon("o_cloud_off")
            ui.label(ctx.fx_service.last_error)
    with ui.column().classes("page" + (" narrow" if narrow else "") + (" wide" if wide else "")):
        with ui.row().classes("w-full items-end gap-3 flex-wrap"):
            with ui.column().classes("gap-0"):
                ui.label(title).classes("large-title max-lg:hidden")
                if subtitle:
                    ui.label(subtitle).classes("title-sub")
            ui.space()
            actions = ui.row().classes("items-center gap-2 flex-wrap")
        yield ctx, actions


def card(title: str | None = None, classes: str = ""):
    c = ui.element("div").classes("surface " + classes)
    with c:
        if title:
            ui.label(title).classes("section-title pb-2")
    return c


@contextmanager
def sheet(title: str, width: str = "30rem"):
    """Modal sheet with a title; yields the dialog. Close it with dlg.close().

    A closed sheet removes itself from the page. That happens on Quasar's 'hide' event, after the
    closing animation, so code that still reads or resets the form right after close() works.
    """
    with ui.dialog() as dlg, ui.card().classes("sheet").style(f"width: {width}; max-width: 94vw"):
        ui.label(title).classes("sheet-title")
        yield dlg
    dlg.on("hide", dlg.delete)
    dlg.open()


def sheet_buttons(dlg: ui.dialog, label: str, on_click, enabled: bool = True) -> ui.button:
    with ui.row().classes("w-full justify-end gap-2 pt-1"):
        ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
        btn = ui.button(label, on_click=on_click).props("unelevated")
    btn.set_enabled(enabled)
    return btn


def segmented(options: dict, value, on_change=None, full: bool = False) -> ui.toggle:
    toggle = ui.toggle(options, value=value, on_change=on_change).props("no-caps unelevated spread=false")
    return toggle.classes("segmented" + (" full" if full else ""))


def metric(label: str, value: float | None, currency: str, sub: str = "", hero: bool = False) -> None:
    with ui.element("div").classes("metric"):
        ui.label(label).classes("metric-label")
        ui.html(money_html(value, currency)).classes("metric-value" + (" hero" if hero else ""))
        if sub:
            ui.label(sub).classes("metric-sub")


def month_stepper(options: dict[str, str], value: str, on_change: Callable[[str], object]) -> ui.select:
    """‹ Month › stepper with a menu for jumping; months are the common unit of navigation here."""
    keys = list(options)

    def step(delta: int) -> None:
        i = keys.index(sel.value) + delta if sel.value in keys else 0
        if 0 <= i < len(keys):
            sel.value = keys[i]

    with ui.row().classes("items-center gap-0 no-wrap"):
        ui.button(icon="chevron_left", on_click=lambda: step(-1)).props("flat round dense").classes(
            "text-primary"
        ).tooltip(t("Previous month"))
        sel = (
            ui.select(options, value=value, on_change=lambda e: on_change(e.value))
            .props("borderless dense options-dense hide-dropdown-icon")
            .classes("font-semibold text-[15px] text-center")
        )
        ui.button(icon="chevron_right", on_click=lambda: step(1)).props("flat round dense").classes(
            "text-primary"
        ).tooltip(t("Next month"))
    return sel


def error(message: str) -> None:
    ui.notify(message, type="negative", position="bottom", timeout=4000)


def success(message: str) -> None:
    ui.notify(message, position="bottom", icon="check_circle", color="grey-9", timeout=1800)


def undoable(message: str) -> None:
    """Confirm a change, with an Undo button for the action that was just recorded (F28)."""
    ctx = get_ctx()
    act = undo_svc.latest(ctx)
    if act is None or act.undone:
        success(message)
        return
    note = ui.notification(message, position="bottom", icon="check_circle", color="grey-9", timeout=8)
    # Quasar calls the action in the browser; it hands the click to the server as an 'undo' event
    handler = f"() => getElement({note.id}).$emit('undo')"
    note.props["options"]["actions"] = [{"label": t("Undo"), "color": "amber-3", "noCaps": True, ":handler": handler}]
    note.update()

    def undo() -> None:
        try:
            undo_svc.undo(ctx, act.id)
        except ValidationError as exc:
            error(str(exc))
            return
        success(t("Undone: {action}", action=undo_svc.label(ctx, act)))
        ui.navigate.reload()  # every list and total on the page follows the restored records

    note.on("undo", undo)


MEDIA_TYPES = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".png": "image/png",
}


async def save_file(data: bytes, filename: str) -> None:
    """Hand a file to the user: the system save dialog in the app window, a download in the browser.

    The app window (pywebview) drops browser downloads, so there the file is written from Python.
    """
    window = app.native.main_window
    if window is None:
        ui.download.content(data, filename, MEDIA_TYPES.get(Path(filename).suffix, ""))
        return
    import webview  # noqa: PLC0415 - only installed with the native extra, the only way to get a window

    save = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    downloads = Path.home() / "Downloads"
    chosen = await window.create_file_dialog(
        save, directory=str(downloads if downloads.is_dir() else Path.home()), save_filename=filename
    )
    if not chosen:
        return  # cancelled
    path = Path(chosen if isinstance(chosen, str) else chosen[0])
    try:
        await run.io_bound(path.write_bytes, data)
    except OSError as exc:
        error(t("Could not save {name}: {error}", name=path.name, error=exc))
        return
    success(t("Saved “{name}”", name=path.name))


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
            ui.date().props("flat minimal first-day-of-week=1").bind_value(inp),
        ):
            pass  # the picker writes into the input through the binding
        with inp.add_slot("append"):
            ui.icon("o_calendar_today", size="18px").on("click", menu.open).classes("cursor-pointer text-primary")
    return inp


def parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None
