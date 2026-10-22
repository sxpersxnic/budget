"""Attachments (F12), pure: which files are accepted, how they are named on disk, how big they may be.

A file is accepted by its extension and its first bytes together: a PDF must start like a PDF, a PNG
like a PNG, and a text file must be text. So a renamed program or web page is refused, and every file
the app serves or opens is of the type its record names.

On disk a file is called `<attachment id>.<extension>`, never by the name the user gave it; that name
is only shown. `stored_name` and `is_stored_name` keep path tricks ("../", drive letters, control
characters) out of every path the app builds.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

MAX_BYTES = 20 * 1024 * 1024  # decision 16: one file at most 20 MB
FOLDER = "attachments"  # in the data folder: attachments/<transaction id>/<stored name>

# record ids and stored names: letters, digits, "_" and "-" only, so they can never leave the folder
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STORED = re.compile(r"^[A-Za-z0-9_-]{1,64}\.[a-z0-9]{1,5}$")


@dataclass(frozen=True)
class FileType:
    extension: str  # without the dot
    content_type: str
    kind: str  # "image", "pdf", "office" or "text": how it is previewed


def _zip(data: bytes) -> bool:
    return data.startswith(b"PK\x03\x04")


def _text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            data.decode("cp1252")  # Windows exports of old e-banking lists
        except UnicodeDecodeError:
            return False
    return True


def _heic(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1")


# The accepted types: receipts and invoices as PDF or photo, and the few office and text files a
# contract or a statement comes as. No HTML, SVG, XML or scripts: the app shows attachments itself.
TYPES: dict[str, tuple[FileType, Callable[[bytes], bool]]] = {
    "pdf": (FileType("pdf", "application/pdf", "pdf"), lambda d: d.startswith(b"%PDF-")),
    "jpg": (FileType("jpg", "image/jpeg", "image"), lambda d: d.startswith(b"\xff\xd8\xff")),
    "jpeg": (FileType("jpg", "image/jpeg", "image"), lambda d: d.startswith(b"\xff\xd8\xff")),
    "png": (FileType("png", "image/png", "image"), lambda d: d.startswith(b"\x89PNG\r\n\x1a\n")),
    "webp": (FileType("webp", "image/webp", "image"), lambda d: d[:4] == b"RIFF" and d[8:12] == b"WEBP"),
    "heic": (FileType("heic", "image/heic", "image"), _heic),
    "heif": (FileType("heic", "image/heic", "image"), _heic),
    "docx": (
        FileType("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "office"),
        _zip,
    ),
    "xlsx": (FileType("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "office"), _zip),
    "odt": (FileType("odt", "application/vnd.oasis.opendocument.text", "office"), _zip),
    "ods": (FileType("ods", "application/vnd.oasis.opendocument.spreadsheet", "office"), _zip),
    "txt": (FileType("txt", "text/plain; charset=utf-8", "text"), _text),
    "csv": (FileType("csv", "text/csv; charset=utf-8", "text"), _text),
}
EXTENSIONS = sorted(TYPES)  # for the file picker's accept list
BY_EXTENSION = {ft.extension: ft for ft, _ in TYPES.values()}


def extension(name: str) -> str:
    """The lower-case extension of a file name, without the dot ("" when it has none)."""
    base = display_name(name)
    return base.rsplit(".", 1)[1].lower() if "." in base.strip(".") else ""


def file_type(name: str, data: bytes) -> FileType | None:
    """The accepted type of a file, by its extension and its content; None if it isn't accepted."""
    entry = TYPES.get(extension(name))
    if entry is None:
        return None
    ft, looks_right = entry
    return ft if looks_right(data) else None


def display_name(name: str) -> str:
    """The name as shown: the last path part, without control characters, at most 200 characters."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 200 - len(ext) - 1] + dot + ext) if dot and len(ext) <= 5 else base[:200]
    return base or "file"


def stored_name(attachment_id: str, ext: str) -> str:
    """The file name on disk."""
    name = f"{attachment_id}.{ext}"
    if not is_stored_name(name):
        raise ValueError(f"unsafe attachment name {name!r}")
    return name


def is_id(text: str) -> bool:
    return bool(_ID.match(text or ""))


def is_stored_name(text: str) -> bool:
    return bool(_STORED.match(text or ""))
