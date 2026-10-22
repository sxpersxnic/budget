"""QR-bill files for the tests (F03), made on the fly so no binary blob has to be trusted or kept in step.

`payload` writes the text of a QR bill (structured addresses, version 0200); `pdf` draws its QR code with the
Swiss cross on an A4 invoice, as vector graphics or as an embedded picture, like e-bills do; `png` and `jpeg`
are photos of the code alone. segno (BSD) makes the QR matrix, reportlab and Pillow the files.
"""

from __future__ import annotations

import io

import segno
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def payload(
    iban: str = "CH4431999123000889012",
    creditor: str = "Robert Schneider AG",
    amount: str = "1949.75",
    currency: str = "CHF",
    reference_type: str = "QRR",
    reference: str = "210000000003139471430009017",
    message: str = "Auftrag vom 15.06.2020",
    bill_info: str = "",
) -> str:
    lines = [
        "SPC",
        "0200",
        "1",
        iban,
        "S",
        creditor,
        "Rue du Lac",
        "1268",
        "2501",
        "Biel",
        "CH",
        *[""] * 7,
        amount,
        currency,
        "S",
        "Pia-Maria Rutschmann-Schnyder",
        "Grosse Marktgasse",
        "28",
        "9400",
        "Rorschach",
        "CH",
        reference_type,
        reference,
        message,
        "EPD",
    ]
    if bill_info:
        lines.append(bill_info)
    return "\r\n".join(lines)


def _qr(text: str) -> segno.QRCode:
    return segno.make(text, error="m", micro=False, encoding="utf-8")


def code_image(text: str, scale: int = 6, cross: bool = True) -> Image.Image:
    """The QR code as a picture, with the Swiss cross in the middle as the guidelines prescribe."""
    buf = io.BytesIO()
    _qr(text).save(buf, kind="png", scale=scale, border=4)
    img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    if cross:
        w = img.width
        side = round(w * 7 / 46)  # 7 mm of the 46 mm code
        x0 = (w - side) // 2
        draw = ImageDraw.Draw(img)
        draw.rectangle((x0, x0, x0 + side, x0 + side), fill="white")
        inner = round(side * 0.86)
        i0 = (w - inner) // 2
        draw.rectangle((i0, i0, i0 + inner, i0 + inner), fill="black")
        bar, arm = max(1, round(inner * 0.18)), round(inner * 0.6)
        c = w // 2
        draw.rectangle((c - bar // 2, c - arm // 2, c + bar // 2, c + arm // 2), fill="white")
        draw.rectangle((c - arm // 2, c - bar // 2, c + arm // 2, c + bar // 2), fill="white")
    return img


def png(text: str, scale: int = 6) -> bytes:
    buf = io.BytesIO()
    code_image(text, scale).save(buf, format="PNG")
    return buf.getvalue()


def jpeg(text: str, scale: int = 8, rotate: bool = False) -> bytes:
    """A photo of the code; with `rotate`, stored on its side with an EXIF tag that turns it upright."""
    img = code_image(text, scale)
    buf = io.BytesIO()
    if rotate:
        exif = Image.Exif()
        exif[0x0112] = 6  # orientation: rotate 90° clockwise to view
        img.rotate(90, expand=True).save(buf, format="JPEG", quality=90, exif=exif)
    else:
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _draw_vector(c: canvas.Canvas, text: str, x: float, y: float, size: float = 46 * mm) -> None:
    qr = _qr(text)
    rows = list(qr.matrix_iter(border=0))
    n = len(rows)
    module = size / n
    c.setFillColorRGB(0, 0, 0)
    for r, row in enumerate(rows):
        for col, dark in enumerate(row):
            if dark:
                c.rect(x + col * module, y + size - (r + 1) * module, module, module, stroke=0, fill=1)
    side = 7 * mm
    cx, cy = x + size / 2, y + size / 2
    c.setFillColorRGB(1, 1, 1)
    c.rect(cx - side / 2, cy - side / 2, side, side, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    inner = side * 0.86
    c.rect(cx - inner / 2, cy - inner / 2, inner, inner, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    bar, arm = inner * 0.18, inner * 0.6
    c.rect(cx - bar / 2, cy - arm / 2, bar, arm, stroke=0, fill=1)
    c.rect(cx - arm / 2, cy - bar / 2, arm, bar, stroke=0, fill=1)


def pdf(*texts: str, vector: bool = True, pages_before: int = 0) -> bytes:
    """An A4 invoice with one payment part per text (one page each), after `pages_before` pages of prose."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for k in range(pages_before):
        c.setFont("Helvetica", 11)
        c.drawString(20 * mm, 270 * mm, f"Invoice, page {k + 1}")
        c.showPage()
    for text in texts:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(5 * mm, 100 * mm, "Receipt")
        c.drawString(67 * mm, 100 * mm, "Payment part")
        c.line(0, 105 * mm, A4[0], 105 * mm)
        if vector:
            _draw_vector(c, text, 67 * mm, 42 * mm)
        else:
            c.drawImage(ImageReader(code_image(text, scale=4)), 67 * mm, 42 * mm, 46 * mm, 46 * mm)
        c.showPage()
    c.save()
    return buf.getvalue()
