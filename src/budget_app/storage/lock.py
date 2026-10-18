"""Single-instance lock: two app processes writing the same CSV files would overwrite each other."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO

_handle: IO[str] | None = None  # kept open for the lifetime of the process


class AlreadyRunningError(RuntimeError):
    pass


def acquire(data_dir: Path) -> None:
    """Take an exclusive OS lock on `<data_dir>/.lock`; released automatically when the process exits."""
    global _handle  # noqa: PLW0603 - one lock per process
    handle = (data_dir / ".lock").open("a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 - platform specific

            handle.seek(0)
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
    _handle = handle
