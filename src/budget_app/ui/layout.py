"""Shared page frame (sidebar), formatting, dialogs and notification helpers."""

from __future__ import annotations

import html
import logging
import weakref
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from nicegui import app, events, run, ui
from nicegui.client import Client
from nicegui.events import KeyEventArguments

from budget_app import config, i18n
from budget_app.domain import months
from budget_app.i18n import N_, t
from budget_app.services import bills as bills_svc
from budget_app.services import loans as loan_svc
from budget_app.services import profiles as profiles_svc
from budget_app.services import recurring as recurring_svc
from budget_app.services import reminders as reminders_svc
from budget_app.services import security, sync
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import updates as updates_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError, get_ctx
from budget_app.ui import theme

log = logging.getLogger(__name__)

# Places only. Creating things ("Add transaction", "Add budget entry") is an action, offered where it is needed.
# Seven places (UX review before 1.0); a place with several pages shows them as tabs under its title (SECTIONS).
NAV = [
    ("/", N_("Overview"), "o_space_dashboard"),
    ("/budget", N_("Budget"), "o_donut_large"),
    ("/transactions", N_("Transactions"), "o_receipt_long"),
    ("/budget-entries", N_("Plan"), "o_event_note"),
    ("/accounts", N_("Wealth"), "o_account_balance_wallet"),
    ("/review", N_("Reports"), "o_insert_chart"),  # the yearly pages: opened a few times a year
]
SETTINGS = ("/settings", N_("Settings"), "o_settings")
# The pages of a place, in their order, shown as tabs under the title; the first is the place's own page.
SECTIONS = {
    "/transactions": [
        ("/transactions", N_("Transactions")),
        ("/recurring", N_("Recurring")),
        ("/bills", N_("Bills")),
    ],
    "/budget-entries": [
        ("/budget-entries", N_("Budget Plan")),
        ("/goals", N_("Goals")),
        ("/scenarios", N_("Scenarios")),
    ],
    "/accounts": [
        ("/accounts", N_("Accounts")),
        ("/investments", N_("Investments")),
        ("/loans", N_("Loans")),
        ("/shared", N_("Shared Expenses")),
    ],
    "/review": [
        ("/review", N_("Year in Review")),
        ("/tax", N_("Tax Summary")),
    ],
    "/settings": [
        ("/settings", N_("General")),
        ("/categories", N_("Categories")),
        ("/payees", N_("Payees")),
        ("/tags", N_("Tags")),
    ],
}
# Pages that are no tab highlight the tab they belong to.
TAB_PARENT = {
    "/add-transaction": "/transactions",
    "/import": "/transactions",
    "/add-budget": "/budget-entries",
    "/history": "/settings",
    "/rules": "/payees",
}
# Every route that is not a place highlights its place in the sidebar.
NAV_PARENT = {tab: place for place, tabs in SECTIONS.items() for tab, _ in tabs if tab != place}
NAV_PARENT |= {route: NAV_PARENT.get(tab, tab) for route, tab in TAB_PARENT.items()}

# F32: the keys the shortcuts sheet lists, in its order. They work wherever the cursor is not in a field.
SHORTCUTS = [
    ("N", N_("New Transaction")),
    ("/", N_("Search this page")),
    ("← →", N_("Previous or next month")),
    (f"1 – {len(NAV)}", N_("The places in the sidebar, in their order")),
    (",", N_("Settings")),
    ("Ctrl/⌘ Enter", N_("Save the open sheet")),
    ("Esc", N_("Close the open sheet")),
    ("?", N_("Keyboard Shortcuts")),
]
SEARCH_FOCUS = "document.querySelector('.search input')?.focus()"  # every page's search field has the class
# the sheet keeps its Ctrl/⌘ Enter, so a sheet opened from a form doesn't also save the form
SAVE_KEYS = (
    "(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); emit(); } }"
)

_data_listeners: weakref.WeakKeyDictionary[Client, Callable[[], object]] = weakref.WeakKeyDictionary()
# per page: the month stepper the arrow keys move (F32), and the sheets (page keys wait while one is open)
_month_steps: weakref.WeakKeyDictionary[Client, Callable[[int], None]] = weakref.WeakKeyDictionary()
_sheets: weakref.WeakKeyDictionary[Client, weakref.WeakSet[ui.dialog]] = weakref.WeakKeyDictionary()
# per page: the badges by route (the Overview's Due list in the sidebar; due recurring, bills, loans on their tabs)
_due_badges: weakref.WeakKeyDictionary[Client, dict[str, ui.badge]] = weakref.WeakKeyDictionary()
BADGES = {
    "/": N_("Things to do"),  # in the sidebar: exactly the rows of the Overview's Due list (reminders.count)
    "/recurring": N_("Due recurring transactions"),  # the others on their tabs
    "/bills": N_("Bills due within a week or overdue"),
    "/loans": N_("Due loan payments"),
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
    balances = await run.io_bound(wealth_svc.revalue_entries, ctx)
    if balances:
        log.info("Valued %d account balances at the rate of their month's end", balances)


async def check_updates(ctx: Context) -> None:
    """Ask for the newest release when the user has turned the check on and it wasn't done today (F33)."""
    await run.io_bound(updates_svc.check_if_due, ctx)


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


def records_reloaded() -> None:
    """F26: the records were read again after another computer changed them; every open page is told, and pages
    that refresh themselves do."""
    for client in list(Client.instances.values()):
        if not client.has_socket_connection:
            continue
        with client:
            ui.notify(t("Records changed on another computer were read again."), type="info")
            callback = _data_listeners.get(client)
            if callback:
                callback()


def _open_elsewhere() -> None:
    """F26: a warning while another computer has the same folder open (a cloud folder)."""
    for other in sync.others():
        since = other.since_at
        with ui.row().classes("w-full px-8 pt-4 max-sm:px-4"), ui.element("div").classes("callout"):
            ui.icon("o_devices")
            ui.label(
                t(
                    "Budget is open on {computer} too, since {time}. Close it there before you change anything here: "
                    "changes made on two computers at the same time overwrite each other.",
                    computer=other.host,
                    time=f"{i18n.date_label(since.date())}, {since:%H:%M}",
                )
            )


def refresh_due_badge() -> None:
    """Show how many things are due beside "Overview" in the sidebar, and on their tabs the due recurring
    transactions (Recurring), the open bills due within a week or overdue (Bills, F03) and the due loan payments
    (Loans, F22)."""
    badges = _due_badges.get(ui.context.client, {})
    counts = {
        "/": reminders_svc.count,
        "/recurring": recurring_svc.due_count,
        "/bills": bills_svc.due_count,
        "/loans": loan_svc.due_count,
    }
    for target, badge in badges.items():
        n = counts[target](get_ctx(), date.today())
        badge.set_text(str(n))
        badge.set_visibility(n > 0)


def _badge(target: str) -> None:
    """The count beside a place or tab that has one (BADGES)."""
    if target in BADGES:
        badge = ui.badge().props("rounded").classes("nav-badge")
        badge.tooltip(t(BADGES[target]))
        _due_badges.setdefault(ui.context.client, {})[target] = badge
        refresh_due_badge()


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
            _badge(target)


def _section_tabs(place: str, path: str) -> None:
    """The pages of a place as tabs under the title; the open one is marked."""
    current = TAB_PARENT.get(path, path)
    with ui.element("nav").classes("section-tabs") as nav:
        nav.props["aria-label"] = t(next(label for target, label, _ in [*NAV, SETTINGS] if target == place))
        for target, label in SECTIONS[place]:
            active = target == current
            with (
                ui.link(target=target)
                .classes("section-tab" + (" active" if active else ""))
                .props('aria-current="page"' if active else "")
            ):
                ui.label(t(label))
                _badge(target)


def _new_transaction() -> None:
    from budget_app.ui.widgets.transaction_form import open_transaction_sheet  # noqa: PLC0415 - avoids an import cycle

    open_transaction_sheet(get_ctx(), on_saved=data_changed)


def lock_now() -> None:
    """Lock the app (F27): every browser and phone has to pass the lock again."""
    security.lock()
    ui.navigate.to("/lock")


def open_profile(profile_id: str) -> None:
    """Switch to another profile (F34): its folder opens, and every open page starts over on its Overview."""
    try:
        profile = profiles_svc.switch(profile_id)
    except ValidationError as exc:
        error(str(exc))
        return
    for client in list(Client.instances.values()):
        if client is not ui.context.client and client.has_socket_connection:
            with client:
                ui.navigate.to("/")  # pages still showing the other profile's records
    success(t("Opened {name}", name=profile.name))
    ui.navigate.to("/")


def _brand() -> None:
    """The app's name above the sidebar; with several profiles (F34), the open profile's name and a menu of the
    others."""
    reg = profiles_svc.load()
    with ui.row().classes("items-center gap-2 px-2 pb-4 no-wrap"):
        ui.icon("savings", size="22px").classes("text-primary")
        if len(reg.profiles) < 2:
            ui.label("Budget").classes("text-[15px] font-semibold")
            return
        with (
            ui.button(reg.active.name)
            .props("flat dense no-caps align=left icon-right=unfold_more")
            .classes("profile-button text-[15px] font-semibold min-w-0 flex-1")
            .tooltip(t("Profiles")),
            ui.menu().props("auto-close"),
        ):
            for p in reg.profiles:
                with ui.menu_item(on_click=lambda p=p: open_profile(p.id)).classes("items-center gap-2"):
                    ui.icon("check" if p.id == reg.current else "", size="18px").classes("text-primary w-5")
                    ui.label(p.name)
            ui.separator()
            ui.menu_item(t("Profiles…"), on_click=lambda: ui.navigate.to("/settings"))


def _page_shortcut(e: KeyEventArguments) -> None:
    """The page keys of SHORTCUTS: N opens a new transaction on every page (F06), the others are F32's."""
    mods = e.modifiers
    if not e.action.keydown or e.action.repeat or mods.ctrl or mods.meta or mods.alt:
        return
    client = ui.context.client
    if any(d.value and not d.is_deleted for d in _sheets.get(client, ())):
        return  # a sheet has the keys; Quasar closes it with Esc
    key = e.key.name
    if key in ("n", "N"):
        _new_transaction()
    elif key == "/":
        ui.run_javascript(SEARCH_FOCUS)
    elif key == "?":
        open_shortcuts()
    elif key in ("ArrowLeft", "ArrowRight") and client in _month_steps:
        _month_steps[client](-1 if key == "ArrowLeft" else 1)
    elif key.isdigit() and 1 <= int(key) <= len(NAV):
        ui.navigate.to(NAV[int(key) - 1][0])
    elif key == ",":
        ui.navigate.to(SETTINGS[0])


def open_shortcuts() -> None:
    """The keyboard shortcuts, as a sheet (F32)."""
    with sheet(t("Keyboard Shortcuts"), width="26rem") as dlg:
        with ui.element("div").classes("shortcuts w-full"):
            for keys, label in SHORTCUTS:
                ui.html(" ".join(f"<kbd>{html.escape(k)}</kbd>" for k in keys.split())).classes("keys")
                ui.label(t(label))
        with ui.row().classes("w-full justify-end pt-1"):
            ui.button(t("Done"), on_click=dlg.close).props("unelevated")


def picks_files(trigger: ui.element, upload: ui.upload) -> ui.element:
    """Open the system file dialog for `upload` when `trigger` is clicked, in the browser itself.

    A browser opens a file dialog only while it handles the user's click. Asking the server first
    (`upload.run_method("pickFiles")`) loses that permission: Chrome often still allows it, WebKit (the macOS
    window) shows nothing.
    """
    return trigger.on("click", js_handler=f"() => runMethod({upload.id}, 'pickFiles', [])")


def named(button: ui.button, label: str) -> ui.button:
    """Name an icon-only button: the tooltip for the eye, `aria-label` for screen readers (F32)."""
    button.props["aria-label"] = label
    return button.tooltip(label)


@contextmanager
def frame(title: str, path: str, subtitle: str | None = None, narrow: bool = False, wide: bool = False):
    """Page skeleton. Yields (ctx, actions): `actions` is the row beside the title for page-level buttons.

    On small screens the large title gives way to the compact header, and the actions wrap below it.
    """
    ctx = get_ctx()
    theme.apply_colors()
    ui.page_title(f"{title} – Budget")
    ui.keyboard(on_key=_page_shortcut)  # ignores keys typed into fields
    _month_steps.pop(ui.context.client, None)
    skip = ui.link(t("Skip to content"), "#").classes("skip-link")
    current = NAV_PARENT.get(path, path)
    with (
        ui.left_drawer().props("width=232 :breakpoint=1023 role=navigation").classes("sidebar") as drawer,
        ui.column().classes("w-full h-full gap-0 px-3 pt-4 pb-3 no-wrap"),
    ):
        _brand()
        drawer.props["aria-label"] = t("Places")
        ui.button(t("New Transaction"), icon="add", on_click=_new_transaction).props("unelevated no-wrap").classes(
            "w-full bg-primary text-white mb-4"
        )
        for target, label, icon in NAV:
            _nav_item(target, label, icon, target == current)
        ui.space()
        _nav_item(*SETTINGS, SETTINGS[0] == current)
        latest = ctx.fx.latest_date()
        with ui.row().classes("items-end no-wrap px-2 pt-3 gap-1"):
            with ui.column().classes("gap-0 flex-1 min-w-0 items-start"):
                ui.label(f"Budget {config.app_version()}").classes("nav-footer")
                if ctx.store.settings.sample_data:  # F30: the folder holds the sample set
                    ui.label(t("Sample data")).classes("nav-footer")
                ui.label(
                    t("ECB rates of {date}", date=i18n.date_label(latest)) if latest else t("No exchange rates yet")
                ).classes("nav-footer")
                release = updates_svc.available(ctx)  # F33: only after a check the user turned on
                if release:
                    ui.link(
                        t("Budget {version} is available", version=release.version), release.url, new_tab=True
                    ).classes("nav-footer text-primary").tooltip(
                        t(
                            "Opens the release page with the installers and the update steps. "
                            "Nothing is downloaded by the app."
                        )
                    )
                ui.button(t("Keyboard Shortcuts"), on_click=open_shortcuts).props("flat dense no-caps").classes(
                    "nav-footer footer-link"
                )
            if security.required():  # F27: lock now, from every page
                named(
                    ui.button(icon="o_lock", on_click=lock_now)
                    .props("flat round dense size=sm")
                    .classes("text-primary"),
                    t("Lock Now"),
                )
    with ui.header().classes("mobile-bar lg:hidden px-2 py-1 items-center gap-1"):
        menu = ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense").classes("text-primary")
        named(menu, t("Menu"))
        ui.label(title).classes("font-semibold text-[15px]")
        ui.space()
        named(
            ui.button(icon="add", on_click=_new_transaction).props("flat round dense").classes("text-primary"),
            t("New Transaction"),
        )
    if ctx.fx_service and ctx.fx_service.last_error:
        with ui.row().classes("w-full px-8 pt-4 max-sm:px-4"), ui.element("div").classes("callout"):
            ui.icon("o_cloud_off")
            ui.label(ctx.fx_service.last_error)
    _open_elsewhere()
    with (
        ui.column()
        .classes("page" + (" narrow" if narrow else "") + (" wide" if wide else ""))
        .props("role=main tabindex=-1") as main
    ):
        skip.props["href"] = f"#{main.html_id}"
        with ui.row().classes("w-full items-end gap-3 flex-wrap"):
            with ui.column().classes("gap-0"):
                ui.label(title).classes("large-title max-lg:hidden")
                if subtitle:
                    ui.label(subtitle).classes("title-sub")
            ui.space()
            actions = ui.row().classes("items-center gap-2 flex-wrap")
        place = NAV_PARENT.get(path, path)
        if place in SECTIONS:
            _section_tabs(place, path)
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
    with (
        ui.dialog() as dlg,
        ui.card().classes("sheet").style(f"width: {width}; max-width: 94vw").props("role=dialog aria-modal=true"),
    ):
        ui.label(title).classes("sheet-title")
        yield dlg
    dlg.on("hide", dlg.delete)
    _sheets.setdefault(ui.context.client, weakref.WeakSet()).add(dlg)
    dlg.open()


def sheet_buttons(dlg: ui.dialog, label: str, on_click, enabled: bool = True) -> ui.button:
    """Cancel and the sheet's action; Ctrl/⌘ Enter anywhere in the sheet presses the action (F32)."""
    with ui.row().classes("w-full justify-end gap-2 pt-1"):
        ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
        btn = ui.button(label, on_click=on_click).props("unelevated")
    btn.set_enabled(enabled)

    def save() -> None:
        if btn.enabled:
            events.handle_event(on_click, events.ClickEventArguments(sender=btn, client=btn.client))

    dlg.default_slot.children[0].on("keydown", save, js_handler=SAVE_KEYS)
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
        named(
            ui.button(icon="chevron_left", on_click=lambda: step(-1)).props("flat round dense").classes("text-primary"),
            t("Previous month"),
        )
        sel = (
            ui.select(options, value=value, on_change=lambda e: on_change(e.value))
            .props("borderless dense options-dense hide-dropdown-icon")
            .classes("font-semibold text-[15px] text-center")
        )
        sel.props["aria-label"] = t("Month")
        named(
            ui.button(icon="chevron_right", on_click=lambda: step(1)).props("flat round dense").classes("text-primary"),
            t("Next month"),
        )
    _month_steps[ui.context.client] = step  # ← and → step it (F32)
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
    ".zip": "application/zip",
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
