"""Single-instance lock: two app processes writing the same CSV files would overwrite each other."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO

_handle: IO[str] | None = None  # kept open for the lifetime of the process

# Windows locks are mandatory: other handles cannot read a locked byte. The lock sits past the
# PID (Windows allows locking beyond the end of the file), so the PID stays readable.
_WINDOWS_LOCK_OFFSET = 1 << 20


class AlreadyRunningError(RuntimeError):
    pass


LOCK_FILE = ".lock"


def _take(data_dir: Path) -> IO[str]:
    """An exclusive OS lock on `<data_dir>/.lock`, with this process's PID in the file."""
    handle = (data_dir / LOCK_FILE).open("a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - platform specific

            handle.seek(_WINDOWS_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 - platform specific

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AlreadyRunningError(f"The budget app is already running with the data folder {data_dir}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def acquire(data_dir: Path) -> None:
    """Take an exclusive OS lock on `<data_dir>/.lock`; released automatically when the process exits."""
    global _handle  # noqa: PLW0603 - one lock per process
    _handle = _take(data_dir)


def switch(data_dir: Path) -> None:
    """Lock another data folder (F34, F26), then let go of the one held so far; raises AlreadyRunningError and keeps
    the old lock when another window has the new folder open."""
    global _handle  # noqa: PLW0603 - see acquire
    handle = _take(data_dir)
    if _handle is not None:
        _handle.close()  # closing the file releases its lock
    _handle = handle
