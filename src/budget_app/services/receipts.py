"""Receipt scanning (F04): a till receipt's PDF or photo becomes a prefilled expenditure with the file attached.

`prepare` checks the file (PDF or photo, like an attachment) and makes the pictures the engine reads and the one
the sheet shows, in the event loop (PDFium is not thread-safe, see `qrcodes`); a PDF that carries its own text
(an e-receipt) needs no engine at all. `read` runs the engine (`ocr.recognizer`, or one the caller injects) on the
pictures, off the event loop, and parses what it read (`domain.receipts`). `prefill` turns the receipt into the
transaction form's starting values: date, amount, currency, the merchant as payee (a known payee keeps its
spelling, F09) and, when the lines add up to the total, the item lines (F11). The user checks everything before
saving; nothing is written here. The file itself is attached when the transaction is saved (F12).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from budget_app.domain import attachments as files
from budget_app.domain import receipts
from budget_app.domain.models import TX_EXPENDITURE
from budget_app.i18n import t
from budget_app.services import ocr, qrcodes
from budget_app.services import payees as payee_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)

MAX_PAGES = 3  # a receipt is one page; a few more for a PDF with the terms attached
SCALE = 3.0  # PDF points to pixels: about 216 dpi, plenty for printed text
PREVIEW_SIDE = 1200  # the longer side of the picture shown beside the form, in pixels


@dataclass
class Prepared:
    """A receipt file ready to be read: its pictures for the engine, its preview, and a PDF's own text."""

    name: str
    data: bytes
    images: list[Image] = field(default_factory=list)
    preview: Image | None = None
    text_rows: list[str] = field(default_factory=list)  # a PDF with a text layer: read without any engine


def prepare(name: str, data: bytes) -> Prepared:
    """Check the file and make its pictures; raises ValidationError for anything but a readable PDF or photo."""
    ft = files.file_type(name, data)
    shown = files.display_name(name)
    if ft is None or ft.kind not in ("pdf", "image") or ft.extension == "heic":
        if ft is not None and ft.extension == "heic":
            raise ValidationError(
                t("“{name}” is a HEIC photo, which can’t be read yet. Save it as JPEG or PNG.", name=shown)
            )
        raise ValidationError(
            t("“{name}” is not a PDF or a photo. Drop a PDF, JPEG or PNG of the receipt.", name=shown)
        )
    if len(data) > files.MAX_BYTES:
        raise ValidationError(t("“{name}” is larger than 20 MB", name=shown))
    out = Prepared(shown, data)
    if ft.kind == "image":
        out.images = [qrcodes._photo(name, data, mode="RGB")]
    else:
        out.images = list(qrcodes._pdf_pages(name, data, SCALE, pages=MAX_PAGES, grayscale=False))
        out.text_rows = pdf_text(data)
    out.preview = preview(out.images[0])
    return out


def pdf_text(data: bytes) -> list[str]:
    """The rows of a PDF's own text layer, when it has one with an amount in it (an e-receipt); else []."""
    import pypdfium2 as pdfium  # noqa: PLC0415 - loaded when a receipt is read

    rows: list[str] = []
    doc = pdfium.PdfDocument(data)
    try:
        for index in range(min(len(doc), MAX_PAGES)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    rows += textpage.get_text_bounded().splitlines()
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        doc.close()
    rows = [r.strip() for r in rows if r.strip()]
    return rows if any(receipts.amounts(r) for r in rows) else []


def preview(image: Image) -> Image:
    """The picture as shown beside the form: at most 1200 pixels on its longer side."""
    img = image.copy()
    img.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
    return img


def read(
    prepared: Prepared,
    recognizer: ocr.Recognizer | None = None,
    today: date | None = None,
    default_currency: str = "CHF",
) -> receipts.Receipt | None:
    """What the receipt says, or None when there is nothing to read it with (no text layer, no engine).

    Raises ValidationError when the engine fails on this file; the form then starts empty.
    """
    if prepared.text_rows:
        return receipts.parse(prepared.text_rows, today, default_currency)
    reader = recognizer or ocr.recognizer()
    if reader is None:
        return None
    try:
        rows = [row for image in prepared.images for row in receipts.rows(reader(image))]
    except Exception as exc:
        log.warning("Reading the receipt %s failed", prepared.name, exc_info=True)
        raise ValidationError(
            t("“{name}” couldn’t be read. Type the amount, date and payee.", name=prepared.name)
        ) from exc
    return receipts.parse(rows, today, default_currency)


def prefill(
    ctx: Context, receipt: receipts.Receipt | None, today: date | None = None
) -> tuple[TransactionInput, list[ItemInput]]:
    """The expenditure the form starts with, and its item lines: from the receipt where it was read, else today's
    date and the display currency only."""
    today = today or date.today()
    inp = TransactionInput(date=today, type=TX_EXPENDITURE, currency=ctx.store.settings.display_currency)
    if receipt is None:
        return inp, []
    inp.date = receipt.date or today
    inp.amount = receipt.total if receipt.total and receipt.total > 0 else None
    inp.currency = receipt.currency or inp.currency
    if receipt.merchant:
        inp.payee = payee_svc.resolve(ctx, receipt.merchant) or payee_svc.clean_name(receipt.merchant)
    items = [ItemInput(name=line.name, quantity=1.0, unit_price=line.amount) for line in receipt.lines]
    return inp, items
