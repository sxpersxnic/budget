"""Full backup of the data folder as one ZIP file, and restoring it (F25).

A backup holds the user's records (`storage.backup.data_files`: the CSV tables, settings and import
profiles), the files of their attachments (F12) under `attachments/<transaction id>/<stored name>`,
and a small manifest. The ECB rates are left out; they are downloaded again.

Restoring checks the archive completely before touching anything: only plain file names (and
attachment paths of exactly that shape), only CSV and JSON records, bounded sizes (an attachment at
most 20 MB), and every table must load. The current files are then copied to
`backups/<timestamp>_before-restore/` (the attachments moved there), replaced, and the store is
reloaded. Only the attachment files a restored record names are written back.

Backups are built and read in memory, hence `MAX_TOTAL_BYTES`; `create` refuses to build one that
`read` would refuse to restore.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from budget_app.config import app_version
from budget_app.domain import attachments as att_files
from budget_app.i18n import t
from budget_app.services import attachments as attachments_svc
from budget_app.services.context import Context, ValidationError
from budget_app.storage import backup
from budget_app.storage.csv_store import CorruptDataError
from budget_app.storage.store import Store

FORMAT = "budget-app/backup"
VERSION = 2  # 2: with attachments (F12); a backup without any is still written as version 1
MANIFEST = "budget-backup.json"
MAX_FILES = 100  # record files
MAX_ATTACHMENTS = 20_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # uncompressed; records are a few MB, attachments the rest
ATTACHMENT_PATH = re.compile(r"^attachments/([A-Za-z0-9_-]{1,64})/([A-Za-z0-9_-]{1,64}\.[a-z0-9]{1,5})$")


@dataclass
class SavedBackup:
    """A backup folder kept by the app in `<data>/backups/`."""

    path: Path
    created: datetime
    reason: str  # "update" (first start of a new version) or "restore" (before a restore)


def file_name(day: date | None = None) -> str:
    return f"Budget-backup-{(day or date.today()).isoformat()}.zip"


def _attachments(ctx: Context) -> list[tuple[str, Path]]:
    """(name in the archive, file) of every attachment record whose file is there."""
    out = []
    for a in ctx.store.attachments.all():
        p = attachments_svc.path(ctx, a)
        if p is not None and p.is_file():
            out.append((f"{backup.ATTACHMENTS}/{a.transaction_id}/{a.stored}", p))
    return sorted(out)


def create(ctx: Context) -> bytes:
    """The data folder's records and attachments as a ZIP, with a manifest naming the format and app version."""
    files = backup.data_files(ctx.store.data_dir)
    attached = _attachments(ctx)
    total = sum(f.stat().st_size for f in files) + sum(p.stat().st_size for _, p in attached)
    if total > MAX_TOTAL_BYTES or len(attached) > MAX_ATTACHMENTS:
        raise ValidationError(t("The records and attachments are too large for one backup file."))
    manifest = {
        "format": FORMAT,
        "version": VERSION if attached else 1,  # without attachments, older versions can restore it
        "app_version": app_version(),
        "created": datetime.now().isoformat(timespec="seconds"),
        "files": [f.name for f in files],
        "attachments": len(attached),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
        for f in files:
            zf.write(f, f.name)
        for name, p in attached:  # photos and PDFs are compressed already
            zf.write(p, name, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def _record_ok(info: zipfile.ZipInfo) -> bool:
    """A plain CSV or JSON name, so nothing may land outside the data folder."""
    name = info.filename
    if info.is_dir() or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        return False
    return name.endswith((".csv", ".json"))


def _attachment_ok(info: zipfile.ZipInfo) -> bool:
    """attachments/<id>/<id>.<ext> exactly, within the size limit of one attachment."""
    return not info.is_dir() and bool(ATTACHMENT_PATH.match(info.filename)) and info.file_size <= att_files.MAX_BYTES


def read(data: bytes) -> dict[str, bytes]:
    """The record and attachment files in a backup ZIP, checked for shape and size. Raises ValidationError.

    Records are keyed by their file name, attachments by `attachments/<transaction id>/<stored name>`.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValidationError(t("This is not a Budget backup (not a ZIP file).")) from exc
    with zf:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        if MANIFEST not in names:
            raise ValidationError(t("This is not a Budget backup (the file list is missing)."))
        attached = [i for i in infos if i.filename.startswith(f"{backup.ATTACHMENTS}/")]
        if (
            len(infos) - len(attached) > MAX_FILES
            or len(attached) > MAX_ATTACHMENTS
            or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES
        ):
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
            if not (_attachment_ok(info) if info in attached else _record_ok(info)):
                raise ValidationError(t("The backup contains an unexpected file: {name}", name=name))
            try:
                files[name] = zf.read(info)
            except (zipfile.BadZipFile, OSError, EOFError) as exc:  # damaged, or not the size it claims
                raise ValidationError(t("The backup can’t be read: {error}", error=exc)) from exc
    if not any(n.endswith(".csv") for n in files):
        raise ValidationError(t("The backup contains no records."))
    return files


def _records(files: dict[str, bytes]) -> dict[str, bytes]:
    return {n: c for n, c in files.items() if "/" not in n}


def check(files: dict[str, bytes]) -> None:
    """Load the files as a store in a scratch folder; raise ValidationError if any table is damaged.

    Attachment records must name their files by plain ids, as the app writes them, with an accepted
    extension; every attachment file must be of an accepted type by its name and its content, so a
    crafted backup can't place a program or script that *Open* would run.
    """
    for name, content in files.items():
        if "/" in name and att_files.stored_type(name.rsplit("/", 1)[1], content) is None:
            raise ValidationError(t("The backup contains an unexpected file: {name}", name=name))
    with tempfile.TemporaryDirectory() as tmp:
        for name, content in _records(files).items():
            (Path(tmp) / name).write_bytes(content)
        try:
            store = Store(Path(tmp))
        except (CorruptDataError, ValueError, TypeError) as exc:
            raise ValidationError(t("The backup can’t be read: {error}", error=exc)) from exc
        for a in store.attachments.all():
            ext = att_files.extension(a.stored)
            if not (att_files.is_id(a.transaction_id) and att_files.is_stored_name(a.stored)) or (
                ext not in att_files.BY_EXTENSION
            ):
                raise ValidationError(
                    t("The backup can’t be read: {error}", error=f"attachments.csv: {a.transaction_id}/{a.stored}")
                )


def restore(ctx: Context, files: dict[str, bytes], now: datetime | None = None) -> Path:
    """Replace the data files with `files` (already read and checked) and reload the store.

    Returns the folder holding a copy of the replaced files. The current attachments move there too;
    of the backup's attachment files, those its records name are written back.
    """
    check(files)
    records = _records(files)
    data_dir = ctx.store.data_dir
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    safety = data_dir / backup.BACKUP_DIR / f"{stamp}_before-restore"
    safety.mkdir(parents=True, exist_ok=True)
    current = backup.data_files(data_dir)
    for f in current:
        shutil.copy2(f, safety / f.name)
    live = data_dir / backup.ATTACHMENTS
    if live.is_dir():
        shutil.move(live, safety / backup.ATTACHMENTS)
    # files the backup doesn't have would otherwise mix newer records into the restored ones
    for f in current:
        if f.name not in records:
            f.unlink()
    for name, content in records.items():
        tmp = data_dir / f".{name}.restore"
        tmp.write_bytes(content)
        tmp.replace(data_dir / name)
    ctx.store = Store(data_dir)
    for a in ctx.store.attachments.all():
        stored = files.get(f"{backup.ATTACHMENTS}/{a.transaction_id}/{a.stored}")
        target = attachments_svc.path(ctx, a)
        if stored is not None and target is not None:
            attachments_svc.write_file(target, stored)
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
    """The record and attachment files of a saved backup folder, ready for `restore`."""
    files = {f.name: f.read_bytes() for f in backup.data_files(folder)}
    for f in backup.attachment_files(folder):
        name = f"{backup.ATTACHMENTS}/{f.parent.name}/{f.name}"
        if ATTACHMENT_PATH.match(name):
            files[name] = f.read_bytes()
    if not any(n.endswith(".csv") for n in files):
        raise ValidationError(t("The backup contains no records."))
    return files
