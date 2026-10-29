"""Month-end balances from a CSV balance column or a camt closing balance (F36)."""

import json
from datetime import date

import pytest
from test_camt import CAMT053, entry, statement
from test_importer import DE, UBS

from budget_app.services import camt, importer, wealth
from budget_app.services import import_balances as ib


def csv_rows(text: str, **profile: object) -> list[importer.ImportRow]:
    parsed = importer.read_file("bank.csv", text.encode())
    guess = importer.guess_profile(parsed)
    for key, value in profile.items():
        setattr(guess, key, value)
    return importer.parse_rows(parsed, guess)


def offered(offer: ib.BalanceOffer) -> list[tuple[str, float, bool, bool]]:
    assert offer.problem == ""
    return [(c.month, c.amount, c.complete, c.include) for c in offer.candidates]


# Oldest booking first, debit and credit columns, three months; the file ends inside October
OLDEST_FIRST = (
    "Datum;Text;Belastung;Gutschrift;Saldo\n"
    "28.08.2026;Coop;50.00;;1'950.00\n"
    "31.08.2026;Zins;;0.50;1'950.50\n"
    "02.09.2026;Migros;45.80;;1'904.70\n"
    "25.09.2026;Lohn;;7'800.00;9'704.70\n"
    "26.09.2026;Miete;2'150.00;;7'554.70\n"
    "03.10.2026;Kino;30.00;;7'524.70\n"
)

# Newest booking first, one signed amount column with decimal commas
NEWEST_FIRST = (
    "Buchungsdatum;Verwendungszweck;Betrag;Kontostand\n"
    "30.09.2026;Miete;-1.500,00;2.500,00\n"
    "15.09.2026;Lohn;3.000,00;4.000,00\n"
    "15.09.2026;Coop;-100,00;1.000,00\n"
    "20.08.2026;Bäckerei;-10,00;1.100,00\n"
)


# ------------------------------------------------------------------ the balance column
@pytest.mark.parametrize("name", ["Saldo", "Kontostand", "Balance", "Solde", "Saldo contabile", "Saldo CHF"])
def test_the_balance_column_is_recognised_by_its_name(name):
    parsed = importer.read_file("bank.csv", f"Datum;Text;Betrag;{name}\n01.09.2026;Coop;-1.00;9.00\n".encode())
    assert importer.guess_profile(parsed).balance_col == name


def test_a_file_without_balance_offers_nothing():
    parsed = importer.read_file("de.csv", DE)
    profile = importer.guess_profile(parsed)
    assert profile.balance_col == ""
    rows = importer.parse_rows(parsed, profile)
    assert all(r.balance is None for r in rows)
    assert ib.from_rows(rows) == ib.BalanceOffer()


def test_profiles_saved_before_the_balance_column_still_load(ctx):
    old = {"name": "Old bank", "delimiter": ";", "date_col": "Datum", "amount_col": "Betrag", "text_cols": ["Text"]}
    (ctx.store.data_dir / importer.PROFILES_FILE).write_text(json.dumps([old]), encoding="utf-8")
    [profile] = importer.load_profiles(ctx.store)
    assert (profile.name, profile.balance_col) == ("Old bank", "")
    profile.balance_col = "Saldo"
    importer.save_profile(ctx.store, profile)
    assert importer.load_profiles(ctx.store)[0].balance_col == "Saldo"


def test_balance_cells_that_are_empty_or_unreadable_are_left_out():
    text = (
        "Datum;Text;Betrag;Saldo\n"
        "31.08.2026;Z;-1.00;\n"
        "01.09.2026;A;-1.00;\n"
        "02.09.2026;B;-1.00;n/a\n"
        "03.09.2026;C;-1.00;7.00\n"
    )
    rows = csv_rows(text)
    assert [r.balance for r in rows] == [None, None, None, 7.0]
    # the balances before the first one shown follow back from the amounts
    assert offered(ib.from_rows(rows)) == [("2026-08", 10.0, True, True), ("2026-09", 7.0, False, False)]


def test_inverted_card_statements_invert_the_balance_too():
    text = "Datum;Text;Betrag;Saldo\n01.09.2026;Shop;20.00;20.00\n30.09.2026;Shop;10.00;30.00\n"
    rows = csv_rows(text, invert=True)
    assert [(r.amount, r.balance) for r in rows] == [(-20.0, -20.0), (-10.0, -30.0)]
    assert offered(ib.from_rows(rows)) == [("2026-09", -30.0, True, True)]


# ------------------------------------------------------------------ order and completeness
def test_oldest_first_with_debit_and_credit_columns():
    rows = csv_rows(OLDEST_FIRST)
    assert [r.amount for r in rows[:2]] == [-50.0, 0.5]
    offer = ib.from_rows(rows)
    assert offered(offer) == [
        ("2026-08", 1950.5, True, True),  # the last booking is on 31 Aug
        ("2026-09", 7554.7, True, True),  # the file continues into October
        ("2026-10", 7524.7, False, False),  # the file ends on 3 Oct: offered unticked
    ]
    assert offer.candidates[1].date == date(2026, 9, 26) and offer.candidates[2].date == date(2026, 10, 3)


def test_newest_first_with_one_amount_column():
    rows = csv_rows(NEWEST_FIRST)
    assert importer.guess_profile(importer.read_file("b.csv", NEWEST_FIRST.encode())).balance_col == "Kontostand"
    assert offered(ib.from_rows(rows)) == [
        ("2026-08", 1100.0, True, True),
        ("2026-09", 2500.0, True, True),  # the last booking is on the month's last day
    ]


def test_the_swiss_bank_export_ends_inside_september():
    parsed = importer.read_file("ubs.csv", UBS)
    profile = importer.guess_profile(parsed)
    assert profile.balance_col == "Saldo"
    offer = ib.from_rows(importer.parse_rows(parsed, profile))
    assert offered(offer) == [("2026-09", 13804.2, False, False)]
    assert offer.candidates[0].date == date(2026, 9, 26)


def test_a_month_without_bookings_ends_with_the_balance_before_it():
    text = "Datum;Text;Betrag;Saldo\n10.07.2026;A;-5.00;95.00\n05.09.2026;B;-5.00;90.00\n"
    offer = ib.from_rows(csv_rows(text))
    assert offered(offer) == [
        ("2026-07", 95.0, True, True),
        ("2026-08", 95.0, True, True),
        ("2026-09", 90.0, False, False),
    ]
    assert offer.candidates[1].date == date(2026, 7, 10)


def test_lines_without_a_balance_are_bridged_by_their_amounts():
    text = (
        "Datum;Text;Betrag;Saldo\n"
        "01.09.2026;A;-5.00;95.00\n"
        "29.09.2026;B;-5.00;\n"
        "30.09.2026;C;-5.00;\n"
        "02.10.2026;D;-5.00;80.00\n"
        "03.10.2026;E;-5.00;\n"
    )
    assert offered(ib.from_rows(csv_rows(text))) == [("2026-09", 85.0, True, True), ("2026-10", 75.0, False, False)]


def test_balances_that_do_not_add_up_are_not_offered():
    text = "Datum;Text;Betrag;Saldo\n01.09.2026;A;-5.00;95.00\n02.09.2026;B;-5.00;91.00\n30.09.2026;C;-5.00;86.00\n"
    offer = ib.from_rows(csv_rows(text))
    assert offer.candidates == []
    assert offer.problem.startswith("The balances in the file do not follow from its amounts")


def test_an_unreadable_line_breaks_the_chain():
    text = "Datum;Text;Betrag;Saldo\n01.09.2026;A;-5.00;95.00\nkaputt;B;-5.00;90.00\n30.09.2026;C;-5.00;85.00\n"
    rows = csv_rows(text)
    assert rows[1].status == importer.ERROR
    assert ib.from_rows(rows).problem.startswith("The balances in the file do not follow")


def test_one_day_in_either_order():
    one_day = "Datum;Text;Betrag;Saldo\n30.09.2026;A;10.00;110.00\n30.09.2026;B;-10.00;100.00\n"
    offer = ib.from_rows(csv_rows(one_day))  # oldest first ends at 100, newest first at 110: can't tell
    assert offer.candidates == [] and offer.problem.startswith("The order of the lines can’t be told")
    single = "Datum;Text;Betrag;Saldo\n30.09.2026;A;10.00;110.00\n"
    assert offered(ib.from_rows(csv_rows(single))) == [("2026-09", 110.0, True, True)]


def test_dates_running_both_ways_are_not_a_reading():
    text = "Datum;Text;Betrag;Saldo\n02.09.2026;A;-5.00;95.00\n01.09.2026;B;-5.00;90.00\n03.09.2026;C;-5.00;85.00\n"
    assert ib.from_rows(csv_rows(text)).problem.startswith("The balances in the file do not follow")


# ------------------------------------------------------------------ camt
def test_a_statement_offers_its_closing_balance():
    st = camt.read_statement("september.xml", CAMT053)
    offer = ib.from_statement(st)
    assert offered(offer) == [("2026-09", 13554.2, True, True)]
    assert offer.candidates[0].date == date(2026, 9, 30)


def closing(amount: str = "500.00", day: str = "<Dt><Dt>2026-09-18</Dt></Dt>", ccy: str = "CHF") -> str:
    return (
        f'<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy="{ccy}">{amount}</Amt>'
        f"<CdtDbtInd>DBIT</CdtDbtInd>{day}</Bal>"
    )


def test_a_statement_ending_inside_a_month_offers_it_unticked():
    st = camt.read_statement("s.xml", statement(closing() + entry()))
    offer = ib.from_statement(st)
    assert offered(offer) == [("2026-09", -500.0, False, False)]  # overdrawn: DBIT
    assert offer.candidates[0].date == date(2026, 9, 18)


def test_a_closing_balance_without_date_uses_the_statement_end():
    body = "<FrToDt><FrDtTm>2026-09-01T00:00:00</FrDtTm><ToDtTm>2026-09-30T23:59:59</ToDtTm></FrToDt>"
    st = camt.read_statement("s.xml", statement(body + closing(day="") + entry()))
    assert offered(ib.from_statement(st)) == [("2026-09", -500.0, True, True)]
    st.end = None
    assert ib.from_statement(st) == ib.BalanceOffer()


def test_statements_without_a_usable_closing_balance_offer_nothing():
    assert ib.from_statement(camt.read_statement("s.xml", statement(entry()))) == ib.BalanceOffer()
    other = camt.read_statement("s.xml", statement(closing(ccy="EUR") + entry()))
    assert other.currency == "CHF" and ib.from_statement(other) == ib.BalanceOffer()


# ------------------------------------------------------------------ recorded balances
def test_recorded_balances_are_shown_replaced_or_left_alone(ctx, setup):
    chf = setup["chf"].id
    wealth.upsert_entry(ctx, chf, "2026-08", 1900.0)  # different: replaced
    wealth.upsert_entry(ctx, chf, "2026-09", 7554.7)  # the same: left unticked
    candidates = ib.compare(ctx, ib.from_rows(csv_rows(OLDEST_FIRST)).candidates, chf)
    aug, sep, oct_ = candidates
    assert (aug.recorded, aug.replaces, aug.unchanged, aug.include) == (1900.0, True, False, True)
    assert (sep.recorded, sep.replaces, sep.unchanged, sep.include) == (7554.7, False, True, False)
    assert (oct_.recorded, oct_.replaces, oct_.include) == (None, False, False)

    oct_.include = True  # the user ticks the partial month anyway
    saved = ib.save(ctx, chf, candidates)
    assert [(e.month, e.amount) for e in saved] == [("2026-08", 1950.5), ("2026-10", 7524.7)]
    assert wealth.entry_for(ctx, chf, "2026-08").amount == 1950.5  # replaced, not added
    assert len([e for e in ctx.store.account_entries.all() if e.month == "2026-08"]) == 1
    entry_ = wealth.entry_for(ctx, chf, "2026-10")
    assert entry_.fx_date == ctx.month_rate_date("2026-10") and entry_.currency == "CHF"


def test_balances_on_a_foreign_account_are_valued_at_the_month_end_rate(ctx, setup):
    eur = setup["eur"].id
    [candidate] = ib.compare(ctx, ib.from_statement(camt.read_statement("s.xml", CAMT053)).candidates, eur)
    assert candidate.recorded is None
    [saved] = ib.save(ctx, eur, [candidate])
    assert saved.fx_date == ctx.month_rate_date("2026-09")
    assert saved.amount_chf == pytest.approx(ctx.convert_on(13554.2, "EUR", "CHF", saved.fx_date))


def test_without_an_account_nothing_is_compared(ctx):
    [candidate] = ib.compare(ctx, ib.from_rows(csv_rows(NEWEST_FIRST)).candidates[1:], "")
    assert candidate.recorded is None and candidate.include
