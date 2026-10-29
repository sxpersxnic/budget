"""Which computers have a data folder open (F26, decision 19): one claim file per computer, carried by cloud sync.

The OS lock in `storage.lock` holds on one computer only. When the data folder sits in iCloud Drive, OneDrive or
Dropbox, the other computer sees the files but not the lock, so the app also writes `<data>/.in-use.<host>.json`
with its host name and the time it opened the folder, refreshed every `HEARTBEAT` seconds while it runs and
removed when it closes the folder. A file per computer, because cloud sync handles two computers writing two files
well and two computers writing one file badly. Another computer's claim refreshed within `STALE` seconds means the
folder is in use there too; one that went quiet (a crash, a computer asleep) no longer counts.
"""

from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

PREFIX = ".in-use."
SUFFIX = ".json"
STALE = 10 * 60  # seconds without a heartbeat after which a claim counts as abandoned
HEARTBEAT = 60


@dataclass
class Claim:
    host: str
    pid: int
    since: str  # ISO time the folder was opened there
    seen: str  # ISO time of the last heartbeat

    @property
    def seen_at(self) -> datetime:
        return datetime.fromisoformat(self.seen)

    @property
    def since_at(self) -> datetime:
        return datetime.fromisoformat(self.since)


def host_name() -> str:
    return platform.node() or "this computer"


def is_claim_file(name: str) -> bool:
    return name.startswith(PREFIX) and name.endswith(SUFFIX)


def path_for(folder: Path, host: str) -> Path:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", host).strip("-.")[:60] or "host"
    return folder / f"{PREFIX}{slug}{SUFFIX}"


def read(path: Path) -> Claim | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        claim = Claim(str(data["host"]), int(data["pid"]), str(data["since"]), str(data["seen"]))
        claim.seen_at  # noqa: B018 - raises ValueError for a time that can't be read
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return claim


def write(folder: Path, host: str, since: datetime, now: datetime | None = None) -> Claim:
    """Claim the folder for `host`, or refresh the claim (the heartbeat)."""
    seen = (now or datetime.now()).isoformat(timespec="seconds")
    claim = Claim(host, os.getpid(), since.isoformat(timespec="seconds"), seen)
    path = path_for(folder, host)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(claim)), encoding="utf-8")
    tmp.replace(path)
    return claim


def release(folder: Path, host: str) -> None:
    """Remove this computer's claim (the folder is closed)."""
    path_for(folder, host).unlink(missing_ok=True)


def others(folder: Path, host: str, now: datetime | None = None) -> list[Claim]:
    """The fresh claims of other computers, the most recently seen first."""
    if not folder.is_dir():
        return []
    own = path_for(folder, host).name
    limit = (now or datetime.now()) - timedelta(seconds=STALE)
    found = [
        c
        for p in folder.iterdir()
        if is_claim_file(p.name) and p.name != own
        for c in [read(p)]
        if c is not None and c.host != host and c.seen_at > limit
    ]
    return sorted(found, key=lambda c: c.seen, reverse=True)
