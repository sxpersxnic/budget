"""Swiss tax summary (F20): the sections and their codes, what counts where (categories, split lines, tags, pillar 3a
transfers, dividends), the tags linked to sections, and the PDF, CSV and ZIP exports."""

import csv
import io
import json
import zipfile
from datetime import date

import pypdfium2 as pdfium
import pytest

from budget_app.domain import tax as tax_dom
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import ASSET_PILLAR3A, ASSET_SECURITIES, TRADE_DIVIDEND
from budget_app.services import accounts as acc_svc
from budget_app.services import attachments as att_svc
from budget_app.services import export as export_svc
from budget_app.services import investments as inv_svc
from budget_app.services import items as items_svc
from budget_app.services import tags as tag_svc
from budget_app.services import tax as tax_svc
from budget_app.services import tax_export
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.investments import TradeInput
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
YEAR = 2026


def spend(ctx, setup, amount, category, day=date(2026, 3, 10), **kw):
    base = dict(
        date=day, type="expenditure", amount=amount, currency="CHF", category=category, from_account=setup["chf"].id
    )
    return tx_svc.save(ctx, TransactionInput(**(base | kw)))


def receive(ctx, setup, amount, category, day=date(2026, 3, 20), **kw):
    base = dict(date=day, type="income", amount=amount, currency="CHF", category=category, to_account=setup["chf"].id)
    return tx_svc.save(ctx, TransactionInput(**(base | kw)))


def amounts(summary, key):
    return [e.amount for e in summary.section(key).entries]


# ------------------------------------------------------------------ the sections
def test_the_code_lists_never_overlap_and_name_real_categories():
    coicop = get_coicop()
    for s in tax_dom.SECTIONS:
        assert all(coicop.get(c) is not None for c in s.all_codes), s.key
    for code in coicop.categories:
        assert sum(s.covers(code) for s in tax_dom.SECTIONS) <= 1, code
    assert [s.key for s in tax_dom.SECTIONS if s.rule] == ["pillar3a", "withholding", "securities"]


@pytest.mark.parametrize(
    ("code", "key"),
    [
        ("07.3", "professional"),  # public transport as such
        ("07.3.1.1", "professional"),  # the train
        ("07.3.5", "professional"),  # a fare network pass
        ("07.3.3.2", None),  # flights are not commuting
        ("07.3.2.2", None),  # nor are taxis
        ("11.1.2", "professional"),
        ("11.1.2.1", None),  # school canteens
        ("11.1.1", None),  # restaurants
        ("10.5", "professional"),
        ("10.2", None),  # school fees of children
        ("12.1", "insurance"),
        ("12.1.2.0", "insurance"),
        ("12.1.3", None),  # household insurance
        ("06", "medical"),
        ("06.2.2.9", "medical"),
        ("14", "donations"),
        ("14.5.0.0", "donations"),
        ("14.3", None),  # sports clubs
        ("13.3.0.1", "childcare"),
        ("13.3.0.2", None),
        ("12.2.1", "debt_interest"),
        ("01.1", None),
        ("TAX", None),
    ],
)
def test_which_section_a_category_belongs_to(code, key):
    found = tax_dom.section_of_code(code)
    assert (found.key if found else None) == key


def test_years_and_the_default_year(ctx, setup):
    spend(ctx, setup, 10, "06", day=date(2023, 5, 1))
    assert tax_svc.default_year(date(2026, 10, 22)) == 2025
    assert tax_svc.years(ctx, date(2026, 10, 22)) == [2026, 2025, 2023]


# ------------------------------------------------------------------ what counts
def test_categories_count_in_chf_by_year(ctx, setup):
    doctor = spend(ctx, setup, 200, "06.2.3", payee="Dr. Muster", comment="Check-up")
    spend(ctx, setup, 50, "06.1.1", day=date(2025, 12, 30))  # another year
    spend(ctx, setup, 30, "01.1")  # food: no section
    euros = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 30),
            type="expenditure",
            amount=100,
            currency="EUR",
            category="12.1.2",
            from_account=setup["eur"].id,
        ),
    )
    s = tax_svc.summary(ctx, YEAR)
    assert amounts(s, "medical") == [200]
    entry = s.section("medical").entries[0]
    assert (entry.payee, entry.note, entry.transaction_id, entry.tags) == ("Dr. Muster", "Check-up", doctor.id, [])
    assert entry.category == get_coicop().label("06.2.3")
    assert amounts(s, "insurance") == [round(euros.amount_chf, 2)]
    assert s.section("insurance").total == round(euros.amount_chf, 2) != 100
    assert tax_svc.summary(ctx, 2025).section("medical").total == 50
    assert s.overlapping == 0 and not s.empty
    assert tax_svc.summary(ctx, 2020).empty


def test_split_lines_count_in_their_own_category_and_advances_stay_out(ctx, setup):
    shop = spend(ctx, setup, 100, "01.1", payee="Migros")
    items_svc.replace(
        ctx,
        shop.id,
        [ItemInput("Bread", 1, 20), ItemInput("Plaster", 2, 15, "06.1.2"), ItemInput("Pills", 1, 10, "06")],
    )
    shared = spend(
        ctx,
        setup,
        300,
        "06.2",
        day=date(2026, 3, 11),
        is_advance=True,
        advance_mode="amount",
        advance_value=100,
        debtor="Mia",
    )
    s = tax_svc.summary(ctx, YEAR)
    rows = s.section("medical").entries
    assert [(r.transaction_id, r.amount) for r in rows] == [(shop.id, 40), (shared.id, 200)]
    assert rows[0].category == f"{get_coicop().label('06.1.2')}, {get_coicop().label('06')}"


def test_tags_put_rows_in_sections_and_sections_can_overlap(ctx, setup):
    work = tag_svc.add(ctx, "Work")
    refund = tag_svc.add(ctx, "Refund")
    interest = tag_svc.add(ctx, "Loan")
    tax_svc.set_links(ctx, {"professional": [work.id], "medical": [refund.id], "debt_interest": [interest.id]})
    bag = spend(ctx, setup, 80, "13.2.9.1", day=date(2026, 3, 1), tags=[work.id])
    both = spend(ctx, setup, 60, "06.1.3.1", day=date(2026, 3, 2), tags=[work.id], payee="Optiker")  # glasses
    shop = spend(ctx, setup, 50, "01.1", day=date(2026, 3, 3))
    items_svc.replace(ctx, shop.id, [ItemInput("Pen", 1, 12, tags=[work.id]), ItemInput("Food", 1, 38)])
    back = receive(ctx, setup, 25, setup["salary"].id, tags=[refund.id], payee="CSS")
    loan = spend(ctx, setup, 40, "12.2.9.9", tags=[interest.id])
    s = tax_svc.summary(ctx, YEAR)
    professional = s.section("professional").entries
    assert [(e.transaction_id, e.amount, e.tags) for e in professional] == [
        (bag.id, 80, [work.id]),
        (both.id, 60, [work.id]),
        (shop.id, 12, [work.id]),  # only the tagged line
    ]
    assert [(e.transaction_id, e.amount) for e in s.section("medical").entries] == [(both.id, 60), (back.id, -25)]
    assert s.section("medical").entries[0].tags == []  # there by its category
    assert s.section("medical").total == 35
    assert amounts(s, "debt_interest") == [40] and s.section("debt_interest").entries[0].transaction_id == loan.id
    assert s.overlapping == 1
    assert s.section("professional").tags == [work.id]


def test_repayments_exchanges_and_revaluations_never_count(ctx, setup):
    refund = tag_svc.add(ctx, "Refund")
    tax_svc.set_links(ctx, {"medical": [refund.id]})
    adv = spend(ctx, setup, 100, "06", is_advance=True, advance_mode="amount", advance_value=50, debtor="Sam")
    receive(
        ctx,
        setup,
        50,
        "",
        is_advance=True,
        advance_account=adv.advance_account,
        settles_id=adv.id,
        tags=[refund.id],
    )
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 30),
            type="exchange",
            amount=100,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=105,
            tags=[refund.id],
        ),
    )
    assert amounts(tax_svc.summary(ctx, YEAR), "medical") == [50]


def test_pillar_3a_counts_transfers_into_a_3a_account(ctx, setup):
    pillar = acc_svc.add_account(ctx, "VIAC 3a", "CHF", asset_class=ASSET_PILLAR3A)
    other = acc_svc.add_account(ctx, "Frankly 3a", "CHF", asset_class=ASSET_PILLAR3A)
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    loan = tag_svc.add(ctx, "Amortisation")
    tax_svc.set_links(ctx, {"debt_interest": [loan.id]})

    def move(src, dst, amount, **kw):
        return tx_svc.save(
            ctx,
            TransactionInput(
                date=date(2026, 6, 1),
                type="transfer",
                amount=amount,
                currency="CHF",
                from_account=src.id,
                to_account=dst.id,
                **kw,
            ),
        )

    paid = move(setup["chf"], pillar, 7258)
    move(pillar, other, 1000)  # between 3a accounts: no payment
    move(pillar, setup["chf"], 500)  # a withdrawal
    tagged = move(setup["chf"], savings, 300, tags=[loan.id])
    s = tax_svc.summary(ctx, YEAR)
    rows = s.section("pillar3a").entries
    assert [(e.transaction_id, e.amount, e.payee, e.category) for e in rows] == [
        (paid.id, 7258, "VIAC 3a", "Account transfer")
    ]
    assert [e.transaction_id for e in s.section("debt_interest").entries] == [tagged.id]


@pytest.fixture
def dividends(ctx, setup):
    """A CHF depot with Nestlé and a EUR depot with a foreign share, each paying a dividend in March."""
    chf = acc_svc.add_account(ctx, "Depot", "CHF", asset_class=ASSET_SECURITIES)
    eur = acc_svc.add_account(ctx, "EUR depot", "EUR", asset_class=ASSET_SECURITIES)
    nestle = inv_svc.add_holding(ctx, chf.id, "Nestlé", "CH0038863350")
    sap = inv_svc.add_holding(ctx, eur.id, "SAP")

    def pay(holding, cash, day, gross, tax):
        return inv_svc.save_trade(
            ctx,
            TradeInput(TRADE_DIVIDEND, holding.id, day, amount=gross, tax=tax, cash_account=cash.id, comment="Q1"),
        )

    pay(nestle, setup["chf"], date(2025, 4, 28), 50, 17.5)  # the year before
    first = pay(nestle, setup["chf"], date(2026, 4, 28), 100, 35)
    pay(sap, setup["eur"], date(2026, 5, 15), 40, 10.55)
    untaxed = pay(nestle, setup["chf"], date(2026, 6, 1), 10, 0)
    return {"first": first, "untaxed": untaxed, "nestle": nestle, "sap": sap}


def test_dividends_give_the_securities_list_and_the_reclaimable_tax(ctx, setup, dividends):
    interest = acc_svc.add_income_category(ctx, "Zinsen")  # the German name of Interest
    receive(ctx, setup, 12.30, interest.id, day=date(2026, 12, 31), payee="Bank")
    receive(ctx, setup, 5000, setup["salary"].id)
    s = tax_svc.summary(ctx, YEAR)
    rows = s.section("securities").entries
    assert [(e.payee, e.amount, e.tax) for e in rows] == [
        ("Nestlé (CH0038863350)", 100, 35),
        ("SAP", rows[1].amount, rows[1].tax),
        ("Nestlé (CH0038863350)", 10, 0),
        ("Bank", 12.30, 0),
    ]
    assert 0 < rows[1].tax < rows[1].amount  # the foreign share, in CHF
    assert rows[0].category == "Dividends" and rows[0].note == "Q1"
    booked = {tx.type: tx for tx in ctx.store.transactions.all() if tx.invest_id == dividends["first"].id}
    assert rows[0].transaction_id == booked["income"].id
    assert s.section("securities").tax_total == round(35 + rows[1].tax, 2)
    reclaim = s.section("withholding").entries
    assert [(e.payee, e.amount, e.category, e.transaction_id) for e in reclaim] == [
        ("Nestlé (CH0038863350)", 35, "Withholding tax", booked["expenditure"].id)
    ]
    assert tax_svc.summary(ctx, 2025).section("withholding").total == 17.5
    assert inv_svc.reclaimable_tax(ctx)[YEAR] == s.section("withholding").total


def test_a_dividend_income_booked_by_hand_and_tagged_spending_in_the_income_section(ctx, setup):
    dividends = acc_svc.add_income_category(ctx, "Dividendes")
    fees = tag_svc.add(ctx, "Custody")
    tax_svc.set_links(ctx, {"securities": [fees.id]})
    receive(ctx, setup, 80, dividends.id)
    spend(ctx, setup, 30, "12.2.9.9", tags=[fees.id])
    assert amounts(tax_svc.summary(ctx, YEAR), "securities") == [-30, 80]


def test_rows_carry_their_attachments(ctx, setup):
    doctor = spend(ctx, setup, 200, "06")
    att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    att_svc.add(ctx, doctor.id, "receipt.pdf", PDF)
    s = tax_svc.summary(ctx, YEAR)
    assert [a.name for a in s.section("medical").entries[0].attachments] == ["invoice.pdf", "receipt.pdf"]
    assert s.section("medical").attachments == 2


# ------------------------------------------------------------------ linking tags
def test_linking_tags_is_one_undoable_step_and_adds_new_names(ctx, setup):
    work = tag_svc.add(ctx, "Work")
    links = tax_svc.set_links(ctx, {"professional": [work.id, "Commute"], "medical": []})
    commute = tag_svc.find(ctx, "Commute")
    assert commute is not None
    assert links["professional"] == [commute.id, work.id] and links["medical"] == []
    act = undo_svc.latest(ctx)
    assert undo_svc.label(ctx, act) == "Link tags to tax sections"
    assert "Tax summary section added: Professional expenses" in [undo_svc.describe_change(ctx, c) for c in act.changes]
    # sections left out keep their tags; a section emptied loses its row's tags
    tax_svc.set_links(ctx, {"insurance": []})
    assert tax_svc.links(ctx)["professional"] == [commute.id, work.id]
    tax_svc.set_links(ctx, {"professional": [commute.id]})
    assert tax_svc.links(ctx)["professional"] == [commute.id]
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert tax_svc.links(ctx)["professional"] == [commute.id, work.id]


def test_a_tag_belongs_to_one_section_only(ctx):
    work = tag_svc.add(ctx, "Work")
    tax_svc.set_links(ctx, {"professional": [work.id]})
    with pytest.raises(ValidationError, match="“Work” can belong to one section only"):
        tax_svc.set_links(ctx, {"medical": [work.id]})
    with pytest.raises(ValidationError, match="“New” can belong to one section only"):
        tax_svc.set_links(ctx, {"medical": ["New"], "insurance": ["new"]})
    assert tag_svc.find(ctx, "New") is None  # nothing written
    with pytest.raises(ValidationError, match="Unknown section"):
        tax_svc.set_links(ctx, {"lottery": []})


def test_deleting_or_merging_a_tag_follows_into_the_sections(ctx):
    work = tag_svc.add(ctx, "Work")
    job = tag_svc.add(ctx, "Job")
    gone = tag_svc.add(ctx, "Gone")
    tax_svc.set_links(ctx, {"professional": [work.id], "medical": [job.id], "donations": [gone.id]})
    tag_svc.merge(ctx, [work.id, job.id], job.id)  # Job is now in two sections: it counts in the first
    assert tax_svc.links(ctx)["professional"] == [job.id] and tax_svc.links(ctx)["medical"] == []
    tag_svc.delete(ctx, [gone.id])
    assert ctx.store.tax_sections.get("donations").tags == ""
    # an id left behind (by an undo, say) is not shown
    row = ctx.store.tax_sections.get("medical")
    row.tags = "0123456789ab|" + job.id
    ctx.store.tax_sections.update(row)
    assert tax_svc.links(ctx)["medical"] == []
    tax_svc.set_links(ctx, {"insurance": []})  # rewrites both away
    assert ctx.store.tax_sections.get("medical").tags == ""


def test_the_links_are_in_the_json_export(ctx):
    work = tag_svc.add(ctx, "Work")
    tax_svc.set_links(ctx, {"professional": [work.id]})
    doc = json.loads(export_svc.everything_json(ctx))
    assert doc["tax_sections"] == [{"tags": work.id, "id": "professional"}]


def test_code_labels():
    assert tax_svc.code_labels(tax_dom.BY_KEY["childcare"]) == get_coicop().label("13.3.0.1")


# ------------------------------------------------------------------ exports
@pytest.fixture
def filled(ctx, setup, dividends):
    work = tag_svc.add(ctx, "Work")
    tax_svc.set_links(ctx, {"professional": [work.id]})
    doctor = spend(ctx, setup, 200, "06", payee="Dr. Muster")
    att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)  # the same name twice
    both = spend(ctx, setup, 60, "06.1.3.1", tags=[work.id], payee="=cmd|' /C calc'!A0", comment="Glasses")
    att_svc.add(ctx, both.id, "glasses: receipt?.pdf", PDF)
    return {"doctor": doctor, "both": both}


def test_csv_has_a_row_per_section_row(ctx, filled):
    data = tax_export.summary_csv(tax_svc.summary(ctx, YEAR)).decode()
    rows = list(csv.DictReader(io.StringIO(data)))
    assert list(rows[0]) == [
        "Section",
        "Date",
        "Payee",
        "Category",
        "Note",
        "Amount (CHF)",
        "Tax withheld (CHF)",
        "Proof",
    ]
    assert [r["Section"] for r in rows] == [
        "Professional expenses",
        "Medical and dental costs",
        "Medical and dental costs",
        "Withholding tax to reclaim",
        "Dividends and interest",
        "Dividends and interest",
        "Dividends and interest",
    ]
    assert rows[0]["Payee"].startswith("'=")  # formula-safe
    assert rows[1]["Proof"] == "glasses: receipt?.pdf" and rows[1]["Note"] == "Glasses"
    assert rows[2]["Proof"] == "invoice.pdf, invoice.pdf" and rows[2]["Amount (CHF)"] == "200.0"
    assert rows[4]["Tax withheld (CHF)"] == "35.0" and rows[6]["Tax withheld (CHF)"] == ""


def pdf_text(data: bytes) -> list[str]:
    doc = pdfium.PdfDocument(data)
    return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]


def test_pdf_has_a_summary_page_and_a_table_per_section(ctx, filled):
    pages = pdf_text(tax_export.summary_pdf(tax_svc.summary(ctx, YEAR), today=date(2027, 2, 1)))
    first = pages[0]
    assert "Tax summary 2026" in first and "made on 1 Feb 2027" in first
    assert "Cantonal rules differ" in first and "Sections can overlap" in first
    assert "Medical and dental costs" in first and "260.00" in first
    assert "of which tax withheld" in first
    rest = "\n".join(pages[1:])
    assert "Dr. Muster" in rest and "invoice.pdf" in rest and "Nothing in 2026" in rest
    assert "Page 2" in pages[1]
    assert "Tax withheld" in rest and "Proof" in rest


def test_zip_holds_the_pdf_the_csv_and_the_proofs_by_section(ctx, filled):
    proofs = tax_export.proofs_zip(ctx, tax_svc.summary(ctx, YEAR))
    names = zipfile.ZipFile(io.BytesIO(proofs.data)).namelist()
    assert names == [
        "Tax summary 2026.pdf",
        "Tax summary 2026.csv",
        "01 Professional expenses/2026-03-10 =cmd ' C calc'!A0 - glasses receipt.pdf",
        "04 Medical and dental costs/2026-03-10 =cmd ' C calc'!A0 - glasses receipt.pdf",
        "04 Medical and dental costs/2026-03-10 Dr. Muster - invoice.pdf",
        "04 Medical and dental costs/2026-03-10 Dr. Muster - invoice (2).pdf",
    ]
    assert proofs.files == 4 and proofs.skipped == []
    assert zipfile.ZipFile(io.BytesIO(proofs.data)).read(names[4]) == PDF
    entry = tax_svc.Entry(date(2026, 1, 2), "", "06", 1)
    assert tax_export._unique(entry, "README", set()) == "2026-01-02 - README"


def test_zip_leaves_out_files_that_fail_the_check(ctx, filled, monkeypatch):
    att = att_svc.list_for(ctx, filled["doctor"].id)[0]
    att_svc.path(ctx, att).write_bytes(b"#!/bin/sh\necho hi\n")  # planted by hand
    proofs = tax_export.proofs_zip(ctx, tax_svc.summary(ctx, YEAR))
    assert proofs.files == 3
    assert proofs.skipped == ["“invoice.pdf” isn’t opened: its content doesn’t match its type"]
    assert tax_export.skipped_message(proofs) == "1 file was left out"
    monkeypatch.setattr(tax_export, "MAX_ZIP_BYTES", 10)
    with pytest.raises(ValidationError, match="too large"):
        tax_export.proofs_zip(ctx, tax_svc.summary(ctx, YEAR))


@pytest.mark.parametrize(
    ("text", "safe"),
    [('a/b\\c:d*e?f"g<h>i|j', "a b c d e f g h i j"), ("  ..  ", "_"), ("x" * 100, "x" * 80), ("Café", "Café")],
)
def test_safe_names(text, safe):
    assert tax_export.safe_name(text) == safe


def test_file_names_and_entry_labels(ctx, setup):
    assert tax_export.file_name(2025, "zip") == "Tax summary 2025.zip"
    assert tax_svc.Entry(date(2026, 1, 1), "", "06", 1, note="Pharmacy").label == "Pharmacy"
    assert tax_export.money(-1234.5) == "-1'234.50"
