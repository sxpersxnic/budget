"""Which record files changed on disk since this app last read or wrote them (F26, decision 19).

Every record file passes through `encryption.read_text` / `write_text`, which note the file's size and change time
afterwards. A file whose size or time differs from what was noted was written by someone else: in a cloud folder,
the other computer's app through the sync client. `sync.tick` asks `changed` and reloads the store.
"""

from __future__ import annotations

from pathlib import Path

Signature = tuple[int, int]  # (modification time in ns, size)
_seen: dict[str, Signature] = {}


def signature(path: Path) -> Signature | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def note(path: Path) -> None:
    """The file as this app last read or wrote it."""
    sig = signature(path)
    if sig is None:
        _seen.pop(str(path), None)
    else:
        _seen[str(path)] = sig


def baseline(paths: list[Path]) -> None:
    for path in paths:
        note(path)


def changed(paths: list[Path]) -> list[Path]:
    """The files that are not as this app last saw them (new ones included)."""
    return [p for p in paths if signature(p) != _seen.get(str(p))]
