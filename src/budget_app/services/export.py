"""Excel / PDF / CSV / JSON exports."""

from __future__ import annotations

import dataclasses
import io
import json
from datetime import date, datetime
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from budget_app.config import BASE_CURRENCY, app_version
from budget_app.domain import months
from budget_app.i18n import t
from budget_app.services.context import Context
from budget_app.services.reports import Line, MonthReport

JSON_FORMAT = "budget-app/export"
JSON_VERSION = 1

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_cell(value):
    """Neutralise spreadsheet formula injection: text cells starting with a formula character
    (e.g. an account named '=HYPERLINK(...)') are prefixed with an apostrophe."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of `df` whose text cells are safe to open in Excel/LibreOffice."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(_escape_cell)
    return out


def _line_row(label: str, line: Line) -> dict:
    return {
        t("Category"): label,
        t("Actual"): round(line.actual, 2),
        t("Budget"): round(line.budget, 2),
        t("Difference"): round(line.diff, 2),
    }


def _expenditure_rows(rep: MonthReport) -> list[dict]:
    rows = []

    def walk(code: str, depth: int) -> None:
        line = rep.expenditure_tree[code]
        rows.append(_line_row("  " * depth + line.label, line))
        for child in rep.children(code):
            if abs(child.actual) > 0.005 or abs(child.budget) > 0.005:
                walk(child.key, depth + 1)

    for code in rep.top_expenditure:
        walk(code, 0)
    return rows


def statement_frames(rep: MonthReport) -> dict[str, pd.DataFrame]:
    """Sheets of the statement export (sheet and column names in the interface language)."""
    tot_e, tot_i = rep.total_expenditure, rep.total_income
    income = [_line_row(tot_i.label, tot_i)]
    income += [_line_row(line.label, line) for line in rep.income_lines]
    expenditure = [_line_row(tot_e.label, tot_e), *_expenditure_rows(rep)]
    account, currency, amount = t("Account"), t("Currency"), t("Amount")
    amount_view = t("Amount ({currency})", currency=rep.currency)
    opening, change, unexplained = t("Opening"), t("Change"), t("Unexplained")

    def rounded(v: float | None) -> float | None:
        return None if v is None else round(v, 2)

    frames: dict[str, pd.DataFrame] = {}
    if rep.complete:
        accounts = [
            {
                account: r.account.name,
                currency: r.account.currency,
                opening: rounded(r.opening),
                amount: round(r.amount, 2),
                change: rounded(r.change),
                unexplained: rounded(r.unexplained),
                amount_view: round(r.amount_view, 2),
            }
            for r in rep.accounts
        ]
        accounts.append(
            {
                account: t("Total wealth"),
                currency: rep.currency,
                opening: rounded(rep.wealth_start),
                amount: None,
                change: rounded(rep.balance_change),
                unexplained: None,
                amount_view: round(sum(r.amount_view for r in rep.accounts), 2),
            }
        )
        if rep.unexplained is not None and rep.balance_change is not None:
            item, value = t("Item"), amount_view
            frames[t("Reconciliation")] = pd.DataFrame(
                [
                    {item: t("Recorded Net"), value: round(tot_i.actual - tot_e.actual, 2)},
                    {item: t("Revaluations"), value: round(rep.total_revaluation.actual, 2)},
                    {item: t("Change in Balances"), value: round(rep.balance_change, 2)},
                    {item: t("Unexplained"), value: round(rep.unexplained, 2)},
                ]
            )
    else:
        accounts = [
            {
                account: t("Projected wealth"),
                currency: rep.currency,
                amount: None,
                amount_view: round(rep.projected_wealth or 0.0, 2),
            }
        ]
    if any(abs(line.actual) > 0.005 for line in rep.revaluation_lines):
        frames[t("Revaluations")] = pd.DataFrame(
            [{t("Category"): line.label, amount_view: round(line.actual, 2)} for line in rep.revaluation_lines]
            + [{t("Category"): rep.total_revaluation.label, amount_view: round(rep.total_revaluation.actual, 2)}]
        )
    return {
        **frames,
        t("Income"): pd.DataFrame(income),
        t("Expenditure"): pd.DataFrame(expenditure),
        t("Accounts"): pd.DataFrame(accounts),
    }


def statement_xlsx(rep: MonthReport) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in statement_frames(rep).items():
            safe_frame(df).to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            for col in sheet.columns:
                width = max(len(str(c.value or "")) for c in col) + 2
                sheet.column_dimensions[col[0].column_letter].width = min(width, 70)
    return buf.getvalue()


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (int, float)):
        return f"{v:,.2f}"
    return str(v)


def statement_pdf(rep: MonthReport) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=t("Statement {month}", month=rep.month),
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(t("Income and account statement – {month}", month=months.label(rep.month)), styles["Title"]),
        Paragraph(
            t("Amounts in {currency}", currency=rep.currency)
            if rep.complete
            else t("Amounts in {currency} · month not yet complete (budget view)", currency=rep.currency),
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
    ]
    for name, df in statement_frames(rep).items():
        story.append(Paragraph(name, styles["Heading2"]))
        data = [list(df.columns)] + [[_fmt(v) for v in row] for row in df.itertuples(index=False)]
        first = 80 if len(df.columns) <= 4 else 45  # wide tables (accounts) need room for their numbers
        table = Table(
            data,
            repeatRows=1,
            hAlign="LEFT",
            colWidths=[first * mm] + [(180 - first) / (len(df.columns) - 1) * mm] * (len(df.columns) - 1),
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#8aa0b8")),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fb")]),
                ]
            )
        )
        story += [table, Spacer(1, 5 * mm)]
    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------ shared PDF pieces (tax summary, year in review)
def money(value: float) -> str:
    """1'234.50, the Swiss format of the app (with a plain hyphen, which the PDF's font has)."""
    return f"{round(value, 2) + 0.0:,.2f}".replace(",", "'")


def pdf_styles() -> dict[str, ParagraphStyle]:
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
        "bold": ParagraphStyle("bold", parent=cell, fontName="Helvetica-Bold"),
        "bold_num": ParagraphStyle("bold_num", parent=cell, fontName="Helvetica-Bold", alignment=2),
        "indent": ParagraphStyle("indent", parent=cell, leftIndent=12),
    }


def para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


ROW_TINT = colors.HexColor("#f6f8fb")
PDF_TABLE_STYLE = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6")),
    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#8aa0b8")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ROW_TINT]),
    ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#8aa0b8")),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
]


def chart_pdf(png: bytes, title: str, subtitle: str = "") -> bytes:
    """One chart image on a landscape A4 page, under its title."""
    buf = io.BytesIO()
    page_w, page_h = landscape(A4)
    margin = 15 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=title,
    )
    styles = getSampleStyleSheet()
    img_w, img_h = ImageReader(io.BytesIO(png)).getSize()
    # fit the page width, but leave room for the title block above a tall image
    width = min(page_w - 2 * margin, (page_h - 2 * margin - 30 * mm) * img_w / img_h)
    story = [Paragraph(title, styles["Title"])]
    if subtitle:
        story.append(Paragraph(subtitle, styles["Normal"]))
    story += [Spacer(1, 4 * mm), Image(io.BytesIO(png), width=width, height=width * img_h / img_w)]
    doc.build(story)
    return buf.getvalue()


def frame_csv(df: pd.DataFrame) -> bytes:
    return safe_frame(df).to_csv(index=False).encode("utf-8")


def frame_xlsx(df: pd.DataFrame, sheet: str = "") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        safe_frame(df).to_excel(writer, sheet_name=(sheet or t("Data"))[:31], index=False)
    return buf.getvalue()


def view_frame(columns: list[dict], rows: list[dict], currency_header: str = "") -> pd.DataFrame:
    """The rows of a list page as shown: its columns with their headers, in that order.

    Fields starting with "_" and the row id stay internal. The amount column shows the currency
    next to the amount on screen; with `currency_header`, it becomes a column of its own after it.
    """
    fields: list[tuple[str, str]] = []  # (row field, header)
    for col in columns:
        fields.append((col["field"], col["headerName"]))
        if currency_header and col["field"] == "amount":
            fields.append(("currency", currency_header))
    return pd.DataFrame([[r.get(f) for f, _ in fields] for r in rows], columns=[h for _, h in fields])


def _plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def everything_json(ctx: Context) -> bytes:
    """Every record as one JSON document, for other tools (F24). It is not meant to be imported back;
    full backups (services/archive.py) are."""
    store = ctx.store

    def table(rows) -> list[dict]:
        return [{k: _plain(v) for k, v in dataclasses.asdict(r).items()} for r in rows]

    doc = {
        "format": JSON_FORMAT,
        "version": JSON_VERSION,
        "app_version": app_version(),
        "exported": datetime.now().isoformat(timespec="seconds"),
        "base_currency": BASE_CURRENCY,
        "settings": dataclasses.asdict(store.settings),
        "accounts": table(store.accounts.all()),
        "income_categories": table(store.income_categories.all()),
        "transactions": table(sorted(store.transactions.all(), key=lambda x: (x.date, x.id))),
        "transaction_items": table(store.transaction_items.all()),
        "attachments": table(store.attachments.all()),  # the records; the files are in full backups
        "account_entries": table(sorted(store.account_entries.all(), key=lambda e: (e.month, e.account_id))),
        "budget_entries": table(sorted(store.budget.all(), key=lambda b: (b.month, b.type, b.category))),
        "recurring": table(sorted(store.recurring.all(), key=lambda r: (r.start, r.id))),
        "payees": table(sorted(store.payees.all(), key=lambda p: (p.name.lower(), p.id))),
        "rules": table(store.rules.all()),
        "holdings": table(sorted(store.holdings.all(), key=lambda h: (h.account_id, h.name.lower()))),
        "trades": table(sorted(store.trades.all(), key=lambda x: (x.date, x.seq))),
        "prices": table(sorted(store.prices.all(), key=lambda x: (x.date, x.holding_id))),
        "tags": table(sorted(store.tags.all(), key=lambda g: (g.name.lower(), g.id))),
        "bills": table(sorted(store.bills.all(), key=lambda b: (b.added, b.id))),  # F03
        "goals": table(sorted(store.goals.all(), key=lambda g: (g.start, g.id))),  # F14
        "tax_sections": table(sorted(store.tax_sections.all(), key=lambda s: s.id)),  # F20
        "subscription_ignores": table(sorted(store.subscription_ignores.all(), key=lambda s: (s.key, s.id))),  # F17
    }
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
