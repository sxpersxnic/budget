"""Automatic categorization (F07): rules, learning from the payee's history, and the suggestions on import."""

import json
import time
from datetime import date

import pytest

from budget_app.domain import rules as rules_domain
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Rule, Transaction
from budget_app.services import categorize, importer
from budget_app.services import export as export_svc
from budget_app.services import rules as rules_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.rules import RuleInput
from budget_app.services.transactions import TransactionInput

UBS = (
    b"Abschlussdatum;Buchungstext;Belastung;Gutschrift\n"
    b"02.10.2026;Einkauf MIGROS ZUERICH HB 1234;45.80;\n"
    b"03.10.2026;TWINT *Brezelkoenig Bern;4.80;\n"
    b"25.10.2026;Lohn Muster AG;;7'800.00\n"
)


def spend(setup, payee: str = "", category: str = "01.1", **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        amount=20.0,
        currency="CHF",
        category=category,
        from_account=setup["chf"].id,
        payee=payee,
    )
    return TransactionInput(**(base | kw))


def rule(match: str, category: str = "01.1", type_: str = TX_EXPENDITURE, account: str = "") -> Rule:
    return Rule(match=match, type=type_, category=category, account=account)


# ------------------------------------------------------------------ which rule applies
def test_a_rule_matches_payee_or_text_ignoring_case_and_accents():
    migros = rule("Migros")
    assert rules_domain.best([migros], TX_EXPENDITURE, "MIGROS", "") is migros
    assert rules_domain.best([migros], TX_EXPENDITURE, "", "Einkauf migros Zürich HB") is migros
    cafe = rule("Café  Fédéral")
    assert rules_domain.best([cafe], TX_EXPENDITURE, "cafe federal Bern", "") is cafe  # accents, spaces
    assert rules_domain.best([migros], TX_INCOME, "Migros", "") is None  # another type
    assert rules_domain.best([migros], TX_EXPENDITURE, "Coop", "Coop Pronto") is None
    assert rules_domain.best([rule("  ")], TX_EXPENDITURE, "anything", "") is None


def test_the_longest_text_wins_and_ties_go_to_the_newest():
    short, long_ = rule("migros", "01.1"), rule("migros restaurant", "11.1")
    assert rules_domain.best([long_, short], TX_EXPENDITURE, "Migros Restaurant Bern", "") is long_
    assert rules_domain.best([short, long_], TX_EXPENDITURE, "Migros Restaurant Bern", "") is long_
    older, newer = rule("coop", "01.1"), rule("COOP", "05.6")
    assert rules_domain.best([older, newer], TX_EXPENDITURE, "Coop", "") is newer
    assert [r.match for r in rules_domain.by_priority([older, short, long_])] == ["migros restaurant", "migros", "coop"]


# ------------------------------------------------------------------ rules
def test_rules_are_saved_checked_and_replaced(ctx, setup):
    saved = rules_svc.save(ctx, RuleInput(match="  Migros   City ", category="01.1"))
    assert (saved.match, saved.type, saved.category, saved.account) == ("Migros City", TX_EXPENDITURE, "01.1", "")
    assert ctx.store.rules.get(saved.id) is not None
    for bad, message in (
        (RuleInput(match=" "), "Enter the text"),
        (RuleInput(match="m", category="01.1"), "at least two characters"),
        (RuleInput(match="Migros", type="transfer", category="01.1"), "expenditure or income"),
        (RuleInput(match="Migros", category="99.9"), "Choose a category"),
        (RuleInput(match="Migros", category="ADV"), "Choose a category"),
        (RuleInput(match="Lohn", type=TX_INCOME, category="01.1"), "Choose a category"),
        (RuleInput(match="Migros", category="01.1", account="nope"), "Account not found"),
    ):
        with pytest.raises(ValidationError, match=message):
            rules_svc.save(ctx, bad)

    # the same text and type again: refused, unless it replaces the rule
    same = RuleInput(match="MIGROS CITY", category="TAX", account=setup["chf"].id)
    assert rules_svc.same_text(ctx, same.match, TX_EXPENDITURE) == saved
    assert rules_svc.same_text(ctx, same.match, TX_INCOME) is None
    with pytest.raises(ValidationError, match="already exists"):
        rules_svc.save(ctx, same)
    replaced = rules_svc.save(ctx, same, replace=True)
    assert replaced.id == saved.id and (replaced.match, replaced.category) == ("MIGROS CITY", "TAX")
    assert len(ctx.store.rules.all()) == 1

    # editing one rule into another's text replaces that one and removes the edited rule
    coop = rules_svc.save(ctx, RuleInput(match="Coop", category="01.1"))
    edited = rules_svc.save(ctx, RuleInput(match="migros city", category="07.3"), coop.id, replace=True)
    assert edited.id == saved.id and [r.id for r in ctx.store.rules.all()] == [saved.id]
    with pytest.raises(ValidationError, match="Rule not found"):
        rules_svc.save(ctx, RuleInput(match="Coop", category="01.1"), "gone")

    # editing keeps the id
    again = rules_svc.save(ctx, RuleInput(match="Migros", type=TX_INCOME, category=setup["salary"].id), saved.id)
    assert again.id == saved.id and again.type == TX_INCOME
    assert rules_svc.delete(ctx, [saved.id, "unknown"]) == 1
    assert ctx.store.rules.all() == []


def test_rule_changes_are_undoable_and_named_in_the_history(ctx, setup):
    saved = rules_svc.save(ctx, RuleInput(match="Migros", category="01.1"))
    action = undo_svc.latest(ctx)
    assert undo_svc.label(ctx, action) == "Save a categorization rule"
    assert undo_svc.describe_change(ctx, action.changes[0]) == "Categorization rule added: Migros · 01.1"
    rules_svc.delete(ctx, [saved.id])
    assert undo_svc.label(ctx, undo_svc.latest(ctx)) == "Delete categorization rules"
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert [r.match for r in ctx.store.rules.all()] == ["Migros"]
    assert "rules" in ctx.store.tables()
    assert json.loads(export_svc.everything_json(ctx))["rules"][0]["match"] == "Migros"


def test_a_rule_starts_from_the_payee_or_the_name_in_the_note(ctx, setup):
    with_payee = tx_svc.save(ctx, spend(setup, "Migros", comment="Einkauf"))
    assert rules_svc.from_transaction(with_payee) == RuleInput("Migros", TX_EXPENDITURE, "01.1", "")
    without = tx_svc.save(ctx, spend(setup, comment="Einkauf Debitkarte 12.10.2026 COOP PRONTO BERN 1234"))
    assert rules_svc.from_transaction(without).match == "Coop Pronto"


def test_rules_are_listed_by_type_and_text_and_labelled(ctx, setup):
    rules_svc.save(ctx, RuleInput(match="Lohn", type=TX_INCOME, category=setup["salary"].id))
    rules_svc.save(ctx, RuleInput(match="SBB", category="07.3"))
    rules_svc.save(ctx, RuleInput(match="Coop", category="01.1"))
    assert [r.match for r in rules_svc.all_rules(ctx)] == ["Coop", "SBB", "Lohn"]
    assert rules_svc.category_label(ctx, TX_EXPENDITURE, "01.1") == "Groceries · 01.1"
    assert rules_svc.category_label(ctx, TX_INCOME, setup["salary"].id) == "Salary"
    assert rules_svc.category_label(ctx, TX_INCOME, "gone") == ""


# ------------------------------------------------------------------ suggestions
def test_a_rule_comes_before_the_history(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Migros", "01.1"))
    rules_svc.save(ctx, RuleInput(match="migros", category="05.6", account=setup["chf"].id))
    hit = categorize.suggest(ctx, TX_EXPENDITURE, "Migros", "")
    assert (hit.category, hit.account, hit.source) == ("05.6", setup["chf"].id, categorize.RULE)
    assert hit.reason == "Rule: contains “migros”"
    # the account is the caller's when it gives one
    assert categorize.suggest(ctx, TX_EXPENDITURE, "Migros", "", account=setup["eur"].id).account == ""
    # the bank text is enough
    assert categorize.suggest(ctx, TX_EXPENDITURE, "", "Einkauf MIGROS Bern").category == "05.6"


def test_the_history_suggests_the_most_frequent_category_then_the_latest(ctx, setup):
    for day, code in ((1, "01.1"), (2, "01.1"), (3, "05.6")):
        tx_svc.save(ctx, spend(setup, "Coop", code, date=date(2026, 9, day)))
    hit = categorize.suggest(ctx, TX_EXPENDITURE, "COOP", "")
    assert (hit.category, hit.source, hit.reason) == ("01.1", categorize.PAYEE, "Used 2 times for this payee")
    assert hit.account == setup["chf"].id  # the account the payee had most often
    tx_svc.save(ctx, spend(setup, "Coop", "05.6", date=date(2026, 9, 4)))  # 2 : 2, the latest wins
    assert categorize.suggest(ctx, TX_EXPENDITURE, "Coop", "").category == "05.6"
    tx_svc.save(ctx, spend(setup, "Kiosk", "02.2", date=date(2026, 9, 5)))
    assert categorize.suggest(ctx, TX_EXPENDITURE, "Kiosk", "").reason == "Used 1 time for this payee"
    # another type, an unknown payee, a transfer
    assert categorize.suggest(ctx, TX_INCOME, "Coop", "") is None
    assert categorize.suggest(ctx, TX_EXPENDITURE, "Migros", "") is None
    assert categorize.suggest(ctx, "transfer", "Coop", "") is None


def test_income_learns_the_to_account_and_repayments_are_ignored(ctx, setup):
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=6200,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            payee="Muster AG",
        ),
    )
    repayment = Transaction(
        date=date(2026, 9, 26),
        type=TX_INCOME,
        amount=50,
        currency="CHF",
        amount_chf=50,
        fx_date=date(2026, 9, 26),
        to_account=setup["eur"].id,
        is_advance=True,
        payee="Muster AG",
    )
    ctx.store.transactions.add(repayment)  # no category of its own
    hit = categorize.suggest(ctx, TX_INCOME, "muster ag", "")
    assert (hit.category, hit.account) == (setup["salary"].id, setup["chf"].id)
    assert hit.reason == "Used 1 time for this payee"


def test_rows_without_payee_learn_from_the_same_text(ctx, setup):
    tx_svc.save(ctx, spend(setup, category="07.3", comment="SBB Mobile 4711"))
    hit = categorize.suggest(ctx, TX_EXPENDITURE, "", "sbb mobile 0815")
    assert (hit.category, hit.source, hit.account) == ("07.3", categorize.TEXT, "")
    assert hit.reason == "Used 1 time for this text"
    assert categorize.suggest(ctx, TX_EXPENDITURE, "", "1234") is None


def test_rules_and_accounts_that_no_longer_fit_are_left_out(ctx, setup):
    ctx.store.rules.add(rule("Lohn", "deleted-category", TX_INCOME))
    ctx.store.rules.add(rule("Coop", "01.1", account="deleted-account"))
    assert categorize.suggest(ctx, TX_INCOME, "Lohn", "") is None
    hit = categorize.suggest(ctx, TX_EXPENDITURE, "Coop", "")
    assert (hit.category, hit.account) == ("01.1", "")


def test_suggesting_writes_nothing(ctx, setup):
    tx_svc.save(ctx, spend(setup, "Coop"))
    rules_svc.save(ctx, RuleInput(match="Migros", category="01.1"))
    actions, payees = len(undo_svc.recent(ctx)), len(ctx.store.payees.all())
    suggester = categorize.Suggester(ctx)
    assert suggester.suggest(TX_EXPENDITURE, "Brand New Shop", "Migros Bern").category == "01.1"
    assert suggester.suggest(TX_EXPENDITURE, "Coop", "").category == "01.1"
    assert len(undo_svc.recent(ctx)) == actions and len(ctx.store.payees.all()) == payees


def test_a_few_thousand_transactions_stay_fast(ctx, setup):
    """The index is built once per import or form; each suggestion is then a dictionary lookup."""
    ctx.store.transactions.add_many(
        Transaction(
            date=date(2026, 1 + i % 9, 1 + i % 28),
            type=TX_EXPENDITURE,
            amount=10,
            currency="CHF",
            amount_chf=10,
            fx_date=date(2026, 1, 1),
            category="01.1" if i % 3 else "05.6",
            from_account=setup["chf"].id,
            payee=f"Shop {i % 300}",
        )
        for i in range(4000)
    )
    ctx.store.rules.add_many(rule(f"brand {i}", "09.4") for i in range(50))
    start = time.perf_counter()
    suggester = categorize.Suggester(ctx)
    hits = [suggester.suggest(TX_EXPENDITURE, f"Shop {i % 400}", f"Einkauf Shop {i}") for i in range(1000)]
    assert time.perf_counter() - start < 2  # about 0.1 s on a laptop
    assert sum(1 for h in hits if h is not None) == 800  # shops 300 to 399 are new


# ------------------------------------------------------------------ import
def test_the_import_suggests_rules_and_history_and_can_clear_them(ctx, setup):
    rules_svc.save(ctx, RuleInput(match="migros", category="01.1", account=setup["eur"].id))
    rules_svc.save(ctx, RuleInput(match="lohn", type=TX_INCOME, category=setup["salary"].id))
    tx_svc.save(ctx, spend(setup, "Brezelkönig", "11.1"))
    parsed = importer.read_file("ubs.csv", UBS)
    rows = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), setup["chf"].id)
    assert [(r.payee, r.category, r.suggested) for r in rows] == [
        ("Migros", "01.1", True),
        ("Brezelkönig", "11.1", True),
        ("Muster", setup["salary"].id, True),
    ]
    assert [r.reason for r in rows] == [
        "Rule: contains “migros”",
        "Used 1 time for this payee",
        "Rule: contains “lohn”",
    ]
    # importing keeps what the preview shows; the rule's account does not move the file's rows
    saved = importer.commit(ctx, rows, setup["chf"].id)
    assert [(tx.category, tx.from_account or tx.to_account) for tx in saved] == [
        ("01.1", setup["chf"].id),
        ("11.1", setup["chf"].id),
        (setup["salary"].id, setup["chf"].id),
    ]
    assert len(ctx.store.rules.all()) == 2  # confirming a suggestion creates no rule

    again = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), setup["chf"].id)
    assert importer.clear_suggestions(again) == 3
    assert [(r.category, r.suggested, r.reason) for r in again] == [("", False, "")] * 3
    assert importer.clear_suggestions(again) == 0


def test_a_category_given_by_the_file_is_not_a_suggestion(ctx, setup):
    rules_svc.save(ctx, RuleInput(match="migros", category="01.1"))
    row = importer.ImportRow(index=0, date=date(2026, 10, 2), amount=-5.0, text="Migros", category="05.6")
    [kept] = importer.analyse(ctx, [row], setup["chf"].id)
    assert (kept.category, kept.suggested) == ("05.6", False)
