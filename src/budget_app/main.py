"""Entry point: `uv run budget [--native] [--port 8080] [--host 127.0.0.1] [--data-dir PATH] [--no-show]`.

Installed builds (PyInstaller) open in a native window by default and store data in the OS user data folder.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
from pathlib import Path

from nicegui import app, native, ui

from budget_app import config
from budget_app.services.context import init_context
from budget_app.storage import lock as instance_lock
from budget_app.ui import theme
from budget_app.ui.layout import refresh_rates
from budget_app.ui.pages import (
    add_budget,
    add_transaction,
    budget_entries,
    budget_overview,
    overview,
    settings,
    transactions,
)

PAGES = {
    "/": ("Overview", overview.page),
    "/budget": ("Budget", budget_overview.page),
    "/add-transaction": ("New Transaction", add_transaction.page),
    "/transactions": ("Transactions", transactions.page),
    "/add-budget": ("New Budget Entry", add_budget.page),
    "/budget-entries": ("Budget Plan", budget_entries.page),
    "/settings": ("Settings", settings.page),
}


def setup(data_dir: Path) -> None:
    ctx = init_context(data_dir)
    theme.install()
    for path, (title, builder) in PAGES.items():
        ui.page(path, title=f"{title} – Budget")(builder)

    async def refresh() -> None:
        await refresh_rates(ctx)

    app.on_startup(refresh)
    app.timer(30 * 60, refresh, immediate=False)  # keeps rates current when the app stays open overnight


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
        reconnect_timeout=30,
    )


if __name__ == "__main__":
    run()
