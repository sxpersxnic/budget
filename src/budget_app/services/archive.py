"""Full backup of the data folder as one ZIP file, and restoring it (F25).

A backup holds the user's records (`storage.backup.data_files`: the CSV tables, settings and import
profiles) plus a small manifest. The ECB rates are left out; they are downloaded again.

Restoring checks the archive completely before touching anything: only plain file names, only
CSV and JSON files, bounded sizes, and every table must load. The current files are then copied to
`backups/<timestamp>_before-restore/`, replaced, and the store is reloaded.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from budget_app.config import app_version
from budget_app.i18n import t
from budget_app.services.context import Context, ValidationError
from budget_app.storage import backup
from budget_app.storage.csv_store import CorruptDataError
from budget_app.storage.store import Store

FORMAT = "budget-app/backup"
VERSION = 1
MANIFEST = "budget-backup.json"
MAX_FILES = 100
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # uncompressed; a household's records are a few MB


@dataclass
class SavedBackup:
    """A backup folder kept by the app in `<data>/backups/`."""

    path: Path
    created: datetime
    reason: str  # "update" (first start of a new version) or "restore" (before a restore)


def file_name(day: date | None = None) -> str:
    return f"Budget-backup-{(day or date.today()).isoformat()}.zip"


def create(ctx: Context) -> bytes:
    """The data folder's records as a ZIP, with a manifest naming the format and app version."""
    files = backup.data_files(ctx.store.data_dir)
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "app_version": app_version(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "files": [f.name for f in files],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
        for f in files:
            zf.write(f, f.name)
    return buf.getvalue()


def read(data: bytes) -> dict[str, bytes]:
    """The record files in a backup ZIP, checked for shape and size. Raises ValidationError."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValidationError(t("This is not a Budget backup (not a ZIP file).")) from exc
    with zf:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        if MANIFEST not in names:
            raise ValidationError(t("This is not a Budget backup (the file list is missing)."))
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise ValidationError(t("The backup is too large."))
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except ValueError as exc:
            raise ValidationError(t("This is not a Budget backup (the file list is missing).")) from exc
        if manifest.get("format") != FORMAT:
            raise ValidationError(t("This is not a Budget backup (the file list is missing)."))
        if int(manifest.get("version", 0)) > VERSION:
            raise ValidationError(t("This backup was made by a newer version of Budget. Update the app first."))
        files: dict[str, bytes] = {}
        for info in infos:
            name = info.filename
            if name == MANIFEST:
                continue
            # plain names only: nothing may land outside the data folder
            if info.is_dir() or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
                raise ValidationError(t("The backup contains an unexpected file: {name}", name=name))
            if not name.endswith((".csv", ".json")):
                raise ValidationError(t("The backup contains an unexpected file: {name}", name=name))
            files[name] = zf.read(info)
    if not any(n.endswith(".csv") for n in files):
        raise ValidationError(t("The backup contains no records."))
    return files


def check(files: dict[str, bytes]) -> None:
    """Load the files as a store in a scratch folder; raise ValidationError if any table is damaged."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, content in files.items():
            (Path(tmp) / name).write_bytes(content)
        try:
            Store(Path(tmp))
        except (CorruptDataError, ValueError, TypeError) as exc:
            raise ValidationError(t("The backup can’t be read: {error}", error=exc)) from exc


def restore(ctx: Context, files: dict[str, bytes], now: datetime | None = None) -> Path:
    """Replace the data files with `files` (already read and checked) and reload the store.

    Returns the folder holding a copy of the replaced files.
    """
    check(files)
    data_dir = ctx.store.data_dir
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    safety = data_dir / backup.BACKUP_DIR / f"{stamp}_before-restore"
    safety.mkdir(parents=True, exist_ok=True)
    current = backup.data_files(data_dir)
    for f in current:
        shutil.copy2(f, safety / f.name)
    # files the backup doesn't have would otherwise mix newer records into the restored ones
    for f in current:
        if f.name not in files:
            f.unlink()
    for name, content in files.items():
        tmp = data_dir / f".{name}.restore"
        tmp.write_bytes(content)
        tmp.replace(data_dir / name)
    ctx.store = Store(data_dir)
    backup.prune(data_dir / backup.BACKUP_DIR)
    return safety


def saved_backups(ctx: Context) -> list[SavedBackup]:
    """The backups the app made itself, newest first."""
    root = ctx.store.data_dir / backup.BACKUP_DIR
    found = []
    for p in root.iterdir() if root.is_dir() else []:
        if not (p.is_dir() and backup.BACKUP_NAME.match(p.name)):
            continue
        try:
            created = datetime.strptime(p.name[:17], "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        found.append(SavedBackup(p, created, "restore" if p.name.endswith("_before-restore") else "update"))
    return sorted(found, key=lambda b: b.created, reverse=True)


def files_in(folder: Path) -> dict[str, bytes]:
    """The record files of a saved backup folder, ready for `restore`."""
    files = {f.name: f.read_bytes() for f in backup.data_files(folder)}
    if not any(n.endswith(".csv") for n in files):
        raise ValidationError(t("The backup contains no records."))
    return files
