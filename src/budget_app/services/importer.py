"""Importing transactions from bank and card CSV exports.

Flow: `read_file` detects encoding, delimiter and header line; `guess_profile` maps the columns;
`parse_rows` turns lines into signed amounts; `analyse` marks duplicates and suggests categories;
`commit` saves the chosen rows. Column settings can be saved per bank as a profile. A running-balance
column, when the profile names one, is read into each row; `import_balances` turns it into month-end balances.
camt.053 / camt.054 statements (`camt.py`) produce the same rows and go through `analyse` and `commit` too.

Like every transaction, imported rows are valued at the ECB rate of their booking date.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from budget_app.domain.coicop import TAX, get_coicop
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Transaction
from budget_app.i18n import t
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput

PROFILES_FILE = "import_profiles.json"
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")  # Swiss bank exports are often Windows-1252
DELIMITERS = ";,\t|"
DATE_FORMATS = {
    "%d.%m.%Y": "31.12.2026",
    "%d.%m.%y": "31.12.26",
    "%Y-%m-%d": "2026-12-31",
    "%d/%m/%Y": "31/12/2026",
    "%m/%d/%Y": "12/31/2026",
    "%d-%m-%Y": "31-12-2026",
    "%Y%m%d": "20261231",
}
MAX_BYTES = 20 * 1024 * 1024

# Header names seen in Swiss, German, French and English bank exports (compared in lower case)
_DATE_NAMES = ("buchungsdatum", "datum", "date", "booking date", "transaction date", "abschlussdatum", "date comptable")
_VALUE_DATE_NAMES = ("valuta", "valutadatum", "value date", "date valeur")
_AMOUNT_NAMES = ("betrag", "amount", "montant", "einzelbetrag", "transaktionsbetrag")
_DEBIT_NAMES = ("belastung", "debit", "lastschrift", "soll", "ausgang", "débit", "withdrawal")
_CREDIT_NAMES = ("gutschrift", "credit", "haben", "eingang", "crédit", "deposit")
_BALANCE_NAMES = ("saldo", "balance", "solde", "kontostand", "saldo contabile")
_TEXT_NAMES = (
    "buchungstext",
    "beschreibung",
    "description",
    "text",
    "details",
    "verwendungszweck",
    "mitteilung",
    "empfänger",
    "zahlungsempfänger",
    "payee",
    "merchant",
    "libellé",
    "beschreibung1",
    "beschreibung2",
    "beschreibung3",
)


class ImportFileError(ValidationError):
    """The file cannot be read as a transaction list."""


@dataclass
class ImportProfile:
    """How to read one bank's export. Saved per bank so the next import needs no mapping."""

    name: str = ""
    delimiter: str = ";"
    encoding: str = "utf-8-sig"
    header_row: int = 0  # index of the header line in the file
    date_col: str = ""
    date_format: str = "%d.%m.%Y"
    amount_col: str = ""  # one signed amount column ...
    debit_col: str = ""  # ... or separate debit and credit columns
    credit_col: str = ""
    decimal: str = "."
    text_cols: list[str] = field(default_factory=list)
    invert: bool = False  # card statements: positive amounts are spending
    balance_col: str = ""  # the running balance after each booking (optional, F36)
    account_id: str = ""
    signature: str = ""  # the header line, to recognise the bank's files again


@dataclass
class ParsedFile:
    name: str
    text: str  # the decoded file
    encoding: str
    delimiter: str
    header_row: int
    lines: list[list[str]]  # every line of the file, split

    @property
    def headers(self) -> list[str]:
        return _unique_headers(self.lines[self.header_row]) if self.lines else []

    @property
    def data(self) -> list[list[str]]:
        return [r for r in self.lines[self.header_row + 1 :] if any(c.strip() for c in r)]

    @property
    def signature(self) -> str:
        return "|".join(h.strip().lower() for h in self.headers)


NEW, DUPLICATE, POSSIBLE_DUPLICATE, ERROR = "new", "duplicate", "possible_duplicate", "error"


@dataclass
class ImportRow:
    index: int  # position in the file's data rows
    date: date | None
    amount: float | None  # signed: negative = money leaving the account
    text: str
    error: str = ""
    status: str = NEW
    include: bool = True
    category: str = ""
    suggested: bool = False  # category taken from an earlier transaction with the same text
    import_ref: str = ""
    balance: float | None = None  # the account's balance after this booking, when the file shows it

    @property
    def type(self) -> str:
        return TX_EXPENDITURE if (self.amount or 0) < 0 else TX_INCOME


# ------------------------------------------------------------------ reading
def read_file(name: str, data: bytes) -> ParsedFile:
    if len(data) > MAX_BYTES:
        raise ImportFileError(t("The file is larger than 20 MB"))
    text, encoding = _decode(data)
    sample = "\n".join(text.splitlines()[:50])
    delimiter = max(DELIMITERS, key=lambda d: _consistency(sample, d))
    lines = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not any(any(c.strip() for c in r) for r in lines):
        raise ImportFileError(t("The file is empty"))
    return ParsedFile(name, text, encoding, delimiter, _header_row(lines), lines)


def reparse(parsed: ParsedFile, delimiter: str, header_row: int) -> ParsedFile:
    """Apply a delimiter or header line chosen by the user."""
    lines = list(csv.reader(io.StringIO(parsed.text), delimiter=delimiter))
    header_row = min(max(header_row, 0), max(len(lines) - 1, 0))
    return ParsedFile(parsed.name, parsed.text, parsed.encoding, delimiter, header_row, lines)


def _decode(data: bytes) -> tuple[str, str]:
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ImportFileError(t("The file's text encoding is not supported"))


def _consistency(sample: str, delimiter: str) -> float:
    """Score how well a delimiter splits the sample into equally wide rows."""
    rows = [r for r in csv.reader(io.StringIO(sample), delimiter=delimiter) if any(c.strip() for c in r)]
    widths = Counter(len(r) for r in rows)
    if not widths:
        return 0
    width, count = widths.most_common(1)[0]
    return 0 if width < 2 else count * width


def _header_row(lines: list[list[str]]) -> int:
    """The first line as wide as the data that is mostly text (bank exports often start with a preamble)."""
    widths = Counter(len(r) for r in lines if any(c.strip() for c in r))
    width = widths.most_common(1)[0][0]
    for i, row in enumerate(lines[:40]):
        cells = [c.strip() for c in row]
        if len(row) >= width and sum(1 for c in cells if c and not _looks_numeric(c)) >= max(2, width // 2):
            return i
    return 0


def _looks_numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[-+()'.,\d\s/:]+", text))


def _unique_headers(row: list[str]) -> list[str]:
    seen: Counter[str] = Counter()
    out = []
    for i, cell in enumerate(row):
        name = cell.strip() or f"Column {i + 1}"  # not translated: saved profiles refer to column names
        seen[name] += 1
        out.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return out


# ------------------------------------------------------------------ mapping
def guess_profile(parsed: ParsedFile, saved: list[ImportProfile] | None = None) -> ImportProfile:
    """A saved profile for files with the same header, or a best guess from the column names."""
    for p in saved or []:
        if p.signature and p.signature == parsed.signature:
            return dataclasses.replace(p, encoding=parsed.encoding)
    headers = parsed.headers
    lower = {h: h.strip().lower() for h in headers}

    def find(names: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str:
        for name in names:
            for h, low in lower.items():
                if low == name and low not in exclude:
                    return h
        for name in names:
            for h, low in lower.items():
                if name in low and not any(x in low for x in exclude):
                    return h
        return ""

    profile = ImportProfile(
        delimiter=parsed.delimiter,
        encoding=parsed.encoding,
        header_row=parsed.header_row,
        signature=parsed.signature,
        date_col=find(_DATE_NAMES, exclude=_VALUE_DATE_NAMES) or find(_VALUE_DATE_NAMES),
        amount_col=find(_AMOUNT_NAMES),
        debit_col=find(_DEBIT_NAMES),
        credit_col=find(_CREDIT_NAMES),
        balance_col=find(_BALANCE_NAMES),
    )
    if profile.debit_col and profile.credit_col:
        profile.amount_col = ""
    profile.text_cols = [h for h in headers if any(n == lower[h] or n in lower[h] for n in _TEXT_NAMES)][:3]
    column = _column(parsed, profile.date_col)
    profile.date_format = detect_date_format(column) or profile.date_format
    amounts = (
        _column(parsed, profile.amount_col) + _column(parsed, profile.debit_col) + _column(parsed, profile.credit_col)
    )
    profile.decimal = detect_decimal(amounts)
    return profile


def _column(parsed: ParsedFile, name: str) -> list[str]:
    if not name or name not in parsed.headers:
        return []
    i = parsed.headers.index(name)
    return [r[i] for r in parsed.data if i < len(r) and r[i].strip()]


def detect_date_format(values: list[str]) -> str | None:
    values = [_date_part(v) for v in values if v.strip()][:200]
    if not values:
        return None
    for fmt in DATE_FORMATS:
        if all(_parse_date(v, fmt) for v in values):
            return fmt
    return None


def detect_decimal(values: list[str]) -> str:
    """',' when amounts look like 1.234,50 or 12,50; '.' otherwise."""
    comma = sum(1 for v in values if re.search(r",\d{1,2}\s*-?$", v.strip()))
    dot = sum(1 for v in values if re.search(r"\.\d{1,2}\s*-?$", v.strip()))
    return "," if comma > dot else "."


def parse_amount(text: str, decimal: str = ".") -> float | None:
    """Parse 1'234.50, 1.234,50, -12.30, 12.30-, (12.30), CHF 12.30 ... ; None when empty."""
    s = text.strip()
    if not s:
        return None
    negative = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        raise ValueError(text)
    s = s.replace(".", "").replace(",", ".") if decimal == "," else s.replace(",", "")
    value = float(s)
    return -value if negative else value


def _date_part(text: str) -> str:
    return text.strip().split(" ")[0].split("T")[0]


def _parse_date(text: str, fmt: str) -> date | None:
    try:
        return datetime.strptime(_date_part(text), fmt).date()
    except ValueError:
        return None


# ------------------------------------------------------------------ rows
def parse_rows(parsed: ParsedFile, profile: ImportProfile) -> list[ImportRow]:
    headers = parsed.headers
    idx = {h: i for i, h in enumerate(headers)}

    def cell(row: list[str], name: str) -> str:
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    out = []
    for n, raw in enumerate(parsed.data):
        text = " · ".join(v for v in (cell(raw, c) for c in profile.text_cols) if v)
        row = ImportRow(index=n, date=None, amount=None, text=text)
        try:
            row.date = _parse_date(cell(raw, profile.date_col), profile.date_format)
            if row.date is None:
                raise ValueError("date")
            if profile.amount_col:
                amount = parse_amount(cell(raw, profile.amount_col), profile.decimal)
            else:
                debit = parse_amount(cell(raw, profile.debit_col), profile.decimal) or 0.0
                credit = parse_amount(cell(raw, profile.credit_col), profile.decimal) or 0.0
                amount = abs(credit) - abs(debit)
            if amount is None or abs(amount) < 0.005:
                raise ValueError("amount")
            row.amount = round(-amount if profile.invert else amount, 2)
            row.balance = _balance(cell(raw, profile.balance_col), profile) if profile.balance_col else None
        except ValueError as exc:
            row.error = t("No valid date") if str(exc) == "date" else t("No valid amount")
            row.status, row.include = ERROR, False
        out.append(row)
    return out


def _balance(text: str, profile: ImportProfile) -> float | None:
    """The balance cell as a signed amount (None when empty or unreadable: the balance is optional)."""
    try:
        value = parse_amount(text, profile.decimal)
    except ValueError:
        return None
    if value is None:
        return None
    return round(-value if profile.invert else value, 2)


def _norm(text: str, keep_digits: bool = True) -> str:
    s = text.lower()
    if not keep_digits:
        s = re.sub(r"\d", " ", s)
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def analyse(ctx: Context, rows: list[ImportRow], account_id: str) -> list[ImportRow]:
    """Set import references, mark duplicates and suggest categories from earlier transactions.

    Rows from a camt statement arrive with the bank's reference (`camt:…`) and keep it; CSV rows get a
    fingerprint. A row matching a transaction entered by hand, or imported from the other kind of file,
    on the same day with the same type and amount is a possible duplicate.
    """
    existing = [tx for tx in ctx.store.transactions.all() if account_id in (tx.from_account, tx.to_account)]
    refs = {tx.import_ref for tx in existing if tx.import_ref}
    by_day: dict[str, Counter[tuple]] = defaultdict(Counter)  # source ("" = typed by hand) -> (day, type, amount)
    for tx in existing:
        by_day[_source(tx.import_ref)][(tx.date, tx.type, round(tx.amount, 2))] += 1
    history = _category_history(ctx)
    seen: Counter[str] = Counter()
    for row in rows:
        if row.status == ERROR or row.date is None or row.amount is None:
            continue
        if not row.import_ref.startswith("camt:"):
            key = f"{account_id}|{row.date.isoformat()}|{row.amount:.2f}|{_norm(row.text)}"
            seen[key] += 1  # identical rows (two coffees on one day) stay distinct
            row.import_ref = "csv:" + hashlib.sha256(f"{key}#{seen[key]}".encode()).hexdigest()[:20]
        day = (row.date, row.type, round(abs(row.amount), 2))
        # one earlier entry matches one row
        other = next((c for s, c in by_day.items() if s != _source(row.import_ref) and c[day] > 0), None)
        if row.import_ref in refs:
            row.status, row.include = DUPLICATE, False
        elif other is not None:
            other[day] -= 1
            row.status, row.include = POSSIBLE_DUPLICATE, False
        else:
            row.status, row.include = NEW, True
        if not row.category:
            suggestion = history.get((row.type, _norm(row.text, keep_digits=False)))
            if suggestion:
                row.category, row.suggested = suggestion, True
    return rows


def _source(import_ref: str) -> str:
    """'csv', 'camt', or '' for a transaction typed by hand."""
    return import_ref.split(":", 1)[0] if import_ref else ""


def _category_history(ctx: Context) -> dict[tuple[str, str], str]:
    """Most frequent category per (type, text without digits) among existing transactions."""
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for tx in ctx.store.transactions.all():
        if tx.type in (TX_EXPENDITURE, TX_INCOME) and tx.category and tx.comment:
            counts[(tx.type, _norm(tx.comment, keep_digits=False))][tx.category] += 1
    return {k: c.most_common(1)[0][0] for k, c in counts.items() if k[1]}


def valid_category(ctx: Context, type_: str, category: str) -> bool:
    if type_ == TX_EXPENDITURE:
        return category == TAX or get_coicop().get(category) is not None
    return ctx.store.income_categories.get(category) is not None


def commit(ctx: Context, rows: list[ImportRow], account_id: str) -> list[Transaction]:
    """Save every included row as a transaction in one write. Nothing is saved if a row is invalid."""
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        raise ValidationError(t("Choose the account the file belongs to"))
    inputs = []
    for row in rows:
        if not row.include:
            continue
        if row.date is None or row.amount is None:
            raise ValidationError(t("Row {n}: {error}", n=row.index + 1, error=row.error or t("incomplete")))
        if not valid_category(ctx, row.type, row.category):
            raise ValidationError(t("Row {n}: choose a category", n=row.index + 1))
        inp = TransactionInput(
            date=row.date,
            type=row.type,
            amount=abs(row.amount),
            currency=acc.currency,
            category=row.category,
            comment=row.text,
            import_ref=row.import_ref,
        )
        if row.type == TX_EXPENDITURE:
            inp.from_account = account_id
        else:
            inp.to_account = account_id
        inputs.append(inp)
    return tx_svc.save_many(ctx, inputs)


# ------------------------------------------------------------------ profiles
def load_profiles(data_dir: Path) -> list[ImportProfile]:
    path = data_dir / PROFILES_FILE
    if not path.exists():
        return []
    known = {f.name for f in dataclasses.fields(ImportProfile)}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ImportProfile(**{k: v for k, v in p.items() if k in known}) for p in raw]


def save_profile(data_dir: Path, profile: ImportProfile) -> None:
    """Add or replace (by name) a profile."""
    profiles = [p for p in load_profiles(data_dir) if p.name != profile.name] + [profile]
    path = data_dir / PROFILES_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([dataclasses.asdict(p) for p in profiles], indent=2), encoding="utf-8")
    tmp.replace(path)
