"""Swiss QR bills (F03), pure: the text of a QR bill's code ("SPC" …), read and checked.

The payload follows the *Swiss Implementation Guidelines QR-bill* of SIX, version 2.3 (payload version
`0200`, the same since version 2.0): one element per line, separated by CR LF or LF, in this order

    QRType SPC · Version 0200 · Coding 1 · IBAN · creditor (address type, name, street or line 1,
    building number or line 2, postal code, town, country) · ultimate creditor (7 empty lines, reserved)
    · amount · currency · debtor (7 lines, all empty when there is none) · reference type · reference ·
    unstructured message · trailer EPD · bill information · up to two alternative procedures

The last three are optional and may be left out. `parse` checks what a bank checks before it pays:

- the IBAN: CH or LI, 21 characters, its mod-97 check digits; a **QR-IBAN** (institution id 30000 to
  31999) goes with a QR reference and only with one;
- the **QR reference** (QRR): 27 digits, the last one the recursive mod-10 check digit of the others;
- the **creditor reference** (SCOR, ISO 11649): `RF`, two mod-97 check digits and up to 21 letters and
  digits; NON has no reference;
- the currency, CHF or EUR; the amount, digits with a decimal point (`1949.75`), at most 999'999'999.99
  and at most 12 characters. An empty amount means the payer fills it in (`amount` is None); `0.00`
  is a notice that is not to be paid (`notice`);
- the addresses: structured (S: street and building number, postal code and town apart) or combined
  (K: two address lines; no longer issued since November 2025, but still found on older bills), each
  with a name, a two-letter country code and the field lengths of the guidelines;
- message and bill information at most 140 characters together, the total at most 997 characters.

Reading is lenient where the guidelines leave no doubt about the meaning: spaces in the IBAN or the
reference are dropped, a final line break is ignored, and an amount may have fewer than two decimals.

`swico` reads the bill information in the Swico syntax (`//S1/10/…/11/…`), from which `due_date`
takes the invoice date plus the net payment term ("0:30" = net within 30 days), when both are given.

Errors are `QrBillError`s whose `message` is an English text marked with `N_`, translated where shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from budget_app.i18n import N_, t

QR_TYPE = "SPC"
MAJOR_VERSION = "02"
CODING_UTF8 = "1"
TRAILER = "EPD"
CURRENCIES = ("CHF", "EUR")
REF_QRR, REF_SCOR, REF_NON = "QRR", "SCOR", "NON"
ADDRESS_STRUCTURED, ADDRESS_COMBINED = "S", "K"
MAX_CHARS = 997
MAX_AMOUNT = Decimal("999999999.99")
QR_IID = range(30000, 32000)  # institution ids of QR-IBANs

_MIN_LINES = 31  # up to and including the trailer EPD
_MAX_LINES = 34  # with bill information and two alternative procedures
_AMOUNT = re.compile(r"^\d{1,9}(\.\d{1,2})?$")
_IBAN = re.compile(r"^(CH|LI)\d{2}\d{5}[0-9A-Z]{12}$")
_QRR = re.compile(r"^\d{27}$")
_SCOR = re.compile(r"^RF\d{2}[0-9A-Z]{1,21}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_MOD10 = (0, 9, 4, 6, 8, 2, 7, 1, 3, 5)

NOT_A_BILL = N_("This QR code is not a Swiss QR bill")


class QrBillError(ValueError):
    """A QR bill that a bank would refuse. `message` is an English text marked with N_, `params` its values."""

    def __init__(self, message: str, **params: str):
        super().__init__(message.format(**params))
        self.message, self.params = message, params

    def translated(self) -> str:
        return t(self.message, **self.params)


@dataclass(frozen=True)
class Address:
    kind: str  # ADDRESS_STRUCTURED or ADDRESS_COMBINED
    name: str
    line1: str = ""  # S: street; K: address line 1 (street and number)
    line2: str = ""  # S: building number; K: address line 2 (postal code and town)
    postal_code: str = ""  # S only
    town: str = ""  # S only
    country: str = ""

    def lines(self) -> list[str]:
        """The address under the name, as printed on a bill: "Rue du Lac 1268", "2501 Biel"."""
        if self.kind == ADDRESS_COMBINED:
            return [x for x in (self.line1, self.line2) if x]
        street = " ".join(x for x in (self.line1, self.line2) if x)
        town = " ".join(x for x in (self.postal_code, self.town) if x)
        return [x for x in (street, town) if x]


@dataclass(frozen=True)
class QrBill:
    iban: str  # without spaces
    creditor: Address
    currency: str
    amount: Decimal | None = None  # None: the payer fills it in
    debtor: Address | None = None
    reference_type: str = REF_NON
    reference: str = ""  # without spaces
    message: str = ""  # the unstructured message
    bill_info: str = ""  # the bill information (often in Swico syntax, `swico`)
    alternatives: tuple[str, ...] = field(default_factory=tuple)  # alternative procedures (eBill …)
    version: str = "0200"

    @property
    def qr_iban(self) -> bool:
        return is_qr_iban(self.iban)

    @property
    def notice(self) -> bool:
        """Amount 0.00: a notice ("do not use for payment"), nothing to pay."""
        return self.amount is not None and self.amount == 0


# ------------------------------------------------------------------ check digits
def iban_valid(iban: str) -> bool:
    """A Swiss or Liechtenstein IBAN with correct mod-97 check digits (ISO 13616)."""
    if not _IBAN.match(iban):
        return False
    return _mod97(iban[4:] + iban[:4]) == 1


def is_qr_iban(iban: str) -> bool:
    """A QR-IBAN: its institution id (positions 5 to 9) lies between 30000 and 31999."""
    return len(iban) >= 9 and iban[4:9].isdigit() and int(iban[4:9]) in QR_IID


def _mod97(text: str) -> int:
    return int("".join(str(int(ch, 36)) for ch in text)) % 97


def mod10_check_digit(digits: str) -> int:
    """The recursive mod-10 check digit of a QR reference's first 26 digits."""
    carry = 0
    for ch in digits:
        carry = _MOD10[(carry + int(ch)) % 10]
    return (10 - carry) % 10


def qr_reference_valid(ref: str) -> bool:
    return bool(_QRR.match(ref)) and mod10_check_digit(ref[:26]) == int(ref[26])


def creditor_reference_valid(ref: str) -> bool:
    """An ISO 11649 creditor reference ("RF18539007547034"): RF, check digits, 1 to 21 letters and digits."""
    return bool(_SCOR.match(ref)) and _mod97(ref[4:] + ref[:4]) == 1


# ------------------------------------------------------------------ parsing
def _lines(text: str) -> list[str]:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return [line.strip() for line in text.split("\n")]


def _address(fields: list[str], debtor: bool) -> Address:
    """The seven lines of a creditor or debtor."""
    kind, name, line1, line2, postal_code, town, country = fields
    invalid = N_("The debtor’s address is invalid") if debtor else N_("The creditor’s address is invalid")
    if kind == ADDRESS_STRUCTURED:
        ok = bool(postal_code and town) and len(line2) <= 16 and len(postal_code) <= 16 and len(town) <= 35
    elif kind == ADDRESS_COMBINED:
        ok = bool(line2) and not (postal_code or town) and len(line2) <= 70
    else:
        raise QrBillError(N_("The address type “{value}” is unknown"), value=kind)
    if not (ok and name and len(name) <= 70 and len(line1) <= 70 and _COUNTRY.match(country)):
        raise QrBillError(invalid)
    return Address(kind, name, line1, line2, postal_code, town, country)


def _amount(text: str) -> Decimal | None:
    if not text:
        return None
    value = Decimal(text) if _AMOUNT.match(text) and len(text) <= 12 else None  # the pattern admits numbers only
    if value is None or value > MAX_AMOUNT:
        raise QrBillError(N_("The amount “{value}” is invalid"), value=text)
    return value.quantize(Decimal("0.01"))


def _reference(kind: str, ref: str, iban: str) -> str:
    ref = ref.replace(" ", "").upper()
    if kind == REF_QRR:
        if not qr_reference_valid(ref):
            raise QrBillError(N_("The QR reference {reference} is invalid"), reference=ref)
        if not is_qr_iban(iban):
            raise QrBillError(N_("A QR reference needs a QR-IBAN"))
    elif kind == REF_SCOR:
        if not creditor_reference_valid(ref):
            raise QrBillError(N_("The creditor reference {reference} is invalid"), reference=ref)
    elif kind == REF_NON:
        if ref:
            raise QrBillError(N_("The reference type NON doesn’t allow a reference"))
    else:
        raise QrBillError(N_("The reference type “{value}” is unknown"), value=kind)
    if kind != REF_QRR and is_qr_iban(iban):
        raise QrBillError(N_("A QR-IBAN needs a QR reference"))
    return ref


def is_payload(text: str) -> bool:
    """Whether a QR code's text claims to be a QR bill (its first line is SPC)."""
    return _lines(text)[0] == QR_TYPE if text else False


def parse(text: str) -> QrBill:
    """Read and check the text of a QR bill's code; raises QrBillError."""
    lines = _lines(text or "")
    if lines[0] != QR_TYPE:
        raise QrBillError(NOT_A_BILL)
    if len(text.lstrip("\ufeff").rstrip("\r\n")) > MAX_CHARS:  # a final line break is tolerated
        raise QrBillError(N_("The QR bill is longer than 997 characters"))
    if len(lines) < 2 or not (len(lines[1]) == 4 and lines[1].isdigit() and lines[1][:2] == MAJOR_VERSION):
        raise QrBillError(N_("QR bills of version {version} can’t be read"), version=lines[1] if len(lines) > 1 else "")
    if len(lines) < _MIN_LINES or len(lines) > _MAX_LINES:
        raise QrBillError(N_("The QR bill has too few or too many lines"))
    if lines[2] != CODING_UTF8:
        raise QrBillError(N_("The QR bill uses an unknown character coding ({value})"), value=lines[2])
    iban = lines[3].replace(" ", "").upper()
    if not iban_valid(iban):
        raise QrBillError(N_("The creditor’s IBAN {iban} is invalid"), iban=lines[3])
    creditor = _address(lines[4:11], debtor=False)
    if any(lines[11:18]):
        raise QrBillError(N_("The QR bill fills in fields reserved for future use"))
    amount = _amount(lines[18])
    currency = lines[19]
    if currency not in CURRENCIES:
        raise QrBillError(N_("The currency “{value}” is not allowed (only CHF and EUR)"), value=currency)
    debtor = _address(lines[20:27], debtor=True) if any(lines[20:27]) else None
    reference = _reference(lines[27], lines[28], iban)
    message = lines[29]
    if lines[30] != TRAILER:
        raise QrBillError(N_("The QR bill doesn’t end with the trailer EPD"))
    bill_info = lines[31] if len(lines) > 31 else ""
    if len(message) + len(bill_info) > 140:
        raise QrBillError(N_("The message and the bill information are longer than 140 characters"))
    alternatives = tuple(x for x in lines[32:] if x)
    if any(len(x) > 100 for x in alternatives):
        raise QrBillError(N_("An alternative procedure is longer than 100 characters"))
    return QrBill(
        iban=iban,
        creditor=creditor,
        currency=currency,
        amount=amount,
        debtor=debtor,
        reference_type=lines[27],
        reference=reference,
        message=message,
        bill_info=bill_info,
        alternatives=alternatives,
        version=lines[1],
    )


# ------------------------------------------------------------------ display
def format_iban(iban: str) -> str:
    """CH44 3199 9123 0008 8901 2"""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def format_reference(kind: str, ref: str) -> str:
    """A QR reference in blocks of five after the first two digits, a creditor reference in blocks of four."""
    if kind == REF_QRR and len(ref) == 27:
        return " ".join([ref[:2], *(ref[i : i + 5] for i in range(2, 27, 5))])
    return " ".join(ref[i : i + 4] for i in range(0, len(ref), 4))


# ------------------------------------------------------------------ bill information (Swico S1)
@dataclass(frozen=True)
class Swico:
    """The bill information in Swico syntax version 1 (`//S1/…`); only what the app uses."""

    invoice_number: str = ""  # /10/
    invoice_date: date | None = None  # /11/ YYMMDD
    customer_reference: str = ""  # /20/
    vat_number: str = ""  # /30/
    terms: tuple[tuple[Decimal, int], ...] = ()  # /40/ discount % and days: "2:10;0:30"


def _unescaped_split(text: str) -> list[str]:
    """Split at "/" that isn't escaped by "\\"; "\\/" and "\\\\" stand for "/" and "\\"."""
    parts, cur, i = [], [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            cur.append(text[i + 1])
            i += 2
            continue
        if ch == "/":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def _yymmdd(text: str) -> date | None:
    if not re.fullmatch(r"\d{6}", text):
        return None
    try:
        return date(2000 + int(text[:2]), int(text[2:4]), int(text[4:]))
    except ValueError:
        return None


def swico(bill_info: str) -> Swico | None:
    """The Swico S1 bill information, or None for any other (or a malformed) text."""
    if not bill_info.startswith("//S1/"):
        return None
    parts = _unescaped_split(bill_info[len("//S1/") :])
    if len(parts) % 2:
        return None
    values: dict[str, str] = {}
    for tag, value in zip(parts[::2], parts[1::2], strict=True):
        if not re.fullmatch(r"\d{2}", tag):
            return None
        values.setdefault(tag, value)
    terms = []
    for entry in filter(None, values.get("40", "").split(";")):
        discount, _, days = entry.partition(":")
        try:
            terms.append((Decimal(discount), int(days)))
        except (InvalidOperation, ValueError):
            return None
    return Swico(
        invoice_number=values.get("10", ""),
        invoice_date=_yymmdd(values.get("11", "")),
        customer_reference=values.get("20", ""),
        vat_number=values.get("30", ""),
        terms=tuple(terms),
    )


def due_date(bill_info: str) -> date | None:
    """The due date the bill information states: its invoice date plus the net term (0 % discount), if both
    are there; None otherwise (a discount term alone says nothing about when the bill is due)."""
    info = swico(bill_info)
    if info is None or info.invoice_date is None:
        return None
    net = [days for discount, days in info.terms if discount == 0]
    if not net or not 0 <= net[0] <= 3650:
        return None
    return info.invoice_date + timedelta(days=net[0])
