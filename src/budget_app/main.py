"""Entry point: `uv run budget [--native] [--port 8080] [--host 127.0.0.1] [--data-dir PATH] [--no-show]`.

Installed builds (PyInstaller) open in a native window by default and store data in the OS user data folder.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import multiprocessing
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from nicegui import app, background_tasks, native, ui

from budget_app import config, i18n
from budget_app.i18n import t
from budget_app.services import attachments as attachments_svc
from budget_app.services import context as context_module
from budget_app.services import profiles, security, sync
from budget_app.services.context import Context, ValidationError, get_ctx, init_context
from budget_app.storage import lock as instance_lock
from budget_app.ui import theme
from budget_app.ui.layout import book_recurring, records_reloaded, refresh_rates
from budget_app.ui.pages import (
    add_budget,
    add_transaction,
    bills,
    budget_entries,
    budget_overview,
    categories,
    goals,
    history,
    import_transactions,
    investments,
    lock,
    overview,
    payees,
    recurring,
    rules,
    settings,
    tags,
    tax,
    transactions,
)
from budget_app.ui.widgets import attachments as attachments_ui
from budget_app.ui.widgets import selects

PAGES = {
    "/": overview.page,
    "/budget": budget_overview.page,
    "/add-transaction": add_transaction.page,
    "/transactions": transactions.page,
    "/recurring": recurring.page,
    "/bills": bills.page,
    "/import": import_transactions.page,
    "/add-budget": add_budget.page,
    "/budget-entries": budget_entries.page,
    "/goals": goals.page,
    "/categories": categories.page,
    "/payees": payees.page,
    "/tags": tags.page,
    "/investments": investments.page,
    "/tax": tax.page,
    "/rules": rules.page,
    "/settings": settings.page,
    "/history": history.page,
}
# Pages outside the lock (F27): the lock page itself.
OPEN_PAGES = {
    "/lock": lock.page,
}
UNLOCK_ROUTE = "/unlock/{nonce}"


def _guarded(path: str, builder: Callable[..., None]) -> Callable[..., RedirectResponse | None]:
    """The page behind the app lock: without a session the request is redirected before anything is built.

    The wrapper keeps the builder's parameters (NiceGUI fills them from the query string) and asks for the
    request on top; the page asked for, query included, is where the unlock leads back to.
    """
    params = list(inspect.signature(builder).parameters.values())
    takes_request = any(p.name == "request" for p in params)
    if not takes_request:
        params.append(inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request))

    def page(**kwargs: Any) -> RedirectResponse | None:
        request: Request = kwargs["request"] if takes_request else kwargs.pop("request")
        wanted = f"{path}?{request.url.query}" if request.url.query else path
        target = security.redirect_for(request, wanted)
        if target is not None:
            return RedirectResponse(target)
        builder(**kwargs)
        return None

    page.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    page.__name__ = builder.__name__
    return page


def register_pages() -> None:
    """Every page of the app (each sets its translated title in layout.frame)."""
    for path, builder in PAGES.items():
        ui.page(path, title="Budget")(_guarded(path, builder))
    for path, builder in OPEN_PAGES.items():
        ui.page(path, title="Budget")(builder)


def _unlock_route(nonce: str) -> Response:
    """Turn the nonce the lock page got into the session cookie, then go on to the page that was asked for."""
    granted = security.consume(nonce)
    if granted is None:
        return RedirectResponse("/lock", status_code=303)
    token, target = granted
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(security.COOKIE, token, httponly=True, samesite="strict", path="/")
    return response


def register_routes(api: FastAPI) -> None:
    attachments_ui.register(api)  # serves attachment files to the page, by record id
    api.add_api_route(UNLOCK_ROUTE, _unlock_route, methods=["GET"], include_in_schema=False)
    selects.add_notes_route()


def open_data(data_dir: Path) -> Context:
    """Open the data folder: the store (through the codec when it is encrypted, F27), then what a start does."""
    ctx = init_context(data_dir, security.codec())
    attachments_svc.cleanup(ctx)  # files whose removal can no longer be undone (F12)
    i18n.set_language(ctx.store.settings.language)
    return ctx


async def refresh() -> None:
    """Rates and automatic bookings; nothing while an encrypted folder waits for its password."""
    if not context_module.is_open():
        return
    ctx = get_ctx()
    await refresh_rates(ctx)
    book_recurring(ctx)  # at the rates of the day


def _open_and_refresh(data_dir: Path) -> None:
    open_data(data_dir)
    background_tasks.create(refresh(), name="refresh after unlock")


def open_folder(folder: Path, moved: bool = False) -> None:
    """Close the open data folder and open `folder`: another profile (F34), or the same records moved to a new place
    (`moved`, F26: the same lock and key, so the browser stays unlocked). Refused, with nothing changed, when another
    Budget window has the folder open."""
    try:
        instance_lock.switch(folder)
    except instance_lock.AlreadyRunningError as exc:
        raise ValidationError(t("The folder {folder} is open in another Budget window", folder=folder)) from exc
    sync.stop()
    context_module.close_context()
    if moved:
        security.relocate(folder)
    else:
        security.configure(folder)
    if security.can_open_without_password() or security.codec().encrypting:
        _open_and_refresh(folder)
    sync.start(folder)


def check_folder() -> None:
    """F26: reload records another computer changed, and tell the open pages."""
    if sync.tick():
        records_reloaded()


def setup(data_dir: Path) -> None:
    security.configure(data_dir)
    security.opener = _open_and_refresh
    profiles.opener = open_folder
    theme.install()
    register_routes(app)
    register_pages()
    if security.can_open_without_password():
        open_data(data_dir)
    sync.start(data_dir)
    app.on_startup(refresh)
    app.on_shutdown(sync.stop)
    app.timer(30 * 60, refresh, immediate=False)  # keeps rates current, and books what falls due overnight
    app.timer(sync.INTERVAL, check_folder, immediate=False)  # F26: changes from another computer


def _redirect_missing_streams(data_dir: Path) -> None:
    """Windowed builds on Windows have no console; uvicorn's logging needs writable streams."""
    if sys.stdout is None or sys.stderr is None:
        log = (data_dir / "budget.log").open("a", encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def run() -> None:
    multiprocessing.freeze_support()  # native mode starts the window in a child process
    frozen = config.is_frozen()
    parser = argparse.ArgumentParser(description="Local budgeting app")
    parser.add_argument(
        "--native",
        action=argparse.BooleanOptionalAction,
        default=frozen,
        help="open in a native window instead of the browser (default for installed builds)",
    )
    parser.add_argument("--port", type=int, default=None, help="port (default: 8080, or a free port in native mode)")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to listen on (default: localhost only; 0.0.0.0 exposes your data to the network)",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="directory for the CSV files")
    parser.add_argument("--no-show", action="store_true", help="do not open a browser")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["BUDGET_DATA_DIR"] = str(args.data_dir.resolve())
    data = config.data_dir()
    _redirect_missing_streams(data)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        instance_lock.acquire(data)
    except instance_lock.AlreadyRunningError as exc:
        logging.getLogger(__name__).error("%s", exc)
        sys.exit(1)
    setup(data)
    if args.native:
        # The app's own exports use the save dialog (layout.save_file); this covers any other download link.
        app.native.settings["ALLOW_DOWNLOADS"] = True
    port = args.port or (native.find_open_port() if args.native else 8080)
    ui.run(
        title="Budget",
        host=args.host,
        port=port,
        native=args.native,
        window_size=(1400, 900) if args.native else None,
        reload=False,
        show=not args.no_show,
        favicon="💰",
        dark=None,  # follow the system appearance
        language=i18n.QUASAR[i18n.language()],  # type: ignore[arg-type]
        reconnect_timeout=30,
    )


if __name__ == "__main__":
    run()
