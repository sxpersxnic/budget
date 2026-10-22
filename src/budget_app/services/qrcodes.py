"""The QR codes in a bill's file (F03): a PDF, page by page, or a photo. On the computer only, nothing is sent.

A PDF's pages are rendered with PDFium (`pypdfium2`), so a QR code drawn as vector graphics reads as well as
one embedded as a picture; photos (JPEG, PNG, WebP) are opened with Pillow and turned upright by their EXIF
orientation. ZXing (`zxing-cpp`) finds the QR codes in the images. Pages are first rendered at about 216 dpi
(enough for the 46 mm code of a QR bill); a document in which nothing turns up is read once more at twice
the resolution, for codes printed small or scanned faintly.

HEIC photos are not read (Pillow needs an extra plugin for them): the message asks for JPEG or PNG.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from budget_app.domain import attachments as files
from budget_app.i18n import t
from budget_app.services.context import ValidationError

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)

MAX_PAGES = 20  # a bill's QR code is on its first or last pages; longer documents are read up to here
SCALES = (3.0, 6.0)  # PDF points (1/72 in) to pixels: about 216 dpi, then 432 dpi when nothing was found


def _decode(image: Image) -> list[str]:
    import zxingcpp  # noqa: PLC0415 - the decoder is loaded when a bill is scanned, not at startup

    out = []
    for code in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormats(zxingcpp.BarcodeFormat.QRCode)):
        try:
            out.append(code.bytes.decode("utf-8"))  # QR bills are UTF-8 (guidelines, coding type 1)
        except UnicodeDecodeError:
            out.append(code.text)
    return out


def _pdf_pages(name: str, data: bytes, scale: float) -> Iterator[Image]:
    import pypdfium2 as pdfium  # noqa: PLC0415 - the PDF engine is loaded when a bill is scanned

    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise ValidationError(t("“{name}” can’t be read as a PDF", name=files.display_name(name))) from exc
    try:
        for index in range(min(len(doc), MAX_PAGES)):
            page = doc[index]
            try:
                yield page.render(scale=scale, grayscale=True).to_pil()
            finally:
                page.close()
    finally:
        doc.close()


def _photo(name: str, data: bytes) -> Image:
    from PIL import Image, ImageOps  # noqa: PLC0415 - loaded with the decoder

    try:
        with Image.open(io.BytesIO(data)) as img:
            upright: Any = ImageOps.exif_transpose(img)
            return upright.convert("L")
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError(t("“{name}” can’t be read as a photo", name=files.display_name(name))) from exc


def texts(name: str, data: bytes) -> list[str]:
    """The texts of the QR codes in a PDF or photo, in page order, each once. Raises ValidationError when the
    file is not a PDF or a photo the app can read."""
    ft = files.file_type(name, data)
    if ft is not None and ft.extension == "heic":
        raise ValidationError(
            t(
                "“{name}” is a HEIC photo, which can’t be read yet. Save it as JPEG or PNG.",
                name=files.display_name(name),
            )
        )
    if ft is None or ft.kind not in ("pdf", "image"):
        raise ValidationError(
            t("“{name}” is not a PDF or a photo. Drop a PDF, JPEG or PNG of the bill.", name=files.display_name(name))
        )
    found: list[str] = []
    if ft.kind == "image":
        found = _decode(_photo(name, data))
    else:
        for scale in SCALES:
            for image in _pdf_pages(name, data, scale):
                found += _decode(image)
            if found:
                break
    return list(dict.fromkeys(found))
