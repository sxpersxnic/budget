"""Encryption at rest of the record files (F27, decision 20).

A `Codec` turns the text of a record file into the bytes written to disk and back. Without a key it
writes plain text, as the app always did. With a key it writes a marker line and a Fernet token
(AES-128-CBC with an HMAC-SHA256, from the `cryptography` package; the token carries its own IV and
timestamp), so a file reads as one opaque blob. Reading is tolerant: a codec with a key also reads a
plain file, and a plain codec reads a plain file, so a folder may hold a mix while encryption is being
turned on or off, or right after a restore, and `reencode` brings every file into the codec's form.

Only the record files are encrypted: the CSV tables, `settings.json`, the import memory files and
`history.jsonl`. Attachment files, the ECB rates and the lock files are not.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from budget_app.storage import watch

MAGIC = b"BUDGET-ENCRYPTED-1\n"
HISTORY_FILE = "history.jsonl"


class EncryptedError(RuntimeError):
    """The file is encrypted and the codec has no key."""


class WrongKeyError(RuntimeError):
    """The file is encrypted with another key, or was tampered with."""


class Codec:
    """Encodes record files for the disk; `key` is a Fernet key (None: plain text)."""

    def __init__(self, key: bytes | None = None):
        self.key = key

    @property
    def encrypting(self) -> bool:
        return self.key is not None

    def encode(self, text: str) -> bytes:
        data = text.encode("utf-8")
        if self.key is None:
            return data
        return MAGIC + Fernet(self.key).encrypt(data)

    def decode(self, data: bytes) -> str:
        if not data.startswith(MAGIC):
            return data.decode("utf-8")
        if self.key is None:
            raise EncryptedError("the file is encrypted")
        try:
            return Fernet(self.key).decrypt(data[len(MAGIC) :]).decode("utf-8")
        except InvalidToken as exc:
            raise WrongKeyError("the file is encrypted with another key") from exc


PLAIN = Codec()


def is_encrypted(path: Path) -> bool:
    """Whether the file on disk starts with the encryption marker."""
    try:
        with path.open("rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def read_text(path: Path, codec: Codec = PLAIN) -> str:
    watch.note(path)  # F26: noted first, so a change landing during the read is seen as a change at the next check
    return codec.decode(path.read_bytes())


def write_text(path: Path, text: str, codec: Codec = PLAIN) -> None:
    """Write the file whole, fsynced, then rename it into place (never a partially written file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(codec.encode(text))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    watch.note(path)  # F26: written here, not by another computer


def record_files(folder: Path) -> list[Path]:
    """The record files of a data (or backup) folder that the codec applies to."""
    from budget_app.storage import backup  # noqa: PLC0415 - backup imports this module's helpers too

    files = backup.data_files(folder)
    history = folder / HISTORY_FILE
    if history.is_file():
        files.append(history)
    return files


def reencode(folder: Path, codec: Codec, write: Codec | None = None) -> int:
    """Rewrite every record file of `folder`, read with `codec`, in the form of `write` (the same codec by default:
    encrypted with its key, or plain). Returns how many files were rewritten; plain files stay as they are when plain
    is wanted."""
    write = write if write is not None else codec
    n = 0
    for path in record_files(folder):
        if not write.encrypting and not is_encrypted(path):
            continue
        write_text(path, read_text(path, codec), write)
        n += 1
    return n
