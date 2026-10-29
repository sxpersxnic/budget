"""The tax summary (F20) as PDF, as CSV, and its proofs as a ZIP of the attachment files.

The PDF has a summary page (each section's total and number of rows, the note that the app judges nothing) and
then one table per section: date, payee, category, amount and the names of the attached files as the proof
column; the securities list has the withholding tax as a column of its own. The CSV has one row per section row.
The ZIP holds the PDF, the CSV and the attachment files of the listed rows, one folder per section. A file is
read only after the check of F12 (`attachments.verified`: an accepted type by name and content); a file that is
missing or fails it is left out and named.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from budget_app import i18n
from budget_app.i18n import t, tn
from budget_app.services import attachments as attachments_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.export import frame_csv
from budget_app.services.tax import CURRENCY, Entry, SectionSummary, Summary

MAX_ZIP_BYTES = 1024 * 1024 * 1024  # the files are read into memory; 1 GB is far more than a year of receipts
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


def money(value: float) -> str:
    """1'234.50, the Swiss format of the app (with a plain hyphen, which the PDF's font has)."""
    return f"{round(value, 2) + 0.0:,.2f}".replace(",", "'")


def file_name(year: int, ext: str) -> str:
    return f"{t('Tax summary')} {year}.{ext}"


def note() -> str:
    return t(
        "Cantonal rules differ. The app lists the amounts of each section; whether and how much you can deduct "
        "is for you to judge."
    )


def overlap_note() -> str:
    return t("Sections can overlap: a transaction counts in one section by its category and in another by a tag.")


def proof(entry: Entry) -> str:
    return ", ".join(a.name for a in entry.attachments)


# ------------------------------------------------------------------ CSV
def frame(summary: Summary) -> pd.DataFrame:
    """One row per section row, sections in their order."""
    columns = [
        t("Section"),
        t("Date"),
        t("Payee"),
        t("Category"),
        t("Note"),
        t("Amount ({currency})", currency=CURRENCY),
        t("Tax withheld ({currency})", currency=CURRENCY),
        t("Proof"),
    ]
    rows = [
        [
            t(s.section.title),
            e.date.isoformat(),
            e.payee,
            e.category,
            e.note,
            e.amount,
            e.tax if e.tax else None,
            proof(e),
        ]
        for s in summary.sections
        for e in s.entries
    ]
    return pd.DataFrame(rows, columns=columns)


def summary_csv(summary: Summary) -> bytes:
    return frame_csv(frame(summary))


# ------------------------------------------------------------------ PDF
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10)
    return {
        "title": base["Title"],
        "h2": base["Heading2"],
        "normal": base["Normal"],
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8.5, leading=11, textColor="#555555"),
        "cell": cell,
        "num": ParagraphStyle("num", parent=cell, alignment=2),
        "head": ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold"),
        "head_num": ParagraphStyle("head_num", parent=cell, fontName="Helvetica-Bold", alignment=2),
        "bold_num": ParagraphStyle("bold_num", parent=cell, fontName="Helvetica-Bold", alignment=2),
        "indent": ParagraphStyle("indent", parent=cell, leftIndent=12),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


ROW_TINT = colors.HexColor("#f6f8fb")
TABLE_STYLE = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6")),
    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#8aa0b8")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ROW_TINT]),
    ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#8aa0b8")),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
]


def _summary_table(summary: Summary, st: dict[str, ParagraphStyle]) -> Table:
    total = t("Total ({currency})", currency=CURRENCY)
    data = [[_p(t("Section"), st["head"]), _p(t("Rows"), st["head_num"]), _p(total, st["head_num"])]]
    for s in summary.sections:
        title = _p(t(s.section.title), st["cell"])
        total_text = money(s.total) if s.entries else "–"
        data.append([title, _p(str(len(s.entries)), st["num"]), _p(total_text, st["num"])])
        if s.tax_total:
            data.append([_p(t("of which tax withheld"), st["indent"]), "", _p(money(s.tax_total), st["num"])])
    table = Table(data, colWidths=[120 * mm, 20 * mm, 40 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([*TABLE_STYLE[:3], ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_TINT])]))
    return table


def _section_table(s: SectionSummary, st: dict[str, ParagraphStyle]) -> Table:
    with_tax = any(e.tax for e in s.entries)
    head = [t("Date"), t("Payee"), t("Category"), t("Amount ({currency})", currency=CURRENCY)]
    widths = [19, 46, 50, 24, 41]
    if with_tax:
        head.append(t("Tax withheld ({currency})", currency=CURRENCY))
        widths = [19, 42, 36, 22, 22, 39]
    numeric = len(head)
    head.append(t("Proof"))
    data = [[_p(h, st["head_num"] if 3 <= i < numeric else st["head"]) for i, h in enumerate(head)]]
    for e in s.entries:
        row = [
            _p(f"{e.date:%d.%m.%Y}", st["cell"]),
            _p(e.label, st["cell"]),
            _p(e.category, st["cell"]),
            _p(money(e.amount), st["num"]),
        ]
        if with_tax:
            row.append(_p(money(e.tax) if e.tax else "", st["num"]))
        row.append(_p(proof(e), st["cell"]))
        data.append(row)
    total = ["", _p(t("Total"), st["head"]), "", _p(money(s.total), st["bold_num"])]
    if with_tax:
        total.append(_p(money(s.tax_total), st["bold_num"]))
    data.append([*total, ""])
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle(TABLE_STYLE))
    return table


def summary_pdf(summary: Summary, today: date | None = None) -> bytes:
    buf = io.BytesIO()
    title = t("Tax summary {year}", year=str(summary.year))
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=title,
    )
    st = _styles()
    made = i18n.date_label(today or date.today())
    story = [
        _p(title, st["title"]),
        _p(t("Amounts in {currency} · made on {date}", currency=CURRENCY, date=made), st["normal"]),
        Spacer(1, 5 * mm),
        _p(note(), st["normal"]),
        Spacer(1, 2 * mm),
        _p(overlap_note(), st["small"]),
        Spacer(1, 6 * mm),
        _summary_table(summary, st),
        PageBreak(),
    ]
    for s in summary.sections:
        body = _section_table(s, st) if s.entries else _p(t("Nothing in {year}", year=str(summary.year)), st["normal"])
        heading = [_p(t(s.section.title), st["h2"]), _p(t(s.section.note), st["small"]), Spacer(1, 2 * mm)]
        story += [KeepTogether([*heading, body]), Spacer(1, 6 * mm)]

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(15 * mm, 10 * mm, title)
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, t("Page {n}", n=document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ------------------------------------------------------------------ ZIP with the proofs
def safe_name(text: str, limit: int = 80) -> str:
    """A file or folder name that works on every system: no path characters, no control characters."""
    clean = _UNSAFE.sub(" ", unicodedata.normalize("NFC", text)).strip(" .")
    clean = re.sub(r"\s+", " ", clean)
    return clean[:limit].rstrip(" .") or "_"


@dataclass
class Proofs:
    data: bytes
    files: int  # attachment files in the ZIP
    skipped: list[str]  # why a file was left out, one line each


def proofs_zip(ctx: Context, summary: Summary, today: date | None = None) -> Proofs:
    """The PDF, the CSV and every attachment of the listed rows, one folder per section (numbered in order)."""
    buf = io.BytesIO()
    files, skipped, total = 0, [], 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(file_name(summary.year, "pdf"), summary_pdf(summary, today))
        zf.writestr(file_name(summary.year, "csv"), summary_csv(summary))
        for n, s in enumerate(summary.sections, start=1):
            folder = f"{n:02d} {safe_name(t(s.section.title))}"
            used: set[str] = set()
            for e in s.entries:
                for att in e.attachments:
                    try:
                        _, data, _ = attachments_svc.verified(ctx, att)
                    except ValidationError as exc:
                        skipped.append(str(exc))
                        continue
                    total += len(data)
                    if total > MAX_ZIP_BYTES:
                        raise ValidationError(t("The attachments are too large for one file."))
                    zf.writestr(f"{folder}/{_unique(e, att.name, used)}", data)
                    files += 1
    return Proofs(buf.getvalue(), files, list(dict.fromkeys(skipped)))


def _unique(entry: Entry, name: str, used: set[str]) -> str:
    """ "2025-03-08 Dr. Muster - invoice.pdf", numbered when a folder has that name already."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    who = safe_name(entry.label, 40) if entry.label else ""
    base = safe_name(" - ".join(x for x in (f"{entry.date.isoformat()} {who}".strip(), stem) if x), 120)
    suffix = f".{safe_name(ext, 8).lower()}" if ext else ""
    candidate, k = base + suffix, 2
    while candidate.casefold() in used:
        candidate, k = f"{base} ({k}){suffix}", k + 1
    used.add(candidate.casefold())
    return candidate


def skipped_message(proofs: Proofs) -> str:
    return tn("{n} file was left out", "{n} files were left out", len(proofs.skipped))
