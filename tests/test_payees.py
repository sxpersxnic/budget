"""Payees (F09): payee names from bank texts, the payee table with its aliases, rename, merge and delete,
and the payee carried by transactions, templates, recurring series and imports."""

import json
from datetime import date

import pytest

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Payee
from budget_app.domain.payees import alias_key, brand, key, normalize
from budget_app.services import camt, importer, undo
from budget_app.services import export as export_svc
from budget_app.services import payees as payee_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import templates as templates_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store

# ------------------------------------------------------------------ normalization
# (raw bank text, payee name): Swiss banks in German, French, Italian and English
BANK_LINES = [
    # UBS, ZKB, Raiffeisen, PostFinance (German)
    ("Einkauf Debitkarte 12.10.2026 MIGROS ZUERICH HB 1234", "Migros"),
    ("MIGROS ZUERICH HB 1234", "Migros"),
    ("Debitkarte Kauf/Dienstleistung 12.10.2026 MIGROS M ZUERICH HB Kartennummer: XXXX 5678", "Migros"),
    ("KAUF/DIENSTLEISTUNG VOM 12.10.2026 KARTEN NR. XXXX1234 COOP-1234 BERN", "Coop"),
    ("Zahlung Debitkarte 12.10.2026 14:23 Coop-2345 Luzern Bahnhof Kartennummer xxxx 1234", "Coop"),
    ("COOP PRONTO SHOP 4711 ZUG", "Coop Pronto"),
    ("Einkauf ZKB Visa Debit Karte Nr. 1234 SBB CFF FFS Zürich HB 8001 Zürich", "SBB"),
    ("TWINT KAUF/DIENSTLEISTUNG VOM 03.10.2026 GALAXUS.CH", "Galaxus"),
    ("Zahlung TWINT: Digitec Galaxus AG", "Digitec Galaxus"),
    ("Lastschrift CSS Versicherung AG", "CSS"),
    ("LSV+ Helsana Versicherungen AG", "Helsana"),
    ("eBill Swisscom (Schweiz) AG Rechnung 10/2026", "Swisscom"),
    ("Zahlung an Elektrizitätswerk der Stadt Zürich", "EWZ"),
    ("RESTAURANT ZUM GRUENEN HECHT ZUERICH", "Restaurant zum Gruenen Hecht"),
    ("Einkauf Debit Mastercard 05.10.2026 H&M HENNES & MAURITZ ZUERICH", "H&M"),
    ("MCDONALDS 1234 ZUERICH", "McDonald's"),
    ("CAFE SPRUENGLI ZUERICH", "Sprüngli"),
    ("IKEA AG SPREITENBACH", "IKEA"),
    ("Migros Bank AG", "Migros Bank"),
    ("Gutschrift Lohn Muster AG", "Muster AG"),
    # decision 11: the legal form stays, in its usual spelling; the location before it goes
    ("EINKAUF MUSTER GMBH ZUERICH", "Muster GmbH"),
    ("Restaurant Zum Hirschen Luzern AG", "Restaurant Zum Hirschen AG"),  # mixed case stays
    ("Paiement à BOULANGERIE DUPONT SARL LAUSANNE", "Boulangerie Dupont Sàrl"),
    ("Pagamento a Pizzeria Bella SAGL Lugano", "Pizzeria Bella Sagl"),
    ("Zahlung Meier & Co Bern", "Meier"),  # "co" is also a plain word: dropped as before
    ("TWINT: Zahlung an +41 79 123 45 67", "TWINT"),
    ("Dauerauftrag Miete Oktober", ""),
    # TWINT, SumUp, PayPal and other payment services
    ("TWINT *Brezelkoenig", "Brezelkönig"),
    ("SumUp *Cafe Zaehringer Bern", "Cafe Zaehringer"),
    ("SQ *COFFEE LAB ZURICH", "Coffee Lab"),
    ("PAYPAL *NETFLIX", "Netflix"),
    ("Google *YouTube Premium", "YouTube"),
    ("AMZN Mktp DE*AB12CD345", "Amazon"),
    ("APPLE.COM/BILL ITUNES.COM", "Apple"),
    ("UBER *EATS", "Uber Eats"),
    # French
    ("Achat VISA Debit 12.10.2026 MIGROS GENEVE CORNAVIN Numéro de carte XXXX1234", "Migros"),
    ("ACHAT/SERVICE DU 12.10.2026 NO DE CARTE XXXX1234 MANOR LAUSANNE", "Manor"),
    ("Paiement Swisscom (Suisse) SA", "Swisscom"),
    ("Paiement à Café du Nord SA Genève", "Café du Nord SA"),
    ("Ordre permanent Gérance Immobilière SA Loyer", "Gérance Immobilière SA"),
    # Italian
    ("Acquisto carta di debito 12.10.2026 COOP LUGANO", "Coop"),
    ("Pagamento TWINT a Mario Rossi", "Mario Rossi"),
    ("Pagamento presso Farmacia Centrale Bellinzona", "Farmacia Centrale"),
    # English (Revolut, English e-banking)
    ("Card payment to Migros Zurich", "Migros"),
    ("To Max Muster", "Max Muster"),
    ("POS 12.10.26 STARBUCKS ZURICH", "Starbucks"),
    ("Direct debit SWISSCOM (SCHWEIZ) AG", "Swisscom"),
    ("Purchase Debit card 12.10.2026 DIGITEC GALAXUS AG ZUERICH", "Digitec Galaxus"),
    ("Netflix.com Los Gatos", "Netflix"),
    ("Top-Up by *1234", "Top-Up"),
    ("", ""),
]


@pytest.mark.parametrize(("raw", "name"), BANK_LINES)
def test_normalize_bank_texts(raw, name):
    assert normalize(raw) == name
    assert normalize(raw) == normalize(raw)  # deterministic


def test_the_alias_key_ignores_what_changes_from_booking_to_booking():
    a = alias_key("Einkauf Debitkarte 02.10.2026 14:05 MIGROS ZUERICH HB Karte Nr. 1234 Ref. 99887")
    b = alias_key("EINKAUF DEBITKARTE 28.10.2026 MIGROS ZUERICH HB KARTE NR. 5678 REF. 12345")
    assert a == b == "migros zuerich hb"
    assert alias_key("Migros Bern") != a  # another shop of the brand is another text
    assert alias_key("Einkauf Debitkarte 02.10.2026") == ""


def test_keys_and_brands():
    assert key("Café-Bar  ZÜRICH") == key("cafe bar zurich") == "cafe bar zurich"
    assert brand(["avec", "bahnhof"]) == "Avec" and brand(["paiement", "avec", "carte"]) == ""  # start only
    assert brand(["ubs", "coop"]) == "Coop"  # banks only when nothing else matches
    assert brand(["ubs"]) == "UBS"
    assert normalize("DR MED HANS MUSTER LUZERN") == "Dr Med Hans Muster"  # no acronym for Dr
    assert normalize("BAECKEREI PKZ TEST") == "Baeckerei PKZ Test"  # acronyms keep their capitals
    assert normalize("bäckerei kuhn") == "Bäckerei Kuhn"
    assert normalize("A1 TELEKOM") == "A1 Telekom"


# ------------------------------------------------------------------ the payee table
def spend(setup, payee: str = "", **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        type=TX_EXPENDITURE,
        amount=20.0,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
        payee=payee,
    )
    base.update(kw)
    return TransactionInput(**base)


def test_old_files_without_payees_load_with_empty_payees(tmp_path):
    (tmp_path / "transactions.csv").write_text(
        "date,type,amount,currency,amount_chf,fx_date,category,from_account,id\n"
        "2026-09-10,expenditure,12.5,CHF,12.5,2026-09-10,01.1,acc1,tx1\n"
    )
    (tmp_path / "templates.csv").write_text("name,type,amount,id\nCoffee,expenditure,4.5,tp1\n")
    store = Store(tmp_path)
    assert store.transactions.get("tx1").payee == ""
    assert store.templates.get("tp1").payee == ""
    assert store.payees.all() == [] and (tmp_path / "payees.csv").read_text() == "name,aliases,id\n"
    assert "payees" in store.tables()


def test_saving_a_transaction_adds_its_payee_once(ctx, setup):
    tx = tx_svc.save(ctx, spend(setup, "  Bäckerei   Kuhn "))
    assert tx.payee == "Bäckerei Kuhn"
    again = tx_svc.save(ctx, spend(setup, "BAECKEREI KUHN"))  # another spelling is another payee ...
    same = tx_svc.save(ctx, spend(setup, "bäckerei kuhn"))  # ... but case and accents are not
    assert same.payee == "Bäckerei Kuhn" and again.payee == "BAECKEREI KUHN"
    assert [p.name for p in payee_svc.all_payees(ctx)] == ["Bäckerei Kuhn", "BAECKEREI KUHN"]
    transfer = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 10),
            type="transfer",
            amount=5,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=acc2(ctx).id,
            payee="Ignored",
        ),
    )
    assert transfer.payee == "" and payee_svc.find(ctx, "ignored") is None
    assert tx_svc.save(ctx, spend(setup)).payee == ""


def acc2(ctx):
    from budget_app.services import accounts as acc_svc  # noqa: PLC0415

    return acc_svc.add_account(ctx, "Second", "CHF")


def test_resolve_looks_up_aliases_and_names_first(ctx):
    raw = "Einkauf Debitkarte 02.10.2026 MIGROS ZUERICH HB 1234"
    assert payee_svc.resolve(ctx, raw) == "Migros"  # nothing stored yet: the normalized name
    ctx.store.payees.add(Payee(name="MIGROS"))
    assert payee_svc.resolve(ctx, raw) == "MIGROS"  # an existing spelling, never a second one
    payee_svc.learn(ctx, [(raw, "Groceries Shop")])
    assert payee_svc.resolve(ctx, "EINKAUF 28.10.2026 MIGROS ZUERICH HB 9") == "Groceries Shop"
    assert payee_svc.resolve(ctx, "Dauerauftrag Miete Oktober") == ""


def test_learn_moves_an_alias_to_the_payee_chosen_last(ctx):
    raw = "SumUp *Cafe Zaehringer Bern"
    payee_svc.learn(ctx, [(raw, "Cafe Zaehringer")])
    payee_svc.learn(ctx, [(raw, "Café Zähringer Bern")])
    first, second = payee_svc.find(ctx, "Cafe Zaehringer"), payee_svc.find(ctx, "Café Zähringer Bern")
    assert first.alias_list == [] and second.alias_list == ["cafe zaehringer bern"]
    payee_svc.learn(ctx, [("SUMUP *CAFE ZAEHRINGER", "Cafe Zaehringer")])  # equals its name: nothing to add
    assert payee_svc.find(ctx, "Cafe Zaehringer").alias_list == []
    payee_svc.learn(ctx, [(raw, "Zähringer")])
    assert payee_svc.find(ctx, "Zähringer").alias_list == ["cafe zaehringer bern"]
    payee_svc.learn(ctx, [(raw, "Zähringer"), ("", "Zähringer"), ("x", "")])  # nothing new
    assert len(ctx.store.payees.all()) == 3


def test_ensure_takes_a_former_name_from_its_old_payee(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Kiosk"))
    payee_svc.rename(ctx, payee_svc.find(ctx, "Kiosk").id, "k kiosk")
    assert payee_svc.resolve(ctx, "KIOSK") == "k kiosk"  # the former name leads to the renamed payee
    assert payee_svc.ensure(ctx, "Kiosk") == "Kiosk"  # typed as a new payee: it is one now
    assert payee_svc.find(ctx, "k kiosk").alias_list == []
    assert payee_svc.resolve(ctx, "KIOSK") == "Kiosk"
    assert payee_svc.ensure(ctx, "  ") == ""


def test_names_lists_recent_payees_first(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Older", date=date(2026, 8, 1)))
    tx_svc.save(ctx, spend(setup, "Newer", date=date(2026, 9, 1)))
    ctx.store.payees.add(Payee(name="Alpha"))
    assert payee_svc.names(ctx) == ["Newer", "Older", "Alpha"]
    assert payee_svc.names(ctx, limit_recent=1) == ["Newer", "Alpha", "Older"]


def test_usage_counts_spending_and_income(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Muster AG", amount=30))
    tx_svc.save(ctx, spend(setup, "Muster AG", amount=12.5, date=date(2026, 9, 20)))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=100,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            payee="muster ag",
        ),
    )
    tx_svc.save(ctx, spend(setup))  # no payee
    assert payee_svc.find(ctx, "") is None
    use = payee_svc.usage(ctx, "CHF")[key("Muster AG")]
    assert (use.count, use.spent, use.received, use.last) == (3, 42.5, 100, date(2026, 9, 25))


# ------------------------------------------------------------------ rename, merge, delete
def test_rename_changes_every_record_in_one_undo_step(ctx, setup):
    for _ in range(3):
        tx_svc.save(ctx, spend(setup, "MIGROS"))
    templates_svc.save(ctx, "Groceries", spend(setup, "MIGROS"))
    series = rec_svc.save(
        ctx, RecurringInput(type=TX_EXPENDITURE, amount=9, currency="CHF", category="01.1",
                            from_account=setup["chf"].id, start=date(2026, 9, 1), payee="Migros")
    )  # fmt: skip
    migros = payee_svc.find(ctx, "migros")
    payee_svc.learn(ctx, [("MIGROS ZUERICH HB 1234", "MIGROS")])

    renamed = payee_svc.rename(ctx, migros.id, "Migros Supermarkt")
    assert renamed.alias_list == ["migros", "migros zuerich hb"]
    assert {tx.payee for tx in ctx.store.transactions.all()} == {"Migros Supermarkt"}
    assert ctx.store.templates.all()[0].payee == "Migros Supermarkt"
    assert ctx.store.recurring.get(series.id).payee == "Migros Supermarkt"
    # the next import of the same text, or of another Migros shop, lands on the renamed payee
    assert payee_svc.resolve(ctx, "Einkauf 01.11.2026 MIGROS ZUERICH HB 777") == "Migros Supermarkt"
    assert payee_svc.resolve(ctx, "MIGROS BERN BAHNHOF") == "Migros Supermarkt"

    undo.undo(ctx, undo.latest(ctx).id)
    assert {tx.payee for tx in ctx.store.transactions.all()} == {"MIGROS"}
    assert payee_svc.find(ctx, "migros").alias_list == ["migros zuerich hb"]

    assert payee_svc.rename(ctx, migros.id, "Migros").name == "Migros"  # only the case changes
    assert payee_svc.find(ctx, "migros").alias_list == ["migros zuerich hb"]
    assert {tx.payee for tx in ctx.store.transactions.all()} == {"Migros"}


def test_rename_refuses_an_empty_or_taken_name(ctx):
    a = ctx.store.payees.add(Payee(name="Coop"))
    ctx.store.payees.add(Payee(name="Migros"))
    with pytest.raises(ValidationError, match="Name is required"):
        payee_svc.rename(ctx, a.id, " ")
    with pytest.raises(ValidationError, match="already exists"):
        payee_svc.rename(ctx, a.id, "MIGROS")
    with pytest.raises(ValidationError, match="not found"):
        payee_svc.rename(ctx, "nope", "X")


def test_merge_moves_transactions_and_aliases_in_one_undo_step(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Holy Cow"))
    tx_svc.save(ctx, spend(setup, "Holy Cow!"))
    tx_svc.save(ctx, spend(setup, "HolyCow Gourmet"))
    payee_svc.learn(ctx, [("TWINT *HOLY COW BERN", "Holy Cow"), ("SumUp *HolyCow Gourmet Zuerich", "HolyCow Gourmet")])
    ids = [p.id for p in payee_svc.all_payees(ctx)]
    keep = payee_svc.find(ctx, "Holy Cow")
    assert len(ids) == 2  # "Holy Cow!" is the same payee as "Holy Cow"

    kept = payee_svc.merge(ctx, ids, keep.id)
    assert [p.name for p in payee_svc.all_payees(ctx)] == ["Holy Cow"]
    assert kept.alias_list == ["holy cow bern", "holycow gourmet", "holycow gourmet zuerich"]
    assert {tx.payee for tx in ctx.store.transactions.all()} == {"Holy Cow"}
    assert payee_svc.resolve(ctx, "SUMUP *HOLYCOW GOURMET ZUERICH") == "Holy Cow"

    undo.undo(ctx, undo.latest(ctx).id)
    assert len(payee_svc.all_payees(ctx)) == 2
    assert sorted(tx.payee for tx in ctx.store.transactions.all()) == ["Holy Cow", "Holy Cow", "HolyCow Gourmet"]
    with pytest.raises(ValidationError, match="at least two"):
        payee_svc.merge(ctx, [keep.id], keep.id)


def test_delete_only_unused_payees(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Used"))
    templates_svc.save(ctx, "T", spend(setup, "In a template"))
    unused = ctx.store.payees.add(Payee(name="Unused"))
    used, in_template = payee_svc.find(ctx, "Used"), payee_svc.find(ctx, "In a template")
    assert payee_svc.in_use(ctx, used) and payee_svc.in_use(ctx, in_template) and not payee_svc.in_use(ctx, unused)
    with pytest.raises(ValidationError, match="still in use"):
        payee_svc.delete(ctx, [unused.id, in_template.id])
    assert payee_svc.delete(ctx, [unused.id, "gone"]) == 1
    assert [p.name for p in payee_svc.all_payees(ctx)] == ["In a template", "Used"]


# ------------------------------------------------------------------ templates, duplicates, recurring
def test_templates_duplicates_and_series_carry_the_payee(ctx, setup):
    tp = templates_svc.save(ctx, "Rent", spend(setup, " Immo  AG "))
    assert tp.payee == "Immo AG"
    assert templates_svc.as_input(tp, TransactionInput()).payee == "Immo AG"
    tx = tx_svc.save(ctx, spend(setup, "Immo AG"))
    assert tx_svc.duplicate_input(ctx, tx, date(2026, 10, 1)).payee == "Immo AG"
    assert tx_svc.to_input(tx).payee == "Immo AG"

    inp = rec_svc.from_transaction(tx)
    assert inp.payee == "Immo AG"
    series = rec_svc.save(ctx, inp, first_booked=tx.id)
    assert series.payee == "Immo AG" and rec_svc.to_input(series).payee == "Immo AG"
    assert rec_svc.title(ctx, series) == "Immo AG · 01.1 Groceries"
    booked = rec_svc.book(ctx, series.id, series.next_due)
    assert booked.payee == "Immo AG"

    salary = rec_svc.save(
        ctx, RecurringInput(type=TX_INCOME, amount=100, currency="CHF", category=setup["salary"].id,
                            to_account=setup["chf"].id, start=date(2026, 9, 25), payee="Muster AG")
    )  # fmt: skip
    assert rec_svc.title(ctx, salary) == "Muster AG · Salary"
    transfer = rec_svc.save(
        ctx, RecurringInput(type="transfer", amount=100, currency="CHF", from_account=setup["chf"].id,
                            to_account=acc2(ctx).id, start=date(2026, 9, 25), payee="Nobody")
    )  # fmt: skip
    assert transfer.payee == ""


def test_history_and_export_know_payees(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Coop"))
    action = undo.latest(ctx)
    lines = [undo.describe_change(ctx, c) for c in action.changes]
    assert "Payee added: Coop" in lines
    doc = json.loads(export_svc.everything_json(ctx))
    assert [p["name"] for p in doc["payees"]] == ["Coop"]


# ------------------------------------------------------------------ imports
CSV = (
    "Buchungsdatum;Buchungstext;Empfänger;Betrag\n"
    "02.10.2026;Einkauf Debitkarte 02.10.2026 MIGROS ZUERICH HB 1234;;-45.20\n"
    "03.10.2026;Einkauf 03.10.2026;Bäckerei Kuhn Bern;-4.80\n"
    "04.10.2026;Dauerauftrag Miete Oktober;;-1900.00\n"
).encode()


def import_csv(ctx, account_id: str) -> list:
    parsed = importer.read_file("bank.csv", CSV)
    profile = importer.guess_profile(parsed)
    assert profile.text_cols == ["Buchungstext", "Empfänger"]
    rows = importer.analyse(ctx, importer.parse_rows(parsed, profile), account_id)
    for r in rows:
        r.category = "01.1"
    return rows


def test_a_csv_import_fills_and_remembers_payees(ctx, setup):
    rows = import_csv(ctx, setup["chf"].id)
    assert [(r.counterparty, r.payee) for r in rows] == [
        ("", "Migros"),
        ("Bäckerei Kuhn Bern", "Bäckerei Kuhn"),
        ("", ""),
    ]
    rows[0].payee = "Migros Supermarkt"  # changed in the preview
    importer.commit(ctx, rows, setup["chf"].id)
    assert [tx.payee for tx in sorted(ctx.store.transactions.all(), key=lambda t: t.date)] == [
        "Migros Supermarkt",
        "Bäckerei Kuhn",
        "",
    ]
    assert payee_svc.find(ctx, "Migros Supermarkt").alias_list == ["migros zuerich hb"]
    assert payee_svc.find(ctx, "Bäckerei Kuhn").alias_list == ["backerei kuhn bern"]
    assert len(undo.latest(ctx).changes) == 3 + 2  # the transactions and their payees: one undo step

    # renamed later: the next import of the same text lands on the new name
    payee_svc.rename(ctx, payee_svc.find(ctx, "Bäckerei Kuhn").id, "Kuhn")
    again = importer.analyse(ctx, importer.parse_rows(importer.read_file("b.csv", CSV), importer.guess_profile(
        importer.read_file("b.csv", CSV))), setup["eur"].id)  # fmt: skip
    assert [r.payee for r in again] == ["Migros Supermarkt", "Kuhn", ""]


def test_a_camt_import_reads_the_payee_from_the_counterparty(ctx, setup):
    from test_camt import CAMT053  # noqa: PLC0415

    st = camt.read_statement("september.xml", CAMT053)
    assert [r.counterparty for r in st.rows] == [
        "Migros Zürich HB",
        "Firma AG",
        "",
        "Swisscom (Schweiz) AG",
        "ewz",
        "",
    ]
    rows = importer.analyse(ctx, st.fresh_rows(), setup["chf"].id)
    assert [r.payee for r in rows] == ["Migros", "Firma AG", "", "Swisscom", "EWZ", "Spende Sammelauftrag"]
    for r in rows:
        r.category = setup["salary"].id if r.type == TX_INCOME else "01.1"
    camt.commit(ctx, st, rows, setup["chf"].id)
    assert payee_svc.find(ctx, "Migros").alias_list == ["migros zurich hb"]
    names = {p.name for p in payee_svc.all_payees(ctx)}
    assert names == {"Migros", "Firma AG", "Swisscom", "EWZ", "Spende Sammelauftrag"}
