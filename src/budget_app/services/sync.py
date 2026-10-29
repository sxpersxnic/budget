"""Sync through a cloud folder, one computer at a time (F26, decision 19).

The data folder may sit in iCloud Drive, OneDrive or Dropbox (Settings → Data → Move…, `profiles.move`); the
sync client copies the files between computers, and the app never talks to a cloud service. Two things keep two
computers from silently overwriting each other:

- *Claims* (`storage.claim`): while a folder is open, the app keeps a claim file for this computer in it. Another
  computer's fresh claim is shown on every page as a warning: the records are open there too.
- *Change detection* (`storage.watch`): every `INTERVAL` seconds `tick` compares the record files with what this app
  last read or wrote. Files changed by someone else are reloaded into the open store once they have settled (the
  same on two checks in a row, so a sync client still copying an action's files is waited for).

There is no merging: a change made on both computers between two checks keeps the one written last.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from budget_app.services import context as context_module
from budget_app.storage import claim, encryption, watch
from budget_app.storage.csv_store import CorruptDataError
from budget_app.storage.encryption import EncryptedError, WrongKeyError

log = logging.getLogger(__name__)

INTERVAL = 5  # seconds between two checks


@dataclass
class State:
    folder: Path
    host: str
    since: datetime
    beat: datetime
    pending: dict[str, watch.Signature | None] | None = None


_state: State | None = None


def start(folder: Path, now: datetime | None = None) -> State:
    """The folder is open: claim it for this computer and take its files as they are now."""
    global _state  # noqa: PLW0603 - one open data folder per process
    stop()
    now = now or datetime.now()
    _state = State(folder, claim.host_name(), now, now)
    try:
        claim.write(folder, _state.host, now, now)
    except OSError as exc:  # a read-only folder still opens; the claim is only a warning for others
        log.warning("Can't write the claim file in %s: %s", folder, exc)
    watch.baseline(encryption.record_files(folder))
    return _state


def stop() -> None:
    """The folder is closed: release this computer's claim."""
    global _state  # noqa: PLW0603 - see start
    if _state is not None:
        claim.release(_state.folder, _state.host)
    _state = None


def others(now: datetime | None = None) -> list[claim.Claim]:
    """Other computers that have the open folder open too."""
    if _state is None:
        return []
    return claim.others(_state.folder, _state.host, now)


def tick(now: datetime | None = None) -> list[Path]:
    """One check: refresh the claim once a `HEARTBEAT`, and reload the store when record files were changed
    elsewhere and have settled. Returns the reloaded files (empty when nothing was reloaded)."""
    st = _state
    if st is None:
        return []
    now = now or datetime.now()
    if (now - st.beat).total_seconds() >= claim.HEARTBEAT:
        st.beat = now
        try:
            claim.write(st.folder, st.host, st.since, now)
        except OSError as exc:
            log.warning("Can't refresh the claim file in %s: %s", st.folder, exc)
    if not context_module.is_open():  # an encrypted folder waiting for its password
        return []
    files = encryption.record_files(st.folder)
    changed = watch.changed(files)
    if not changed:
        st.pending = None
        return []
    settled = {str(p): watch.signature(p) for p in changed}
    if settled != st.pending:
        st.pending = settled  # still arriving, maybe: look again at the next check
        return []
    st.pending = None
    try:
        context_module.get_ctx().store.reload()
    except (OSError, ValueError, CorruptDataError, EncryptedError, WrongKeyError) as exc:
        log.warning("The records changed on disk but can't be read yet: %s", exc)
        watch.baseline(changed)  # not again until they change once more
        return []
    log.info("Reloaded %d record files changed on another computer", len(changed))
    return changed


def cloud_folders(home: Path | None = None) -> list[tuple[str, Path]]:
    """The cloud folders found on this computer, as (name, folder), for the Move sheet's suggestions."""
    home = home or Path.home()
    candidates: list[tuple[str, Path]] = [
        ("iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
    ]
    storage = home / "Library" / "CloudStorage"  # macOS: Dropbox, OneDrive, Google Drive …
    if storage.is_dir():
        for p in sorted(storage.iterdir()):
            candidates.append((p.name.split("-")[0].replace("GoogleDrive", "Google Drive"), p))
    candidates += [
        ("Dropbox", home / "Dropbox"),
        ("OneDrive", home / "OneDrive"),
        ("Google Drive", home / "Google Drive"),
    ]
    seen: set[Path] = set()
    found = []
    for name, path in candidates:
        if path.is_dir() and path not in seen:
            seen.add(path)
            found.append((name, path))
    return found
