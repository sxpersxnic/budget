"""Till receipts (F04), pure: from the text an OCR engine read to merchant, date, total, currency and lines.

The engine returns text boxes with their position. `rows` puts the boxes that lie on one line of the receipt
together ("TOTAL CHF" and "11.45" are two boxes on one row), left to right, top to bottom; `parse` reads the rows:

- the **total**: the last row that names a total ("Total", "Summe", "Montant", "Totale", "Amount due", …) and
  carries an amount, rows of a subtotal, VAT, change or payment left out; without such a row, the largest
  amount on the receipt;
- the **date**: the first date on the receipt (`28.10.2026`, `28.10.26`, `28/10/2026`, `2026-10-28`, also glued
  to a time, `28.10.202618:42`) that is not in the future and not before 2000;
- the **currency**: CHF or EUR when the receipt names one (`CHF`, `Fr.`, `SFr.`, `EUR`, `€`), else the default;
- the **merchant**: the first row of words, before the total, that is not a date, an amount, a phone number
  or a line of the receipt's own bookkeeping ("Kassenbon", "Ticket", "Ricevuta", …);
- the **lines**: the rows between the merchant and the total that end in an amount, when they add up to the
  total; otherwise none, so a misread line never produces a wrong split.

Everything is a guess the user checks in the form; nothing here is stored as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from budget_app.domain.coicop import fold


@dataclass(frozen=True)
class TextBox:
    """A piece of text the engine read, with the top-left corner and the height of its box, in pixels."""

    text: str
    x: float = 0.0
    y: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class ReceiptLine:
    name: str
    amount: float


@dataclass
class Receipt:
    merchant: str = ""
    date: date | None = None
    total: float | None = None
    currency: str = ""
    lines: list[ReceiptLine] = field(default_factory=list)
    rows: list[str] = field(default_factory=list)  # the text, one row of the receipt per entry

    @property
    def empty(self) -> bool:
        return not (self.merchant or self.date or self.total)


# an amount: 11.45, 1'234.50, 1 234,50, 11.45-, -11.45; no thousands groups without a decimal part
_AMOUNT = re.compile(r"(?<![\d.,])(-?)(\d{1,3}(?:['’ ]\d{3})+|\d+)[.,](\d{2})(?!\d|[.,]\d)(-?)")
_DATE_LONG = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](20\d{2})")
_DATE_SHORT = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})")
_PHONE = re.compile(r"(?:\+41|0\d{2})[\s./-]?\d{3}[\s./-]?\d{2}[\s./-]?\d{2}")
_QUANTITY = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*(?:x|×|st|stk|pce|pcs|pz|kg|g|l)?\s+", re.IGNORECASE)
_LETTERS = re.compile(r"[^\W\d_]")

TOTAL_WORDS = (
    "total",  # en, de, fr; also "totale" (it) through the prefix match
    "summe",
    "gesamt",
    "endbetrag",
    "zu zahlen",
    "zu bezahlen",
    "betrag",
    "montant",
    "a payer",
    "importo",
    "da pagare",
    "amount due",
    "balance due",
)
# rows that carry an amount but are not the total, even when they say "total"
NOT_TOTAL_WORDS = (
    "subtotal",
    "sub-total",
    "sous-total",
    "sous total",
    "subtotale",
    "zwischensumme",
    "zwischentotal",
    "mwst",
    "mehrwertsteuer",
    "tva",
    "iva",
    "vat",
    "steuer",
    "ruckgeld",
    "retour",
    "rendu",
    "resto",
    "change",
    "bar",
    "bezahlt",
    "gegeben",
    "paye",
    "pagato",
    "twint",
    "karte",
    "carte",
    "carta",
    "card",
    "maestro",
    "visa",
    "mastercard",
    "rabatt",
    "remise",
    "sconto",
    "discount",
    "punkte",
    "points",
    "punti",
    "saldo",
    "solde",
)
DISCOUNT_WORDS = ("rabatt", "remise", "sconto", "discount")  # not the total, but a (negative) line of the receipt
NOT_LINE_WORDS = tuple(w for w in NOT_TOTAL_WORDS if w not in DISCOUNT_WORDS)
# rows that name the receipt itself or its circumstances, never the merchant
NOT_MERCHANT_WORDS = (
    "kassenbon",
    "kassabon",
    "quittung",
    "beleg",
    "bon",
    "rechnung",
    "receipt",
    "ticket",
    "recu",
    "facture",
    "ricevuta",
    "scontrino",
    "fattura",
    "tel",
    "fax",
    "www",
    "http",
    "@",
    "mwst",
    "tva",
    "iva",
    "vat",
    "uid",
    "che-",
    "kasse",
    "caisse",
    "cassa",
    "filiale",
    "datum",
    "date",
    "data",
    "danke",
    "merci",
    "grazie",
    "thank",
)
CURRENCIES = {"chf": "CHF", "fr.": "CHF", "sfr": "CHF", "eur": "EUR", "€": "EUR"}


# ------------------------------------------------------------------ rows
def rows(boxes: list[TextBox]) -> list[str]:
    """The texts of the boxes, row by row: a box whose vertical centre lies within half a box height of the row's
    first box belongs to that row; the boxes of a row are joined left to right with two spaces."""
    ordered = sorted((b for b in boxes if b.text.strip()), key=lambda b: (b.y + b.height / 2, b.x))
    out: list[list[TextBox]] = []
    for box in ordered:
        if out:
            first = out[-1][0]  # the row's topmost box anchors it, so a row never drifts down box by box
            height = max(first.height, box.height, 1.0)
            if abs((box.y + box.height / 2) - (first.y + first.height / 2)) <= height / 2:
                out[-1].append(box)
                continue
        out.append([box])
    return ["  ".join(b.text.strip() for b in sorted(row, key=lambda b: b.x)) for row in out]


# ------------------------------------------------------------------ pieces of a row
def amounts(text: str) -> list[float]:
    """Every amount in the text, in order; a trailing or leading minus makes it negative."""
    out = []
    for sign, whole, cents, trailing in _AMOUNT.findall(text):
        value = float(re.sub(r"['’ ]", "", whole) + "." + cents)
        out.append(-value if sign or trailing else value)
    return out


def _valid(y: int, m: int, d: int, today: date) -> date | None:
    try:
        found = date(y, m, d)
    except ValueError:
        return None
    return found if date(2000, 1, 1) <= found <= today else None


def find_date(text: str, today: date) -> date | None:
    """The first plausible date in the text: day first (Swiss order), or ISO."""
    for pattern in (_DATE_LONG, _DATE_ISO, _DATE_SHORT):
        for m in pattern.finditer(text):
            a, b, c = (int(x) for x in m.groups())
            found = (
                _valid(a, b, c, today) if pattern is _DATE_ISO else _valid(c + (2000 if c < 100 else 0), b, a, today)
            )
            if found is not None:
                return found
    return None


def find_currency(text: str) -> str:
    folded = fold(text)
    for word, code in CURRENCIES.items():
        if re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", folded):
            return code
    return ""


def _has(folded: str, words: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(w), folded) for w in words)


def _letters(text: str) -> int:
    return len(_LETTERS.findall(text))


def _digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def _is_merchant_row(row: str, today: date) -> bool:
    if _letters(row) < 3 or _digits(row) > 2 or amounts(row) or find_date(row, today) or _PHONE.search(row):
        return False
    return not _has(fold(row), NOT_MERCHANT_WORDS)


def _line(row: str) -> ReceiptLine | None:
    """A product line: words, then its amount at the end of the row (a quantity in front is dropped)."""
    found = list(_AMOUNT.finditer(row))
    if not found or found[-1].end() < len(row.rstrip()) - 1:
        return None
    name = row[: found[-1].start()].strip(" .:-")
    name = _QUANTITY.sub("", name).strip(" .:-")
    if _letters(name) < 2 or _has(fold(name), NOT_LINE_WORDS):
        return None
    return ReceiptLine(name, amounts(row)[-1])


# ------------------------------------------------------------------ the receipt
def parse(text_rows: list[str], today: date | None = None, default_currency: str = "CHF") -> Receipt:
    today = today or date.today()
    text_rows = [r.strip() for r in text_rows if r.strip()]
    receipt = Receipt(rows=text_rows, currency=default_currency)
    total_index = -1
    for i, row in enumerate(text_rows):
        folded = fold(row)
        if amounts(row) and _has(folded, TOTAL_WORDS) and not _has(folded, NOT_TOTAL_WORDS):
            total_index = i
    if total_index >= 0:
        receipt.total = amounts(text_rows[total_index])[-1]
    else:
        every = [a for row in text_rows for a in amounts(row) if a > 0]
        receipt.total = max(every) if every else None
    all_text = "\n".join(text_rows)
    receipt.date = find_date(all_text, today)
    receipt.currency = find_currency(all_text) or default_currency
    merchant_index = next(
        (
            i
            for i, row in enumerate(text_rows[: total_index if total_index >= 0 else None])
            if _is_merchant_row(row, today)
        ),
        -1,
    )
    if merchant_index >= 0:
        receipt.merchant = text_rows[merchant_index]
    if total_index > merchant_index:
        lines = [line for row in text_rows[merchant_index + 1 : total_index] if (line := _line(row)) is not None]
        if lines and receipt.total is not None and abs(sum(line.amount for line in lines) - receipt.total) < 0.005:
            receipt.lines = lines
    return receipt
