"""The Swiss QR-bill payload (F03): the guidelines' examples, both address types, and what a bank refuses."""

from datetime import date
from decimal import Decimal

import pytest
import qrbill as qrbill_lib  # an independent implementation (MIT), to cross-check payloads it writes

from budget_app import i18n
from budget_app.domain import qrbill as q
from budget_app.domain.qrbill import QrBillError

# The examples of the SIX Implementation Guidelines QR-bill (2.x), structured addresses
QRR_EXAMPLE = "\r\n".join(
    [
        "SPC",
        "0200",
        "1",
        "CH4431999123000889012",
        "S",
        "Robert Schneider AG",
        "Rue du Lac",
        "1268",
        "2501",
        "Biel",
        "CH",
        *[""] * 7,
        "1949.75",
        "CHF",
        "S",
        "Pia-Maria Rutschmann-Schnyder",
        "Grosse Marktgasse",
        "28",
        "9400",
        "Rorschach",
        "CH",
        "QRR",
        "210000000003139471430009017",
        "Auftrag vom 15.06.2020",
        "EPD",
        "//S1/10/10201409/11/190512/20/1400.000-53/30/106017086/31/180508/32/7.7/40/2:10;0:30",
        "Name AV1: UV;UltraPay005;12345",
        "Name AV2: XY;XYService;54321",
    ]
)
SCOR_EXAMPLE = "\n".join(
    [
        "SPC",
        "0200",
        "1",
        "CH5800791123000889012",
        "S",
        "Robert Schneider AG",
        "Rue du Lac",
        "1268",
        "2501",
        "Biel",
        "CH",
        *[""] * 7,
        "199.95",
        "CHF",
        "S",
        "Pia-Maria Rutschmann-Schnyder",
        "Grosse Marktgasse",
        "28",
        "9400",
        "Rorschach",
        "CH",
        "SCOR",
        "RF18539007547034",
        "",
        "EPD",
    ]
)
# no amount, no debtor, no reference: a donation slip
NON_EXAMPLE = "\n".join(
    [
        "SPC",
        "0200",
        "1",
        "CH3709000000304442225",
        "S",
        "Stiftung Höhenrausch",
        "Bahnhofstrasse",
        "12",
        "8001",
        "Zürich",
        "CH",
        *[""] * 7,
        "",
        "CHF",
        *[""] * 7,
        "NON",
        "",
        "Spende",
        "EPD",
    ]
)
# combined address (K): two address lines, no postal code or town of their own; EUR
COMBINED = "\r\n".join(
    [
        "SPC",
        "0200",
        "1",
        "LI21088100002324013AA",
        "K",
        "Muster Treuhand Anstalt",
        "Städtle 7",
        "9490 Vaduz",
        "",
        "",
        "LI",
        *[""] * 7,
        "50.5",
        "EUR",
        "K",
        "Hans Muster",
        "Musterweg 1",
        "8000 Zürich",
        "",
        "",
        "CH",
        "NON",
        "",
        "Rechnung 2026-17",
        "EPD",
        "",
    ]
)


def lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


def changed(text: str, index: int, value: str) -> str:
    parts = lines(text)
    parts[index] = value
    return "\n".join(parts)


def refused(text: str) -> str:
    with pytest.raises(QrBillError) as info:
        q.parse(text)
    return info.value.message.format(**info.value.params)


# ------------------------------------------------------------------ the guidelines' examples
def test_the_qr_reference_example() -> None:
    bill = q.parse(QRR_EXAMPLE)
    assert bill.iban == "CH4431999123000889012" and bill.qr_iban
    assert bill.creditor == q.Address("S", "Robert Schneider AG", "Rue du Lac", "1268", "2501", "Biel", "CH")
    assert bill.creditor.lines() == ["Rue du Lac 1268", "2501 Biel"]
    assert bill.amount == Decimal("1949.75") and bill.currency == "CHF" and not bill.notice
    assert bill.debtor is not None and bill.debtor.name == "Pia-Maria Rutschmann-Schnyder"
    assert bill.reference_type == "QRR" and bill.reference == "210000000003139471430009017"
    assert bill.message == "Auftrag vom 15.06.2020"
    assert bill.bill_info.startswith("//S1/10/10201409")
    assert bill.alternatives == ("Name AV1: UV;UltraPay005;12345", "Name AV2: XY;XYService;54321")
    assert bill.version == "0200"


def test_the_creditor_reference_example() -> None:
    bill = q.parse(SCOR_EXAMPLE)
    assert (bill.reference_type, bill.reference, bill.amount) == ("SCOR", "RF18539007547034", Decimal("199.95"))
    assert not bill.qr_iban and bill.bill_info == "" and bill.alternatives == ()


def test_without_amount_debtor_or_reference() -> None:
    bill = q.parse(NON_EXAMPLE)
    assert bill.amount is None and bill.debtor is None
    assert (bill.reference_type, bill.reference, bill.message) == ("NON", "", "Spende")
    assert bill.creditor.name == "Stiftung Höhenrausch"


def test_combined_addresses_and_euro() -> None:
    bill = q.parse(COMBINED)
    assert bill.iban == "LI21088100002324013AA" and bill.currency == "EUR"
    assert bill.creditor == q.Address("K", "Muster Treuhand Anstalt", "Städtle 7", "9490 Vaduz", "", "", "LI")
    assert bill.creditor.lines() == ["Städtle 7", "9490 Vaduz"]
    assert bill.debtor == q.Address("K", "Hans Muster", "Musterweg 1", "8000 Zürich", "", "", "CH")
    assert bill.amount == Decimal("50.50")  # one decimal is read; the guidelines write two


def test_lenient_where_the_meaning_is_clear() -> None:
    text = changed(changed(SCOR_EXAMPLE, 3, "CH58 0079 1123 0008 8901 2"), 28, "rf18 5390 0754 7034")
    bill = q.parse("﻿" + text + "\n")
    assert (bill.iban, bill.reference) == ("CH5800791123000889012", "RF18539007547034")


@pytest.mark.parametrize("street", [True, False])
def test_payloads_of_another_implementation(street: bool) -> None:
    """Bills written by the `qrbill` package, with structured and combined addresses."""
    creditor = (
        {"name": "Robert Schneider AG", "street": "Rue du Lac", "house_num": "1268", "pcode": "2501", "city": "Biel"}
        if street
        else {"name": "Robert Schneider AG", "line1": "Rue du Lac 1268", "line2": "2501 Biel"}
    )
    lib_bill = qrbill_lib.QRBill(
        account="CH44 3199 9123 0008 8901 2",
        creditor={**creditor, "country": "CH"},
        amount="1949.75",
        reference_number="21 00000 00003 13947 14300 09017",
        additional_information="Auftrag vom 15.06.2020",
        debtor={"name": "Pia Rutschmann", "line1": "Marktgasse 28", "line2": "9400 Rorschach", "country": "CH"},
    )
    bill = q.parse(lib_bill.qr_data())
    assert bill.creditor.kind == ("S" if street else "K")
    assert bill.creditor.lines() == ["Rue du Lac 1268", "2501 Biel"]
    assert (bill.amount, bill.reference, bill.message) == (
        Decimal("1949.75"),
        "210000000003139471430009017",
        "Auftrag vom 15.06.2020",
    )


# ------------------------------------------------------------------ check digits
@pytest.mark.parametrize(
    ("iban", "valid", "qr"),
    [
        ("CH4431999123000889012", True, True),
        ("CH5800791123000889012", True, False),
        ("CH9300762011623852957", True, False),
        ("LI21088100002324013AA", True, False),
        ("CH4431999123000889013", False, True),  # wrong check digits
        ("DE89370400440532013000", False, False),  # not Swiss
        ("CH44319991230008890", False, True),  # too short
    ],
)
def test_iban(iban: str, valid: bool, qr: bool) -> None:
    assert q.iban_valid(iban) is valid
    assert q.is_qr_iban(iban) is qr


def test_qr_reference_mod10() -> None:
    assert q.mod10_check_digit("21000000000313947143000901") == 7
    assert q.qr_reference_valid("210000000003139471430009017")
    assert q.qr_reference_valid("000000000000000000000000000")
    assert not q.qr_reference_valid("210000000003139471430009018")
    assert not q.qr_reference_valid("21000000000313947143000901")  # 26 digits


def test_creditor_reference_iso11649() -> None:
    assert q.creditor_reference_valid("RF18539007547034")
    assert q.creditor_reference_valid("RF712348231")
    assert not q.creditor_reference_valid("RF19539007547034")
    assert not q.creditor_reference_valid("RF18" + "1" * 22)  # longer than 25
    assert not q.creditor_reference_valid("XX18539007547034")


# ------------------------------------------------------------------ what a bank refuses
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", q.NOT_A_BILL),
        ("https://example.com", q.NOT_A_BILL),
        (changed(SCOR_EXAMPLE, 1, "0100"), "QR bills of version 0100 can’t be read"),
        ("SPC", "QR bills of version  can’t be read"),
        ("\n".join(lines(SCOR_EXAMPLE)[:30]), "The QR bill has too few or too many lines"),
        (SCOR_EXAMPLE + "\n\nx\ny\nz", "The QR bill has too few or too many lines"),
        (changed(SCOR_EXAMPLE, 2, "2"), "The QR bill uses an unknown character coding (2)"),
        (changed(SCOR_EXAMPLE, 3, "CH5800791123000889013"), "The creditor’s IBAN CH5800791123000889013 is invalid"),
        (changed(SCOR_EXAMPLE, 3, "DE89370400440532013000"), "The creditor’s IBAN DE89370400440532013000 is invalid"),
        (changed(SCOR_EXAMPLE, 4, "X"), "The address type “X” is unknown"),
        (changed(SCOR_EXAMPLE, 5, ""), "The creditor’s address is invalid"),
        (changed(SCOR_EXAMPLE, 5, "N" * 71), "The creditor’s address is invalid"),
        (changed(SCOR_EXAMPLE, 8, ""), "The creditor’s address is invalid"),  # S needs a postal code
        (changed(SCOR_EXAMPLE, 7, "1" * 17), "The creditor’s address is invalid"),
        (changed(SCOR_EXAMPLE, 10, "Schweiz"), "The creditor’s address is invalid"),
        (changed(SCOR_EXAMPLE, 25, ""), "The debtor’s address is invalid"),
        (changed(COMBINED, 8, "9490"), "The creditor’s address is invalid"),  # K has no postal code field
        (changed(COMBINED, 7, ""), "The creditor’s address is invalid"),  # K needs line 2
        (changed(SCOR_EXAMPLE, 12, "Someone"), "The QR bill fills in fields reserved for future use"),
        (changed(SCOR_EXAMPLE, 18, "1'949.75"), "The amount “1'949.75” is invalid"),
        (changed(SCOR_EXAMPLE, 18, "1949,75"), "The amount “1949,75” is invalid"),
        (changed(SCOR_EXAMPLE, 18, "-5.00"), "The amount “-5.00” is invalid"),
        (changed(SCOR_EXAMPLE, 18, "1000000000.00"), "The amount “1000000000.00” is invalid"),
        (changed(SCOR_EXAMPLE, 18, "12.345"), "The amount “12.345” is invalid"),
        (changed(SCOR_EXAMPLE, 19, "USD"), "The currency “USD” is not allowed (only CHF and EUR)"),
        (changed(SCOR_EXAMPLE, 27, "IPI"), "The reference type “IPI” is unknown"),
        (changed(SCOR_EXAMPLE, 28, "RF19539007547034"), "The creditor reference RF19539007547034 is invalid"),
        (
            changed(QRR_EXAMPLE, 28, "210000000003139471430009018"),
            "The QR reference 210000000003139471430009018 is invalid",
        ),
        (
            changed(changed(SCOR_EXAMPLE, 27, "QRR"), 28, "210000000003139471430009017"),
            "A QR reference needs a QR-IBAN",
        ),
        (changed(QRR_EXAMPLE, 3, "CH5800791123000889012"), "A QR reference needs a QR-IBAN"),
        (changed(changed(QRR_EXAMPLE, 27, "NON"), 28, ""), "A QR-IBAN needs a QR reference"),
        (changed(changed(QRR_EXAMPLE, 27, "SCOR"), 28, "RF18539007547034"), "A QR-IBAN needs a QR reference"),
        (changed(NON_EXAMPLE, 28, "12345"), "The reference type NON doesn’t allow a reference"),
        (changed(SCOR_EXAMPLE, 30, "EOD"), "The QR bill doesn’t end with the trailer EPD"),
        (
            SCOR_EXAMPLE.replace("\nEPD", "\nEPD\n" + "x" * 141),
            "The message and the bill information are longer than 140 characters",
        ),
        (
            SCOR_EXAMPLE + "\n\n" + "a" * 101,
            "An alternative procedure is longer than 100 characters",
        ),
        (
            SCOR_EXAMPLE.replace("\nEPD", "\nEPD\n" + "x" * 100 + "\n" + "y" * 900),
            "The QR bill is longer than 997 characters",
        ),
    ],
)
def test_refused(text: str, message: str) -> None:
    assert refused(text) == message


def test_a_notice_is_read_but_marked() -> None:
    bill = q.parse(changed(SCOR_EXAMPLE, 18, "0.00"))
    assert bill.amount == 0 and bill.notice


def test_errors_are_translated() -> None:
    i18n.set_language("de")
    try:
        with pytest.raises(QrBillError) as info:
            q.parse(changed(SCOR_EXAMPLE, 19, "USD"))
        assert "USD" in info.value.translated() and "nicht" in info.value.translated()
    finally:
        i18n.set_language("en")


def test_is_payload() -> None:
    assert q.is_payload(SCOR_EXAMPLE) and q.is_payload("SPC\r\n0100")
    assert not q.is_payload("") and not q.is_payload("WIFI:S:home;;")


# ------------------------------------------------------------------ display
def test_formatting() -> None:
    assert q.format_iban("CH4431999123000889012") == "CH44 3199 9123 0008 8901 2"
    assert q.format_reference("QRR", "210000000003139471430009017") == "21 00000 00003 13947 14300 09017"
    assert q.format_reference("SCOR", "RF18539007547034") == "RF18 5390 0754 7034"
    assert q.format_reference("NON", "") == ""


# ------------------------------------------------------------------ bill information (Swico)
def test_swico_bill_information() -> None:
    info = q.swico("//S1/10/10201409/11/190512/20/1400.000-53/30/106017086/31/180508/32/7.7/40/2:10;0:30")
    assert info is not None
    assert (info.invoice_number, info.invoice_date, info.customer_reference, info.vat_number) == (
        "10201409",
        date(2019, 5, 12),
        "1400.000-53",
        "106017086",
    )
    assert info.terms == ((Decimal(2), 10), (Decimal(0), 30))
    assert q.due_date("//S1/10/10201409/11/190512/40/2:10;0:30") == date(2019, 6, 11)


def test_swico_escapes() -> None:
    info = q.swico(r"//S1/10/X.66711\/8824/11/200712/20/MW-2020\\04/40/0:15")
    assert info is not None and info.invoice_number == "X.66711/8824" and info.customer_reference == "MW-2020\\04"
    assert q.due_date(r"//S1/10/X.66711\/8824/11/200712/40/0:15") == date(2020, 7, 27)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Rechnung 17",
        "//S2/10/123",  # another syntax
        "//S1/10",  # a tag without a value
        "//S1/1/12345/11/200712",  # a tag of one digit
        "//S1/40/zwei:10",
    ],
)
def test_other_bill_information_is_not_swico(text: str) -> None:
    assert q.swico(text) is None and q.due_date(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "//S1/10/123",  # no invoice date
        "//S1/11/200712",  # no terms
        "//S1/11/200712/40/2:10",  # a discount term only
        "//S1/11/201399/40/0:30",  # no such day
        "//S1/11/2007/40/0:30",  # not YYMMDD
        "//S1/11/200712/40/0:99999",  # implausible
    ],
)
def test_no_due_date_without_a_reliable_one(text: str) -> None:
    assert q.due_date(text) is None
