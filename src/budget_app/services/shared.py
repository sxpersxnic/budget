"""Who owes what (F23): the people you paid for, built on advance payments.

An expenditure *partly paid for others* (`Transaction.is_advance`, F-base) names a `debtor` and the share paid
for them; a repayment is an income with `settles_id` (§3). This module reads them per **person** (debtors compared
folded, `coicop.fold`): the open items with what is still outstanding, the oldest one, and what was settled this
calendar year; **settling up** records the repayment of an amount received as one repayment per open item, oldest
first, through `transactions.save` (so every rule of a repayment holds), in one undoable action; and a **summary**
of a person's open items as text or PDF, to copy or save – the app sends nothing.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from budget_app import i18n
from budget_app.domain.coicop import fold, get_coicop
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Transaction
from budget_app.i18n import N_, t, tn
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import EPS, OpenAdvance, TransactionInput
from budget_app.services.undo import recorded


@dataclass
class Person:
    """Someone you paid for: their open items (oldest first) and what they settled this year, per currency."""

    name: str  # as last written
    key: str  # `fold(name)`: how debtors are matched
    items: list[OpenAdvance] = field(default_factory=list)
    settled_year: dict[str, float] = field(default_factory=dict)

    @property
    def open(self) -> dict[str, float]:
        """What is outstanding per currency, in the order the currencies first appear."""
        out: dict[str, float] = {}
        for item in self.items:
            out[item.tx.currency] = round(out.get(item.tx.currency, 0.0) + item.outstanding, 2)
        return out

    @property
    def oldest(self) -> date | None:
        return self.items[0].tx.date if self.items else None

    def open_in(self, currency: str) -> list[OpenAdvance]:
        return [item for item in self.items if item.tx.currency == currency]


# ------------------------------------------------------------------ reading
def _today(today: date | None) -> date:
    return today or date.today()


def people(ctx: Context, today: date | None = None) -> list[Person]:
    """Everyone named as debtor: those with open items first, by their oldest item, then the others by name. A
    repayment counts for the person of the advance it settles, so renaming the debtor of an advance moves it along."""
    year = _today(today).year
    rows = sorted(ctx.store.transactions.all(), key=lambda x: (x.date, x.id))
    advances = {tx.id: tx for tx in rows if tx.type == TX_EXPENDITURE and tx.is_advance}
    settled: dict[str, float] = {}
    for tx in rows:
        if tx.settles_id:
            settled[tx.settles_id] = settled.get(tx.settles_id, 0.0) + tx.amount
    found: dict[str, Person] = {}
    latest: dict[str, date] = {}
    for tx in rows:
        if not tx.is_advance:
            continue
        source = advances.get(tx.settles_id, tx) if tx.type == TX_INCOME else tx
        if not source.debtor.strip():
            continue
        key = fold(source.debtor)
        found_person = found.setdefault(key, Person(source.debtor.strip(), key))
        if source.date >= latest.get(key, date.min):
            found_person.name, latest[key] = source.debtor.strip(), source.date
        if tx.type == TX_EXPENDITURE:
            left = round(tx.advance_amount - settled.get(tx.id, 0.0), 2)
            if left > EPS:
                found_person.items.append(OpenAdvance(tx, left))
        elif tx.type == TX_INCOME and tx.date.year == year:
            found_person.settled_year[tx.currency] = round(
                found_person.settled_year.get(tx.currency, 0.0) + tx.amount, 2
            )
    return sorted(found.values(), key=lambda p: (p.oldest is None, p.oldest or date.min, p.name.lower()))


def person(ctx: Context, key: str, today: date | None = None) -> Person:
    for p in people(ctx, today):
        if p.key == key:
            return p
    raise ValidationError(t("Nobody of that name owes anything"))


def item_label(tx: Transaction) -> str:
    """What an item was: the payee, else the category, with the note."""
    what = tx.payee or get_coicop().label(tx.category)
    return f"{what} · {tx.comment}" if tx.comment else what


# ------------------------------------------------------------------ settling up
@dataclass
class Settlement:
    """How an amount received splits over a person's open items, oldest first."""

    parts: list[tuple[OpenAdvance, float]]

    @property
    def total(self) -> float:
        return round(sum(a for _, a in self.parts), 2)


def plan(p: Person, currency: str, amount: float | None) -> Settlement:
    """The repayments an amount received settles: the oldest items first, the last one partly. Refused above the
    total outstanding in that currency."""
    items = p.open_in(currency)
    total = round(sum(i.outstanding for i in items), 2)
    if not items:
        raise ValidationError(t("{name} owes nothing in {currency}", name=p.name, currency=currency))
    if amount is None or amount <= 0:
        raise ValidationError(t("Enter the amount received"))
    if amount > total + EPS:
        raise ValidationError(t("{name} owes {amount}, not more", name=p.name, amount=f"{total:,.2f} {currency}"))
    left, parts = round(amount, 2), []
    for item in items:
        if left <= EPS:
            break
        part = round(min(left, item.outstanding), 2)
        parts.append((item, part))
        left = round(left - part, 2)
    return Settlement(parts)


@recorded(N_("Settle up"))
def settle(
    ctx: Context,
    key: str,
    currency: str,
    amount: float | None,
    to_account: str,
    on: date | None = None,
    comment: str = "",
) -> list[Transaction]:
    """Record what a person paid back: one repayment per open item it covers, oldest first, into `to_account`."""
    p = person(ctx, key)
    settlement = plan(p, currency, amount)
    day = _today(on)
    out = []
    for item, part in settlement.parts:
        inp = TransactionInput(
            date=day,
            type=TX_INCOME,
            amount=part,
            currency=currency,
            to_account=to_account,
            is_advance=True,
            advance_account=item.tx.advance_account,
            settles_id=item.tx.id,
            comment=comment.strip() or t("Settle-up {name}", name=p.name),
            payee=p.name,
        )
        out.append(tx_svc.save(ctx, inp))
    return out


# ------------------------------------------------------------------ the summary
def _money(value: float, currency: str) -> str:
    return f"{round(value, 2) + 0.0:,.2f} {currency}".replace(",", "'")


def summary_lines(p: Person, today: date | None = None) -> list[str]:
    """The summary's lines: a heading, one line per open item, the totals and what was settled this year."""
    day = _today(today)
    lines = [t("Open items for {name} · {date}", name=p.name, date=i18n.date_label(day)), ""]
    for item in p.items:
        tx = item.tx
        share = (
            _money(item.outstanding, tx.currency)
            if abs(item.outstanding - tx.advance_amount) < EPS
            else t(
                "{open} of {share}",
                open=_money(item.outstanding, tx.currency),
                share=_money(tx.advance_amount, tx.currency),
            )
        )
        lines.append(f"{i18n.date_label(tx.date)} · {item_label(tx)} · {share}")
    if not p.items:
        lines.append(t("Nothing open."))
    return [*lines, "", *totals_lines(p, day)]


def totals_lines(p: Person, today: date | None = None) -> list[str]:
    """What is open in total and what was settled this year, one line per currency."""
    year = str(_today(today).year)
    return [t("Total open: {amount}", amount=_money(total, cur)) for cur, total in p.open.items()] + [
        t("Settled in {year}: {amount}", year=year, amount=_money(total, cur)) for cur, total in p.settled_year.items()
    ]


def summary_text(p: Person, today: date | None = None) -> str:
    return "\n".join(summary_lines(p, today)) + "\n"


def file_name(p: Person, ext: str) -> str:
    safe = "".join(c for c in p.name if c.isalnum() or c in " -_").strip() or "person"
    return f"{t('Open items')} {safe}.{ext}"


def summary_pdf(p: Person, today: date | None = None) -> bytes:
    """One page: the heading, a table of the open items (date, what, open) and the totals."""
    day = _today(today)
    buf = io.BytesIO()
    title = t("Open items for {name} · {date}", name=p.name, date=i18n.date_label(day))
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, title=title)
    base = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=base["Normal"], fontSize=10, leading=13)
    head = ParagraphStyle("h", parent=base["Title"], fontSize=16, leading=20, alignment=0)
    story = [Paragraph(escape(title), head), Spacer(1, 4 * mm)]
    if p.items:
        rows = [[t("Date"), t("What"), t("Open")]]
        for item in p.items:
            rows.append(
                [
                    i18n.date_label(item.tx.date),
                    Paragraph(escape(item_label(item.tx)), normal),
                    _money(item.outstanding, item.tx.currency),
                ]
            )
        table = Table(rows, colWidths=[30 * mm, 100 * mm, 38 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                    ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#999999")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph(escape(t("Nothing open.")), normal))
    story.append(Spacer(1, 5 * mm))
    story.extend(Paragraph(escape(line), normal) for line in totals_lines(p, day))
    doc.build(story)
    return buf.getvalue()


def count_label(n: int) -> str:
    return tn("{n} item", "{n} items", n)
