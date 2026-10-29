from datetime import date

import pytest

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import importer
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput

# UBS-style export: Windows-1252, preamble lines, separate debit/credit columns, Swiss numbers
UBS = (
    "Kontonummer:;0235-00123456.01\n"
    "IBAN:;CH12 0023 5235 1234 5601 A\n"
    "\n"
    "Abschlussdatum;Buchungstext;Belastung;Gutschrift;Saldo\n"
    "02.09.2026;Migros Zürich HB;45.80;;8'154.20\n"
    "25.09.2026;Lohn Firma AG;;7'800.00;15'954.20\n"
    "26.09.2026;Miete Oktober;2'150.00;;13'804.20\n"
).encode("cp1252")

# German-style export: one signed amount column with decimal comma
DE = (
    "Buchungsdatum;Verwendungszweck;Betrag\n"
    "15.09.2026;Coop Pronto 1234;-12,30\n"
    "15.09.2026;Coop Pronto 1234;-12,30\n"
    "16.09.2026;Rückerstattung;1.234,50\n"
    "kaputt;Zeile ohne Datum;5,00\n"
).encode()


def test_reads_a_swiss_bank_export_with_preamble():
    parsed = importer.read_file("ubs.csv", UBS)
    assert parsed.delimiter == ";" and parsed.encoding == "cp1252"
    assert parsed.headers[:4] == ["Abschlussdatum", "Buchungstext", "Belastung", "Gutschrift"]
    profile = importer.guess_profile(parsed)
    assert (profile.date_col, profile.debit_col, profile.credit_col) == ("Abschlussdatum", "Belastung", "Gutschrift")
    assert profile.amount_col == "" and profile.text_cols == ["Buchungstext"]
    rows = importer.parse_rows(parsed, profile)
    assert [(r.date, r.amount, r.text) for r in rows] == [
        (date(2026, 9, 2), -45.80, "Migros Zürich HB"),
        (date(2026, 9, 25), 7800.0, "Lohn Firma AG"),
        (date(2026, 9, 26), -2150.0, "Miete Oktober"),
    ]


def test_reads_decimal_commas_and_reports_bad_rows():
    parsed = importer.read_file("de.csv", DE)
    profile = importer.guess_profile(parsed)
    assert profile.decimal == "," and profile.amount_col == "Betrag"
    rows = importer.parse_rows(parsed, profile)
    assert [r.amount for r in rows[:3]] == [-12.30, -12.30, 1234.50]
    assert rows[3].status == importer.ERROR and not rows[3].include and rows[3].error == "No valid date"


@pytest.mark.parametrize(
    ("text", "decimal", "value"),
    [
        ("1'234.50", ".", 1234.5),
        ("-12.30", ".", -12.3),
        ("12.30-", ".", -12.3),
        ("(12.30)", ".", -12.3),
        ("CHF 1,234.50", ".", 1234.5),
        ("1.234,50", ",", 1234.5),
        ("-0,99", ",", -0.99),
        ("", ".", None),
    ],
)
def test_parse_amount(text, decimal, value):
    assert importer.parse_amount(text, decimal) == value


def test_import_values_at_booking_date_and_skips_duplicates_next_time(ctx, setup):
    parsed = importer.read_file("de.csv", DE)
    profile = importer.guess_profile(parsed)
    rows = importer.analyse(ctx, importer.parse_rows(parsed, profile), setup["eur"].id)
    assert [r.status for r in rows] == ["new", "new", "new", "error"]
    assert rows[0].import_ref != rows[1].import_ref  # identical lines stay two transactions

    with pytest.raises(ValidationError, match="choose a category"):
        importer.commit(ctx, rows, setup["eur"].id)
    rows[0].category = rows[1].category = "01.1"
    rows[2].category = setup["salary"].id
    saved = importer.commit(ctx, rows, setup["eur"].id)
    assert len(saved) == 3
    assert saved[0].type == TX_EXPENDITURE and saved[0].from_account == setup["eur"].id
    assert saved[2].type == TX_INCOME and saved[2].to_account == setup["eur"].id
    # booked 15 Sep: last ECB rate on or before is 29 Aug (0.95 CHF per EUR), not the latest
    assert saved[0].fx_date == date(2026, 8, 29) and saved[0].amount_chf == pytest.approx(12.30 * 0.95)

    again = importer.analyse(ctx, importer.parse_rows(parsed, profile), setup["eur"].id)
    assert [r.status for r in again[:3]] == ["duplicate"] * 3
    assert not any(r.include for r in again)
    # a later file from the same account learns the categories
    assert again[0].category == "01.1" and again[0].suggested


def test_a_chosen_category_is_suggested_for_the_same_payee():
    def row(i: int, amount: float, payee: str, category: str = "", suggested: bool = False, **kw):
        return importer.ImportRow(
            i, date(2026, 9, 1), amount, payee, category=category, suggested=suggested, payee=payee, **kw
        )

    chosen = row(0, -5, "Kiosk Muster", "02.3")
    rows = [
        chosen,
        row(1, -3, "KIOSK MUSTER"),  # no category: filled in
        row(2, -4, "Kiosk Muster", "01.1", suggested=True),  # a suggestion: replaced
        row(3, -6, "Kiosk Muster", "05.6"),  # chosen by hand: kept
        row(4, 10, "Kiosk Muster"),  # money coming in: another type
        row(5, -2, "Bäckerei Muster"),  # another payee
        row(6, -2, "Kiosk Muster", status=importer.ERROR),
    ]
    assert importer.spread(rows, [chosen]) == 2
    assert [(r.category, r.suggested) for r in rows] == [
        ("02.3", False),
        ("02.3", True),
        ("02.3", True),
        ("05.6", False),
        ("", False),
        ("", False),
        ("", False),
    ]
    assert rows[1].reason == "Same payee as a row you chose"
    assert importer.spread(rows, [row(7, -1, "", "02.3")]) == 0  # no payee, nothing to go by


def test_rows_left_out_come_back_when_the_file_is_imported_again(ctx, setup):
    parsed = importer.read_file("ubs.csv", UBS)
    profile = importer.guess_profile(parsed)
    rows = importer.analyse(ctx, importer.parse_rows(parsed, profile), setup["chf"].id)
    assert [(r.category, r.suggested) for r in rows] == [("01.1", True), ("", False), ("", False)]
    rows[1].include = rows[2].include = False  # categorize those later
    assert len(importer.commit(ctx, rows, setup["chf"].id)) == 1

    again = importer.analyse(ctx, importer.parse_rows(parsed, profile), setup["chf"].id)
    assert [(r.status, r.include) for r in again] == [("duplicate", False), ("new", True), ("new", True)]


def test_manual_entry_on_the_same_day_is_flagged(ctx, setup):
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 2),
            type=TX_EXPENDITURE,
            amount=45.80,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
        ),
    )
    parsed = importer.read_file("ubs.csv", UBS)
    rows = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), setup["chf"].id)
    assert rows[0].status == importer.POSSIBLE_DUPLICATE and not rows[0].include
    assert rows[1].status == rows[2].status == importer.NEW


def test_profiles_are_saved_and_recognised(ctx):
    parsed = importer.read_file("ubs.csv", UBS)
    profile = importer.guess_profile(parsed)
    profile.name, profile.invert = "UBS", True
    importer.save_profile(ctx.store, profile)
    loaded = importer.load_profiles(ctx.store)
    assert [p.name for p in loaded] == ["UBS"]
    assert importer.guess_profile(parsed, loaded).invert  # same header -> saved settings


def test_rejects_empty_files():
    with pytest.raises(importer.ImportFileError):
        importer.read_file("empty.csv", b"\n\n")
