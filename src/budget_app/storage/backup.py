"""Safety copy of the data files when the app starts under a different version than last time.

An update replaces only the program; the data folder is never touched by the installers. Still,
a new version may migrate or rewrite the files, so the first start of each new version copies them
to `<data>/backups/<timestamp>_<old>_to_<new>/` before anything reads or writes them.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MARKER = ".app_version"  # version that last opened this data folder
BACKUP_DIR = "backups"
KEEP = 10  # update backups to keep; older ones are removed
_BACKUP_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_.+_to_.+$")


def data_files(data_dir: Path) -> list[Path]:
    """The user's records: the CSV tables, settings and import profiles (the ECB file can be downloaded again)."""
    return sorted(p for p in [*data_dir.glob("*.csv"), *data_dir.glob("*.json")] if p.is_file())


def backup_on_version_change(data_dir: Path, version: str, now: datetime | None = None) -> Path | None:
    """Copy the data files if `version` differs from the one that last used `data_dir`.

    Returns the backup folder, or None when nothing was copied (same version, or no data yet).
    If the copy fails, the marker is not updated, so the next start tries again.
    """
    marker = data_dir / MARKER
    previous = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if previous == version:
        return None
    files = data_files(data_dir)
    target = None
    if files:
        stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
        target = data_dir / BACKUP_DIR / f"{stamp}_{_safe(previous or 'earlier')}_to_{_safe(version)}"
        try:
            target.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, target / f.name)
        except OSError:
            log.exception("Could not back up the data files to %s", target)
            return None
        log.info("Backed up %d data files to %s", len(files), target)
        _prune(data_dir / BACKUP_DIR)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(marker)
    return target


def _safe(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z.+-]", "-", text)


def _prune(root: Path) -> None:
    backups = sorted(p for p in root.iterdir() if p.is_dir() and _BACKUP_NAME.match(p.name))
    for old in backups[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
