"""Attachments (F12): receipts, invoices and contracts kept with a transaction, in the data folder.

A file is copied to `attachments/<transaction id>/<attachment id>.<extension>` (decision 16), so
backups and restores carry it; `attachments.csv` holds one record per file with the name it had.

**Files and undo.** Removing an attachment, or deleting its transaction, deletes only the record, as
one undoable action. The file stays on disk, so Undo brings the record back with its file. `cleanup`
(at every start) deletes the files that no record names and that no action in the change history
could bring back; the history keeps the newest 500 actions, so a file is deleted for good once its
removal can no longer be undone.

**Bills (F03).** A scanned QR bill keeps its file as an attachment with the bill's id (`bill_id`), in the
bill's folder `attachments/<bill id>/`, before any transaction exists. Paying the bill sets the record's
`transaction_id`: the file then belongs to the payment too, and stays where it is, so undoing the payment
needs nothing but the record. Deleting the payment gives the file back to the bill (`remove_for`).
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - opens a stored attachment with the system's viewer, fixed commands only
import sys
from collections import Counter
from pathlib import Path

from budget_app.domain import attachments as files
from budget_app.domain.attachments import FileType
from budget_app.domain.models import Attachment
from budget_app.i18n import N_, t
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

log = logging.getLogger(__name__)

MAX_BYTES = files.MAX_BYTES
TABLE = "attachments"  # the table's name in the change history


def folder(ctx: Context) -> Path:
    return ctx.store.data_dir / files.FOLDER


def check(name: str, data: bytes) -> FileType:
    """The type of a file that may be attached; raises ValidationError naming the file otherwise."""
    shown = files.display_name(name)
    if not data:
        raise ValidationError(t("“{name}” is empty", name=shown))
    if len(data) > MAX_BYTES:
        raise ValidationError(t("“{name}” is larger than 20 MB", name=shown))
    ft = files.file_type(name, data)
    if ft is None:
        if files.extension(name) in files.TYPES:
            raise ValidationError(
                t("“{name}” is not a valid .{ext} file", name=shown, ext=files.extension(name).lower())
            )
        raise ValidationError(
            t(
                "“{name}” can’t be attached. Attach PDF files, photos (JPEG, PNG, HEIC, WebP), "
                "Word, Excel or OpenDocument files, or text and CSV files.",
                name=shown,
            )
        )
    return ft


@recorded(N_("Attach a file"))
def add(ctx: Context, tx_id: str, name: str, data: bytes) -> Attachment:
    """Copy a file into the data folder and attach it to the transaction."""
    if ctx.store.transactions.get(tx_id) is None or not files.is_id(tx_id):
        raise ValidationError(t("Transaction not found"))
    return _store(ctx, Attachment(transaction_id=tx_id, name=name, stored=""), data)


@recorded(N_("Attach a file"))
def add_to_bill(ctx: Context, bill_id: str, name: str, data: bytes) -> Attachment:
    """Copy a bill's file into the data folder (F03), inside the scan's action."""
    if ctx.store.bills.get(bill_id) is None or not files.is_id(bill_id):
        raise ValidationError(t("This bill is no longer in the list"))
    return _store(ctx, Attachment(transaction_id="", bill_id=bill_id, name=name, stored=""), data)


def _store(ctx: Context, att: Attachment, data: bytes) -> Attachment:
    ft = check(att.name, data)
    att.name, att.size, att.content_type = files.display_name(att.name), len(data), ft.content_type
    att.stored = files.stored_name(att.id, ft.extension)
    target = path(ctx, att)
    if target is None:  # the attachments folder is a link to somewhere else
        raise ValidationError(t("Transaction not found"))
    write_file(target, data)  # the file first: a record never names a file that isn't there
    return ctx.store.attachments.add(att)


def write_file(target: Path, data: bytes) -> None:
    """Write a file whole or not at all (temp file, fsync, rename)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(target)


@recorded(N_("Remove an attachment"))
def remove(ctx: Context, attachment_id: str) -> Attachment:
    """Remove an attachment; its file stays until the removal can no longer be undone (see `cleanup`)."""
    att = ctx.store.attachments.get(attachment_id)
    if att is None:
        raise ValidationError(t("This attachment no longer exists"))
    ctx.store.attachments.delete([att.id])
    return att


@recorded(N_("Remove attachments"))
def remove_for(ctx: Context, tx_ids: set[str] | list[str]) -> int:
    """Remove the attachments of transactions that are deleted (inside their deletion's action).

    The file of a bill that still exists goes back to the bill instead (it is open again, `bills.release`).
    """
    ids = set(tx_ids)
    mine = [a for a in ctx.store.attachments.all() if a.transaction_id and a.transaction_id in ids]
    kept = [a for a in mine if a.bill_id and ctx.store.bills.get(a.bill_id) is not None]
    for att in kept:
        back = ctx.store.attachments.get(att.id)
        if back is not None:
            back.transaction_id = ""
            ctx.store.attachments.update(back)
    return ctx.store.attachments.delete([a.id for a in mine if a not in kept])


def remove_for_bills(ctx: Context, bill_ids: set[str]) -> int:
    """Remove the files of deleted bills that no transaction holds (inside the deletion's action)."""
    return ctx.store.attachments.delete(
        [a.id for a in ctx.store.attachments.all() if a.bill_id in bill_ids and not a.transaction_id]
    )


def get(ctx: Context, attachment_id: str) -> Attachment | None:
    return ctx.store.attachments.get(attachment_id) if attachment_id else None


def list_for(ctx: Context, tx_id: str) -> list[Attachment]:
    """A transaction's attachments, oldest first (in the order they were added)."""
    rows = [a for a in ctx.store.attachments.all() if tx_id and a.transaction_id == tx_id]
    return sorted(rows, key=lambda a: a.added)


def list_for_bill(ctx: Context, bill_id: str) -> list[Attachment]:
    """The files a bill came with (F03), whether it is paid or not."""
    rows = [a for a in ctx.store.attachments.all() if bill_id and a.bill_id == bill_id]
    return sorted(rows, key=lambda a: a.added)


def counts(ctx: Context) -> Counter[str]:
    """Number of attachments per transaction id, for the Transactions list."""
    return Counter(a.transaction_id for a in ctx.store.attachments.all() if a.transaction_id)


def names_by_transaction(ctx: Context) -> dict[str, str]:
    """The file names of each transaction's attachments, for searching transactions by them."""
    out: dict[str, list[str]] = {}
    for a in ctx.store.attachments.all():
        if a.transaction_id:
            out.setdefault(a.transaction_id, []).append(a.name)
    return {k: " ".join(v) for k, v in out.items()}


def ids_ok(att: Attachment) -> bool:
    """Whether a record names its folder and file by plain ids, as the app writes them: its transaction's and
    its bill's id (either may be empty, not both) and `<id>.<ext>`."""
    owners = [x for x in (att.transaction_id, att.bill_id) if x]
    return bool(owners) and all(files.is_id(x) for x in owners) and files.is_stored_name(att.stored)


def path(ctx: Context, att: Attachment) -> Path | None:
    """Where the attachment's file is, or None when its record names an impossible place.

    The path is built from the record's ids alone, and only when both are plain names, so no record
    (edited by hand, or from a backup) can point outside the attachments folder.
    """
    if not ids_ok(att):
        return None
    root = folder(ctx)
    target = root / att.folder / att.stored
    if target.resolve().parent.parent != root.resolve():  # a folder linked elsewhere
        return None
    return target


def exists(ctx: Context, att: Attachment) -> bool:
    p = path(ctx, att)
    return p is not None and p.is_file()


def _content(target: Path) -> bytes:
    """A stored file's content, read up to one byte over the limit (anything longer is refused anyway)."""
    with target.open("rb") as f:
        return f.read(MAX_BYTES + 1)


def verified(ctx: Context, att: Attachment) -> tuple[Path, bytes, FileType]:
    """The attachment's file and content, after checking that it is of an accepted type by name and content.

    Raises ValidationError when the file is missing, or when it isn't what its name says (a file that got
    into the folder from a crafted backup or by hand): such a file is never opened, served or copied.
    """
    p = path(ctx, att)
    if p is None or not p.is_file():
        raise ValidationError(t("The file of “{name}” is missing from the data folder", name=att.name))
    data = _content(p)
    ft = files.stored_type(att.stored, data)
    if ft is None:
        raise ValidationError(t("“{name}” isn’t opened: its content doesn’t match its type", name=att.name))
    return p, data, ft


def read(ctx: Context, att: Attachment) -> bytes:
    return verified(ctx, att)[1]


def open_file(ctx: Context, att: Attachment) -> None:
    """Open an attachment with the system's viewer, once its file is known to be what its record says."""
    target, _, _ = verified(ctx, att)
    system_open(target)


def kind(att: Attachment) -> str:
    """ "image", "pdf", "office" or "text", for the preview."""
    ft = files.BY_EXTENSION.get(files.extension(att.stored))
    return ft.kind if ft else ""


def size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return t("{size} MB", size=f"{size / (1024 * 1024):.1f}")
    if size >= 1024:
        return t("{size} KB", size=max(1, round(size / 1024)))
    return t("{size} bytes", size=size)


def system_open(target: Path) -> None:
    """Open a file with the system's default viewer (the app window; a browser gets it served instead).

    The system picks the program by the extension, and would run a program or script, so only a file of
    an accepted type whose content matches it is handed over; anything else raises ValidationError.
    """
    if not target.is_file() or files.stored_type(target.name, _content(target)) is None:
        raise ValidationError(t("“{name}” isn’t opened: its content doesn’t match its type", name=target.name))
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - a stored attachment
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([command, str(target)])  # noqa: S603 - fixed command, a path the app built


# ---------------------------------------------------------------------- files nobody can bring back
def _wanted(ctx: Context) -> set[tuple[str, str]]:
    """(folder, stored name) of every live record and of every record an undo could restore."""
    wanted = {(a.folder, a.stored) for a in ctx.store.attachments.all()}
    for act in ctx.store.history.actions():
        for change in act.changes:
            if change.table != TABLE:
                continue
            for raw in (change.before, change.after):
                if raw:
                    wanted.add((raw.get("bill_id") or raw.get("transaction_id", ""), raw.get("stored", "")))
    return wanted


def cleanup(ctx: Context) -> int:
    """Delete attachment files that nothing refers to any more; returns how many. Run at startup.

    Only files and folders the app names itself are touched; anything else in the folder stays.
    """
    root = folder(ctx)
    if not root.is_dir():
        return 0
    wanted = _wanted(ctx)
    removed = 0
    for tx_dir in root.iterdir():
        if not (tx_dir.is_dir() and not tx_dir.is_symlink() and files.is_id(tx_dir.name)):
            continue
        for f in tx_dir.iterdir():
            leftover = f.name.startswith(".") and f.name.endswith(".tmp")  # an interrupted write
            if not f.is_file() or not (leftover or files.is_stored_name(f.name)):
                continue
            if leftover or (tx_dir.name, f.name) not in wanted:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    log.warning("Could not delete the unused attachment %s", f, exc_info=True)
        if not any(tx_dir.iterdir()):
            tx_dir.rmdir()
    if removed:
        log.info("Deleted %d attachment files no record or undo refers to", removed)
    return removed
