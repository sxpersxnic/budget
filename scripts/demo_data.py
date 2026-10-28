"""Fill a data folder with a year of example records for manual testing: `just demo`.

Usage: `uv run python scripts/demo_data.py demo-data`

The folder is emptied first, so every run starts from the same data (dated relative to today). The set
itself is `budget_app.services.demo`, the sample data the app offers on its welcome screen (F30), so the
folder is marked as holding sample data and Settings can remove it again.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from budget_app import config
from budget_app.domain.fx import FxService, download_ecb_csv
from budget_app.services import demo
from budget_app.services.context import Context
from budget_app.storage import backup
from budget_app.storage import lock as instance_lock
from budget_app.storage.store import Store


def reset(folder: Path) -> None:
    """Empty `folder`, refusing while the app runs on it (the lock stays held until this script exits)."""
    folder.mkdir(parents=True, exist_ok=True)
    instance_lock.acquire(folder)
    for entry in folder.iterdir():
        if entry.name == ".lock":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    backup.backup_on_version_change(folder, config.app_version())  # marks the folder; nothing to back up yet


def install_rates(folder: Path) -> None:
    """Copy the dev folder's ECB file (works offline), or download it."""
    target = folder / "ecb" / config.ECB_CSV_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    local = config.PROJECT_ROOT / "data" / "ecb" / config.ECB_CSV_NAME
    if local.is_file():
        shutil.copy2(local, target)
    else:
        download_ecb_csv(target)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: demo_data.py FOLDER")
    folder = Path(sys.argv[1]).resolve()
    try:
        reset(folder)
    except instance_lock.AlreadyRunningError as exc:
        sys.exit(f"{exc}; close it first.")
    install_rates(folder)
    demo.load(Context(store=Store(folder), fx_service=FxService(folder)))
    print(f"Demo data written to {folder}")


if __name__ == "__main__":
    main()
