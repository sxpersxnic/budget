"""Importing Swiss bank statements in ISO 20022 XML: camt.053 (account statement) and camt.054
(debit/credit notification), any version (.001.02, .001.04, .001.08 ...).

`read_statement` turns the file into the same `ImportRow`s the CSV import produces, so
`importer.analyse` (duplicates, category suggestions) and `importer.commit` handle them unchanged.
No column mapping is needed: dates, signed amounts, currency and the bank's reference are structured.

- One row per booked entry (`Ntry`); pending entries (`Sts` other than BOOK) are left out.
- A batch entry whose transaction details (`TxDtls`) each carry an amount that adds up to the entry
  becomes one row per detail.
- `import_ref` is `camt:<IBAN>:<bank reference>` (`AcctSvcrRef`, else `NtryRef`, else a content
  hash), so re-importing the same or an overlapping statement skips exact duplicates.
- The payee (F09) is read from the counterparty's name (`RltdPties`), else from the entry's text.
- The account chosen for a statement's IBAN is remembered in `import_accounts.json`.

The XML is parsed with the standard library after a pre-scan that refuses any document type
declaration (no entities, so no entity expansion attacks), and files are capped at `MAX_BYTES`.
"""

from __future__ import annotations

import copy
import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import NoReturn
from xml.parsers import expat

from budget_app.domain.models import Transaction
from budget_app.i18n import N_, t
from budget_app.services import importer
from budget_app.services.context import Context, ValidationError
from budget_app.services.importer import ERROR, MAX_BYTES, ImportFileError, ImportRow
from budget_app.services.undo import recorded
from budget_app.storage.store import Store

ACCOUNTS_FILE = "import_accounts.json"  # statement IBAN -> account id
_MESSAGE = re.compile(r"camt\.\d{3}(?:\.\d{3}\.\d{2})?")
_CONTAINERS = {"BkToCstmrStmt": ("Stmt", "camt.053"), "BkToCstmrDbtCdtNtfctn": ("Ntfctn", "camt.054")}


@dataclass
class Balance:
    amount: float  # signed: negative = overdrawn
    currency: str
    date: date | None


@dataclass
class Statement:
    """A camt file: the account it belongs to, its period and its booked entries as import rows."""

    name: str
    message: str  # e.g. "camt.053.001.04"
    iban: str
    currency: str
    start: date | None
    end: date | None
    rows: list[ImportRow] = field(default_factory=list)
    pending: int = 0  # entries left out because they are not booked yet
    closing_balance: Balance | None = None  # kept for F36 (reconcile against recorded balances)

    def fresh_rows(self) -> list[ImportRow]:
        """Copies of the rows, for one review pass (`analyse` changes status, include and category)."""
        return copy.deepcopy(self.rows)


def looks_like_xml(data: bytes) -> bool:
    return data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<")


def format_iban(iban: str) -> str:
    """CH9300762011623852957 -> CH93 0076 2011 6238 5295 7"""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


# ------------------------------------------------------------------ reading
def read_statement(name: str, data: bytes) -> Statement:
    if len(data) > MAX_BYTES:
        raise ImportFileError(t("The file is larger than 20 MB"))
    _refuse_doctype(data)
    root = ET.fromstring(data)  # noqa: S314 - well-formed and without DTD (checked above), so no entities
    namespace = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    for el in root.iter():
        el.tag = el.tag.rsplit("}", 1)[-1]
    container = root if root.tag in _CONTAINERS else next((c for c in root if c.tag in _CONTAINERS), None)
    if container is None:
        raise ImportFileError(t("The XML file is not a camt.053 or camt.054 bank statement"))
    part, kind = _CONTAINERS[container.tag]
    found = _MESSAGE.search(namespace)
    message = found.group(0) if found and found.group(0).startswith(kind) else kind
    statements = container.findall(part)

    ibans = list(dict.fromkeys(_iban(st) for st in statements if _iban(st)))
    if len(ibans) > 1:
        raise ImportFileError(
            t(
                "The file holds statements for several accounts ({ibans}); export one account per file",
                ibans=", ".join(format_iban(i) for i in ibans),
            )
        )
    iban = ibans[0] if ibans else ""
    currency = _currency(statements)
    out = Statement(name=name, message=message, iban=iban, currency=currency, start=None, end=None)
    seen: Counter[str] = Counter()
    starts, ends = [], []
    for st in statements:
        starts.append(_date(st.findtext("FrToDt/FrDtTm")))
        ends.append(_date(st.findtext("FrToDt/ToDtTm")))
        for bal in st.findall("Bal"):
            balance = _balance(bal)
            if balance and (out.closing_balance is None or _later(balance, out.closing_balance)):
                out.closing_balance = balance
        for ntry in st.findall("Ntry"):
            status = _text(ntry, "Sts/Cd") or _text(ntry, "Sts")
            if status and status != "BOOK":
                out.pending += 1
                continue
            for row in _entry_rows(ntry, currency):
                seen[row.import_ref] += 1  # two identical entries without a reference stay distinct
                ref = row.import_ref if seen[row.import_ref] == 1 else f"{row.import_ref}#{seen[row.import_ref]}"
                row.import_ref = f"camt:{iban}:{ref}"
                row.index = len(out.rows)
                out.rows.append(row)
    if not out.rows:
        raise ImportFileError(t("The statement has no booked entries"))
    dates = [r.date for r in out.rows if r.date]
    out.start = min((d for d in starts if d), default=min(dates, default=None))
    out.end = max((d for d in ends if d), default=max(dates, default=None))
    return out


def _refuse_doctype(data: bytes) -> None:
    """Check the XML is well-formed and has no document type (and so no entity) declarations."""

    def refuse(*_: object) -> NoReturn:
        raise ImportFileError(t("The XML file declares a document type, which bank statements never do"))

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ImportFileError(t("The file is not valid XML (line {line})", line=exc.lineno)) from exc


def _text(el: ET.Element, path: str) -> str:
    return " ".join((el.findtext(path) or "").split())


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError:
        return None


def _amount(el: ET.Element | None) -> tuple[float | None, str]:
    if el is None:
        return None, ""
    try:
        value = round(float((el.text or "").strip()), 2)
    except ValueError:
        value = None
    return value, (el.get("Ccy") or "").strip().upper()


def _iban(st: ET.Element) -> str:
    return re.sub(r"\s", "", _text(st, "Acct/Id/IBAN") or _text(st, "Acct/Id/Othr/Id")).upper()


def _currency(statements: list[ET.Element]) -> str:
    """The account currency, else the balances' or the entries' currency."""
    for path in ("Acct/Ccy", "Bal/Amt", "Ntry/Amt"):
        for st in statements:
            el = st.find(path)
            if el is not None:
                code = (el.get("Ccy") if path != "Acct/Ccy" else el.text) or ""
                if code.strip():
                    return code.strip().upper()
    return ""


def _balance(bal: ET.Element) -> Balance | None:
    if (_text(bal, "Tp/CdOrPrtry/Cd") or _text(bal, "Tp/CdOrPrtry/Prtry")) != "CLBD":
        return None
    amount, currency = _amount(bal.find("Amt"))
    if amount is None:
        return None
    sign = -1 if _text(bal, "CdtDbtInd") == "DBIT" else 1
    return Balance(sign * abs(amount), currency, _date(_text(bal, "Dt/Dt") or _text(bal, "Dt/DtTm")))


def _later(a: Balance, b: Balance) -> bool:
    return (a.date or date.min) >= (b.date or date.min)


def _sign(indicator: str) -> int | None:
    return {"CRDT": 1, "DBIT": -1}.get(indicator)


def _counterparty(tx: ET.Element, sign: int | None) -> str:
    """Who was paid (money out) or who paid (money in)."""
    role = "Cdtr" if sign == -1 else "Dbtr"
    for path in (f"RltdPties/{role}/Pty/Nm", f"RltdPties/{role}/Nm", f"RltdPties/Ultmt{role}/Nm"):
        if name := _text(tx, path):
            return name
    return ""


def _join(*parts: str) -> str:
    """Join the non-empty parts with ' · ', leaving out a part already contained in the text."""
    out: list[str] = []
    for part in parts:
        if part and not any(part.lower() in o.lower() for o in out):
            out.append(part)
    return " · ".join(out)


def _entry_rows(ntry: ET.Element, currency: str) -> list[ImportRow]:
    booked = _date(_text(ntry, "BookgDt/Dt") or _text(ntry, "BookgDt/DtTm")) or _date(
        _text(ntry, "ValDt/Dt") or _text(ntry, "ValDt/DtTm")
    )
    amount, ccy = _amount(ntry.find("Amt"))
    sign = _sign(_text(ntry, "CdtDbtInd"))
    info = _text(ntry, "AddtlNtryInf")
    ref = _text(ntry, "AcctSvcrRef") or _text(ntry, "NtryRef")
    details = ntry.findall("NtryDtls/TxDtls")

    parts = _batch(details, amount, ccy, sign)
    if parts is None:  # one row for the entry, described by its only (or first) transaction detail
        tx = details[0] if details else None
        text = _join(
            _counterparty(tx, sign) if tx is not None else "",
            _remittance(tx) if tx is not None else "",
            info,
        )
        row = _row(booked, amount, sign, ccy, currency, text)
        row.counterparty = _counterparty(tx, sign) if tx is not None else ""
        row.import_ref = ref or _hash(row, ccy)
        return [row]
    rows = []
    for i, (tx, tx_amount, tx_sign) in enumerate(parts, start=1):
        text = _join(_counterparty(tx, tx_sign), _remittance(tx)) or _join(_text(tx, "AddtlTxInf"), info)
        row = _row(booked, tx_amount, tx_sign, ccy, currency, text)
        row.counterparty = _counterparty(tx, tx_sign)
        part_ref = _text(tx, "Refs/AcctSvcrRef") or str(i)
        row.import_ref = f"{ref}/{part_ref}" if ref else _hash(row, ccy)
        rows.append(row)
    return rows


def _batch(
    details: list[ET.Element], amount: float | None, ccy: str, sign: int | None
) -> list[tuple[ET.Element, float, int]] | None:
    """The batch's parts when every detail has an amount and they add up to the entry; else None."""
    if len(details) < 2 or amount is None or sign is None:
        return None
    parts = []
    for tx in details:
        el = tx.find("Amt")
        tx_amount, tx_ccy = _amount(el if el is not None else tx.find("AmtDtls/TxAmt/Amt"))
        tx_sign = _sign(_text(tx, "CdtDbtInd")) or sign
        if tx_amount is None or tx_ccy != ccy:
            return None
        parts.append((tx, tx_amount, tx_sign))
    if abs(sum(a * s for _, a, s in parts) - amount * sign) >= 0.005:
        return None
    return parts


def _remittance(tx: ET.Element) -> str:
    return " ".join(" ".join((u.text or "").split()) for u in tx.findall("RmtInf/Ustrd") if (u.text or "").strip())


def _row(booked: date | None, amount: float | None, sign: int | None, ccy: str, currency: str, text: str) -> ImportRow:
    row = ImportRow(index=0, date=booked, amount=None, text=text)
    if booked is None:
        row.error = t("No valid date")
    elif amount is None or sign is None or amount < 0.005:
        row.error = t("No valid amount")
    else:
        row.amount = round(sign * amount, 2)
        if currency and ccy and ccy != currency:
            row.error = t("Amount in {currency}, not in the statement's currency", currency=ccy)
    if row.error:
        row.status, row.include = ERROR, False
    return row


def _hash(row: ImportRow, ccy: str) -> str:
    """A reference from the content, for entries the bank gave none."""
    key = f"{row.date}|{row.amount}|{ccy}|{importer._norm(row.text)}"
    return "h" + hashlib.sha256(key.encode()).hexdigest()[:20]


# ------------------------------------------------------------------ account
def currency_problem(ctx: Context, statement: Statement, account_id: str) -> str:
    """Why the statement cannot go to that account ('' when it can)."""
    acc = ctx.store.accounts.get(account_id)
    if acc is None or not statement.currency or acc.currency == statement.currency:
        return ""
    return t(
        "The statement is in {currency}, but “{account}” is in {account_currency}. Choose an account in {currency}.",
        currency=statement.currency,
        account=acc.name,
        account_currency=acc.currency,
    )


@recorded(N_("Import transactions"))
def commit(ctx: Context, statement: Statement, rows: list[ImportRow], account_id: str) -> list[Transaction]:
    """Save the chosen rows to the account and remember the account for the statement's IBAN."""
    problem = currency_problem(ctx, statement, account_id)
    if problem:
        raise ValidationError(problem)
    saved = importer.commit(ctx, rows, account_id)
    if statement.iban:
        remember_account(ctx.store, statement.iban, account_id)
    return saved


def _load_accounts(store: Store) -> dict[str, str]:
    raw = store.read_json(ACCOUNTS_FILE)
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def remembered_account(store: Store, iban: str) -> str:
    """The account last used for statements of this IBAN ('' if none)."""
    return _load_accounts(store).get(iban, "") if iban else ""


def remember_account(store: Store, iban: str, account_id: str) -> None:
    store.write_json(ACCOUNTS_FILE, _load_accounts(store) | {iban: account_id})
