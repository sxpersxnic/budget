"""The year in review (F19) as Excel and as PDF, built like the statement export.

The Excel file has a sheet per part of the review (summary, months, spending, income, biggest payments), each
column in the interface language and formula-safe. The PDF is the same on paper: the summary table, the chart the
page rendered (when it hands one over, like the Overview's *Save as PDF*), then the tables.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date

import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, KeepTogether, SimpleDocTemplate, Spacer, Table, TableStyle

from budget_app import i18n
from budget_app.domain import months, trends
from budget_app.domain.coicop import get_coicop
from budget_app.i18n import t
from budget_app.services.export import PDF_TABLE_STYLE, money, para, pdf_styles, safe_frame
from budget_app.services.review import Figure, YearReview


def file_name(year: int, ext: str) -> str:
    return f"{t('Year in review')} {year}.{ext}"


def period_label(review: YearReview) -> str:
    """'2025', or '2026 so far (January to October)' for the current year."""
    p = review.period
    if p.complete:
        return str(p.year)
    first, last = i18n.MONTHS_LONG[i18n.language()][0], i18n.MONTHS_LONG[i18n.language()][int(p.last[5:]) - 1]
    return t("{year} so far ({first} to {last})", year=str(p.year), first=first, last=last)


def comparison_label(review: YearReview) -> str:
    """What the year is compared with."""
    p = review.period
    if p.complete:
        return t("compared with {year}", year=str(p.year - 1))
    return t("compared with the same months of {year}", year=str(p.year - 1))


def percent(value: float | None) -> str:
    """A rate as '23 %' (negative rates keep their sign), '–' without one."""
    if value is None:
        return "–"
    return f"{value * 100:.0f} %".replace("-", "−")


def change_text(change: trends.Change) -> str:
    """'+12 %', '−8 %', 'new' or '' (nothing on either side) for the exports."""
    if change.empty:
        return ""
    if change.ratio is None:
        return t("new")
    if abs(change.ratio) < 0.005:
        return "0 %"
    return ("+" if change.ratio > 0 else "−") + trends.percent(change.ratio)


# ------------------------------------------------------------------ frames
def _figure_rows(lines: list[Figure], review: YearReview, label: str) -> list[dict]:
    this_year, last_year = str(review.year), str(review.year - 1)
    return [
        {
            label: line.label,
            this_year: round(line.amount, 2),
            last_year: round(line.previous, 2),
            t("Change"): change_text(line.change),
        }
        for line in lines
    ]


def frames(review: YearReview) -> dict[str, pd.DataFrame]:
    """The sheets of the export, in the interface language."""
    this_year, last_year = str(review.year), str(review.year - 1)
    item, change = t("Item"), t("Change")

    def row(label: str, figure: Figure) -> dict:
        return {item: label, this_year: round(figure.amount, 2), last_year: round(figure.previous, 2)} | {
            change: change_text(figure.change)
        }

    summary = [
        row(t("Income"), review.income),
        row(t("Expenditure"), review.expenditure),
        row(t("Saved"), review.saved),
        {
            item: t("Savings rate"),
            this_year: percent(review.savings_rate),
            last_year: percent(review.previous_savings_rate),
            change: "",
        },
    ]
    if review.wealth_end is not None:
        summary += [
            {item: t("Wealth at the start"), this_year: _rounded(review.wealth_start), last_year: None, change: ""},
            {
                item: t("Wealth at the end ({month})", month=months.label(review.wealth_month)),
                this_year: _rounded(review.wealth_end),
                last_year: None,
                change: "",
            },
            {
                item: t("Wealth change"),
                this_year: _rounded(review.wealth_change),
                last_year: _rounded(review.previous_wealth_change),
                change: "",
            },
        ]
    month, income, expenditure, saved = t("Month"), t("Income"), t("Expenditure"), t("Saved")
    monthly = [
        {month: m.month, income: round(m.income, 2), expenditure: round(m.expenditure, 2), saved: round(m.saved, 2)}
        for m in review.monthly
    ]
    coicop = get_coicop()
    biggest = [
        {
            t("Date"): b.date.isoformat(),
            t("Payee"): b.name,
            t("Category"): coicop.label(b.category),
            t("Amount ({currency})", currency=review.currency): round(b.amount, 2),
        }
        for b in review.biggest
    ]
    return {
        t("Summary"): pd.DataFrame(summary),
        t("Months"): pd.DataFrame(monthly),
        t("Expenditure"): pd.DataFrame(_figure_rows(review.expenditure_lines, review, t("Category"))),
        t("Income"): pd.DataFrame(_figure_rows(review.income_lines, review, t("Category"))),
        t("Biggest Expenses"): pd.DataFrame(biggest),
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def xlsx(review: YearReview) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in frames(review).items():
            safe_frame(df).to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]
            for col in sheet.columns:
                width = max(len(str(c.value or "")) for c in col) + 2
                sheet.column_dimensions[col[0].column_letter].width = min(width, 60)
    return buf.getvalue()


# ------------------------------------------------------------------ PDF
def _table(df: pd.DataFrame, st: dict[str, ParagraphStyle], widths: Sequence[float], numeric_from: int = 1) -> Table:
    """A frame as a PDF table: the first `numeric_from` columns are text (left), the others figures (right)."""
    data = [[para(str(c), st["head_num"] if i >= numeric_from else st["head"]) for i, c in enumerate(df.columns)]]
    for row in df.itertuples(index=False):
        data.append([para(_fmt(v), st["num"] if i >= numeric_from else st["cell"]) for i, v in enumerate(row)])
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([*PDF_TABLE_STYLE[:3], *PDF_TABLE_STYLE[5:]]))
    return table


def _fmt(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (int, float)):
        return money(float(value))
    return str(value)


def pdf(review: YearReview, chart_png: bytes | None = None, today: date | None = None) -> bytes:
    buf = io.BytesIO()
    title = t("Year in review {year}", year=str(review.year))
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm, title=title
    )
    st = pdf_styles()
    made = i18n.date_label(today or date.today())
    sheets = frames(review)
    subtitle = " · ".join(
        [
            period_label(review),
            comparison_label(review),
            t("Amounts in {currency}", currency=review.currency),
            t("made on {date}", date=made),
        ]
    )
    story: list = [para(title, st["title"]), para(subtitle, st["normal"]), Spacer(1, 6 * mm)]
    story += [_table(sheets[t("Summary")], st, [80, 34, 34, 32]), Spacer(1, 6 * mm)]
    if chart_png:
        img_w, img_h = ImageReader(io.BytesIO(chart_png)).getSize()
        width = 180 * mm
        story += [
            para(t("Income and Expenditure"), st["h2"]),
            Image(io.BytesIO(chart_png), width=width, height=width * img_h / img_w),
            Spacer(1, 6 * mm),
        ]
    sections = [
        (t("Spending by Category"), sheets[t("Expenditure")], [80, 34, 34, 32]),
        (t("Income"), sheets[t("Income")], [80, 34, 34, 32]),
        (t("Biggest Expenses"), sheets[t("Biggest Expenses")], [22, 58, 66, 34]),
        (t("Month by Month"), sheets[t("Months")], [45, 45, 45, 45]),
    ]
    for heading, df, widths in sections:
        numeric_from = 3 if heading == t("Biggest Expenses") else 1
        body = _table(df, st, widths, numeric_from) if not df.empty else para(t("Nothing recorded"), st["normal"])
        story += [KeepTogether([para(heading, st["h2"]), Spacer(1, 2 * mm), body]), Spacer(1, 6 * mm)]

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColorRGB(0.47, 0.47, 0.47)
        canvas.drawString(15 * mm, 10 * mm, title)
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, t("Page {n}", n=document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
