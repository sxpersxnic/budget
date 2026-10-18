"""Excel / PDF / CSV exports."""

from __future__ import annotations

import io

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from budget_app.domain import months
from budget_app.i18n import t
from budget_app.services.reports import Line, MonthReport

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
    if rep.complete:
        accounts = [
            {
                account: r.account.name,
                currency: r.account.currency,
                amount: round(r.amount, 2),
                amount_view: round(r.amount_view, 2),
            }
            for r in rep.accounts
        ]
        accounts.append(
            {
                account: t("Total wealth"),
                currency: rep.currency,
                amount: None,
                amount_view: round(sum(r.amount_view for r in rep.accounts), 2),
            }
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
    return {
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
        table = Table(
            data,
            repeatRows=1,
            hAlign="LEFT",
            colWidths=[80 * mm] + [(180 - 80) / (len(df.columns) - 1) * mm] * (len(df.columns) - 1),
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


def frame_csv(df: pd.DataFrame) -> bytes:
    return safe_frame(df).to_csv(index=False).encode("utf-8")


def frame_xlsx(df: pd.DataFrame, sheet: str = "") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        safe_frame(df).to_excel(writer, sheet_name=(sheet or t("Data"))[:31], index=False)
    return buf.getvalue()
