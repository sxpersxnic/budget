"""Automatic categorization (F07) in the interface: the form's suggestion, the import preview, the rule sheet
("Always Categorize Like This…") and the Categorization Rules page."""

from datetime import date

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_bulk_edit_ui import select
from test_camt import CAMT053
from test_import_page import field, import_button, rows, settle, upload
from test_payees_page import newest

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Rule
from budget_app.services import accounts as acc_svc
from budget_app.services import rules as rules_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.rules import RuleInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.selects import PayeeInput

UBS = (
    b"Abschlussdatum;Buchungstext;Belastung;Gutschrift\n"
    b"02.10.2026;Einkauf MIGROS ZUERICH HB 1234;45.80;\n"
    b"03.10.2026;TWINT *Brezelkoenig Bern;4.80;\n"
    b"04.10.2026;Papeterie Zumstein;3.50;\n"
)


def put(user: User, element, value) -> None:
    with user:
        element.value = value


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def button(user: User, text: str) -> ui.button:
    return newest(user, ui.button, text=text)


def spend(setup, payee: str, category: str = "01.1", **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        amount=20.0,
        currency="CHF",
        category=category,
        from_account=setup["chf"].id,
        payee=payee,
    )
    return TransactionInput(**(base | kw))


def summary(user: User) -> str:
    return next(e.text for e in user.find(kind=ui.label).elements if "Duplicates and unreadable" in e.text)


def edit_cell(user: User, index: int, column: str, value) -> None:
    [grid] = user.find(kind=ui.aggrid).elements
    UserInteraction(user, {grid}, None).trigger(
        "cellValueChanged", {"data": {"id": str(index)}, "colId": column, "newValue": value}
    )


# ------------------------------------------------------------------ the form
async def test_the_form_suggests_a_category_and_fills_it_only_when_clicked(user: User, ctx, setup) -> None:
    tx_svc.save(ctx, spend(setup, "Coop"))
    tx_svc.save(ctx, spend(setup, "Coop"))
    await user.open("/add-transaction")
    put(user, newest(user, ui.number, "Amount"), 12.4)
    payee = newest(user, PayeeInput, "Payee")
    put(user, payee, "coop")
    await user.should_see("Used 2 times for this payee · Bank (CHF)")
    category, account = newest(user, ui.select, "Category"), newest(user, ui.select, "From Account")
    assert category.value is None and account.value is None  # shown, never filled in on its own

    click(user, button(user, "Use 01.1 Groceries"))
    assert (category.value, account.value) == ("01.1", setup["chf"].id)
    await user.should_not_see("Used 2 times for this payee")
    click(user, button(user, "Add Transaction"))
    assert notified(user, "Transaction added")
    assert ctx.store.rules.all() == []  # confirming a suggestion creates no rule

    # after saving, the form knows the new transaction too, and a chosen account stays
    put(user, payee, "Coop")
    await user.should_see("Used 3 times for this payee")
    put(user, account, setup["chf"].id)
    await user.should_see("Used 3 times for this payee")
    await user.should_not_see("Used 3 times for this payee · Bank (CHF)")
    put(user, payee, "Unknown Shop")
    await user.should_not_see("Used 3 times for this payee")


async def test_a_rule_suggests_income_and_its_account(user: User, ctx, setup) -> None:
    rules_svc.save(ctx, RuleInput(match="muster", type=TX_INCOME, category=setup["salary"].id, account=setup["eur"].id))
    await user.open("/add-transaction")
    put(user, newest(user, ui.toggle), "income")
    put(user, newest(user, PayeeInput, "Payee"), "Muster AG")
    await user.should_see("Rule: contains “muster”")  # the EUR account doesn't fit the CHF amount
    await user.should_not_see("Rule: contains “muster” · Euro account (EUR)")
    put(user, newest(user, ui.select, "Currency"), "EUR")
    await user.should_see("Rule: contains “muster” · Euro account (EUR)")
    click(user, button(user, "Use Salary"))
    assert newest(user, ui.select, "Income category").value == setup["salary"].id
    assert newest(user, ui.select, "To Account").value == setup["eur"].id


async def test_a_rule_can_match_the_note(user: User, ctx, setup) -> None:
    rules_svc.save(ctx, RuleInput(match="Wocheneinkauf", category="01.1"))
    await user.open("/add-transaction")
    put(user, newest(user, PayeeInput, "Payee"), "Market Stall")
    await user.should_not_see("Use 01.1 Groceries")
    put(user, newest(user, ui.textarea, "Note"), "wocheneinkauf am Samstag")
    await user.should_see("Rule: contains “Wocheneinkauf”")


async def test_the_edit_sheet_offers_always_categorize_like_this(user: User, ctx, setup) -> None:
    tx = tx_svc.save(ctx, spend(setup, "Migros", "01.1", comment="Wocheneinkauf"))
    await user.open("/transactions")
    await select(user, tx.id)
    click(user, button(user, "Edit"))
    await user.should_see("Edit Transaction")
    await user.should_not_see("Use 01.1 Groceries")  # editing shows no suggestions
    form_category, make_rule = newest(user, ui.select, "Category"), button(user, "Always Categorize Like This…")
    click(user, make_rule)
    await user.should_see("Always Categorize Like This")
    assert newest(user, ui.input, "Text contains").value == "Migros"
    assert newest(user, ui.select, "Category").value == "01.1"
    assert newest(user, ui.select, "Account").value is None  # empty unless chosen
    put(user, newest(user, ui.input, "Text contains"), "migros city")
    click(user, button(user, "Save Rule"))
    assert notified(user, "Saved the rule for “migros city”")
    [rule] = ctx.store.rules.all()
    assert (rule.match, rule.type, rule.category, rule.account) == ("migros city", TX_EXPENDITURE, "01.1", "")
    assert undo_svc.label(ctx, undo_svc.latest(ctx)) == "Save a categorization rule"

    # the button follows the form: not for a transfer, nor without a category
    put(user, form_category, None)
    assert not make_rule.visible


# ------------------------------------------------------------------ the Transactions page
async def test_the_selection_bar_makes_a_rule_and_warns_about_the_same_text(user: User, ctx, setup) -> None:
    coop = tx_svc.save(ctx, spend(setup, "Coop", "05.6"))
    plain = tx_svc.save(ctx, spend(setup, "", "07.3", comment="Einkauf Debitkarte SBB MOBILE 4711"))
    old = rules_svc.save(ctx, RuleInput(match="COOP", category="01.1"))
    await user.open("/transactions")

    await select(user, coop.id, plain.id)
    click(user, button(user, "Always Categorize Like This…"))
    await settle()
    assert notified(user, "Select exactly one transaction to categorize like it")

    await select(user, coop.id)
    click(user, button(user, "Always Categorize Like This…"))
    await user.should_see("A rule for “COOP” already exists (01.1 Groceries). Saving replaces it.")
    assert button(user, "Replace Rule").enabled
    put(user, newest(user, ui.input, "Text contains"), "Coop Pronto")
    await user.should_not_see("Saving replaces it.")
    assert button(user, "Save Rule").enabled
    put(user, newest(user, ui.input, "Text contains"), "coop")
    click(user, button(user, "Replace Rule"))
    assert notified(user, "Saved the rule for “coop”")
    [rule] = ctx.store.rules.all()
    assert (rule.id, rule.match, rule.category) == (old.id, "coop", "05.6")

    # without a payee, the name in the note
    await select(user, plain.id)
    click(user, button(user, "Always Categorize Like This…"))
    await settle()
    assert newest(user, ui.input, "Text contains").value == "SBB"  # the brand named in the bank text


async def test_only_expenditure_and_income_with_a_category_make_a_rule(user: User, ctx, setup) -> None:
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    transfer = tx_svc.save(ctx, spend(setup, "", "", type="transfer", to_account=savings.id))
    await user.open("/transactions")
    await select(user, transfer.id)
    click(user, button(user, "Always Categorize Like This…"))
    await settle()
    assert notified(user, "Choose an expenditure or income that has a category")


# ------------------------------------------------------------------ the rule sheet's checks
async def test_the_rule_sheet_checks_its_fields(user: User, ctx, setup) -> None:
    await user.open("/rules")
    await user.should_see("Categorization Rules")
    click(user, button(user, "New Rule"))
    await user.should_see("New Rule")
    save = button(user, "Save Rule")
    assert not save.enabled
    put(user, newest(user, ui.input, "Text contains"), "x")
    await user.should_see("The text needs at least two characters")
    put(user, newest(user, ui.input, "Text contains"), "Lohn")
    await user.should_see("Choose a category")
    put(user, newest(user, ui.toggle), TX_INCOME)
    await user.should_see("Optional. Suggested as the To account when a transaction has none yet.")
    put(user, newest(user, ui.select, "Income category"), setup["salary"].id)
    put(user, newest(user, ui.select, "Account"), setup["chf"].id)
    assert save.enabled
    UserInteraction(user, {newest(user, ui.input, "Text contains")}, None).trigger("keydown.enter")
    assert notified(user, "Saved the rule for “Lohn”")
    [rule] = ctx.store.rules.all()
    assert (rule.type, rule.category, rule.account) == (TX_INCOME, setup["salary"].id, setup["chf"].id)
    await user.should_see("Salary · Bank (CHF)")


# ------------------------------------------------------------------ the Rules page
@pytest.fixture
def three_rules(ctx, setup):
    return [
        rules_svc.save(ctx, RuleInput(match="Migros", category="01.1", account=setup["chf"].id)),
        rules_svc.save(ctx, RuleInput(match="SBB", category="07.3")),
        rules_svc.save(ctx, RuleInput(match="Lohn", type=TX_INCOME, category=setup["salary"].id)),
    ]


async def test_the_rules_page_lists_edits_and_deletes(user: User, ctx, setup, three_rules) -> None:
    migros, sbb, _ = three_rules
    await user.open("/rules")
    await user.should_see("Contains “Migros”")
    await user.should_see("01.1 Groceries · Bank (CHF)")
    await user.should_see("Contains “Lohn”")
    await user.should_see("3 rules")
    active = {link.props["href"] for link in user.find(kind=ui.link).elements if "active" in link.classes}
    assert active == {"/settings", "/payees"}  # Settings in the sidebar, its Payees tab

    edit = min((b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "o_edit"), key=lambda b: b.id)
    click(user, edit)
    await user.should_see("Edit Rule")
    assert newest(user, ui.input, "Text contains").value == "Migros"
    put(user, newest(user, ui.input, "Text contains"), "SBB")  # the other rule's text
    await user.should_see("A rule for “SBB” already exists (07.3 Public transport). Saving replaces it.")
    put(user, newest(user, ui.input, "Text contains"), "Migros Bank")
    click(user, button(user, "Save Rule"))
    assert notified(user, "Saved the rule for “Migros Bank”")
    await user.should_see("Contains “Migros Bank”")
    assert ctx.store.rules.get(migros.id).match == "Migros Bank"

    delete = [b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "o_delete"]
    click(user, min(delete, key=lambda b: b.id))  # the first row: Migros Bank
    await user.should_see("Delete the rule for “Migros Bank”?")
    click(user, button(user, "Delete"))
    assert notified(user, "Deleted the rule for “Migros Bank”")
    await user.should_not_see("Contains “Migros Bank”")
    assert [r.id for r in ctx.store.rules.all()] == [sbb.id, three_rules[2].id]


async def test_saving_a_rule_deleted_meanwhile_is_refused(user: User, ctx, setup, three_rules) -> None:
    await user.open("/rules")
    edit = min((b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "o_edit"), key=lambda b: b.id)
    click(user, edit)
    rules_svc.delete(ctx, [three_rules[0].id])  # in another window
    click(user, button(user, "Save Rule"))
    assert notified(user, "Rule not found")


async def test_the_rules_page_adds_per_type_and_says_when_empty(user: User, ctx, setup) -> None:
    await user.open("/rules")
    await user.should_see("No rules yet.")
    add_income = max((b for b in user.find(kind=ui.button).elements if b.text == "Add Rule…"), key=lambda b: b.id)
    click(user, add_income)
    await user.should_see("New Rule")
    assert newest(user, ui.toggle).value == TX_INCOME


async def test_a_rule_whose_category_is_gone_says_so(user: User, ctx, setup) -> None:
    ctx.store.rules.add(Rule(match="Bonus", type=TX_INCOME, category="gone", account="gone"))
    await user.open("/rules")
    await user.should_see("Category no longer exists · Account no longer exists")


async def test_payees_lead_to_the_rules(user: User, ctx, setup) -> None:
    await user.open("/payees")
    click(user, button(user, "Rules…"))
    await user.should_see("Categorization Rules")
    await user.should_see("A rule suggests its category for new and imported transactions")


# ------------------------------------------------------------------ the import preview
async def test_the_import_preview_marks_suggestions_until_they_are_changed(user: User, ctx, setup) -> None:
    rules_svc.save(ctx, RuleInput(match="migros", category="01.1"))
    tx_svc.save(ctx, spend(setup, "Brezelkönig", "11.1"))
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await settle()
    assert [(r["payee"], r["category"], r["suggested"], r["reason"]) for r in rows(user)] == [
        ("Migros", "01.1 Groceries", True, "Rule: contains “migros”"),
        ("Brezelkönig", "11.1 Eating out", True, "Used 1 time for this payee"),
        ("Papeterie Zumstein", "", False, ""),
    ]
    [grid] = user.find(kind=ui.aggrid).elements
    category = next(c for c in grid.options["columnDefs"] if c["field"] == "category")
    assert category["cellClassRules"] == {"suggested": "data.suggested"}  # in italics
    assert "2 categories are suggestions (in italics): check them before importing." in summary(user)
    clear = button(user, "Clear Suggestions")
    assert clear.visible

    # chosen by hand: no longer a suggestion
    await select(user, "1")
    put(user, field(user, ui.select, "Category"), "11.1.1")
    click(user, button(user, "Apply"))
    await settle()
    assert [(r["suggested"], r["reason"]) for r in rows(user)][:2] == [(True, "Rule: contains “migros”"), (False, "")]
    assert "1 category is a suggestion (in italics): check it before importing." in summary(user)

    # a new payee brings its own suggestion; the one chosen by hand stays
    edit_cell(user, 2, "payee", "Migros")
    edit_cell(user, 1, "payee", "Migros")
    assert [(r["category"], r["suggested"]) for r in rows(user)] == [
        ("01.1 Groceries", True),
        ("11.1.1 Restaurants and cafés", False),
        ("01.1 Groceries", True),
    ]
    edit_cell(user, 2, "payee", "Somebody Else")
    assert rows(user)[2]["category"] == "" and not rows(user)[2]["suggested"]

    click(user, clear)
    assert [(r["category"], r["suggested"]) for r in rows(user)] == [
        ("", False),
        ("11.1.1 Restaurants and cafés", False),
        ("", False),
    ]
    assert not clear.visible
    assert "suggestion" not in summary(user)


async def test_importing_keeps_the_suggested_categories(user: User, ctx, setup) -> None:
    rules_svc.save(ctx, RuleInput(match="migros", category="01.1"))
    tx_svc.save(ctx, spend(setup, "Brezelkönig", "11.1"))
    tx_svc.save(ctx, spend(setup, "Papeterie Zumstein", "09.7"))
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await settle()
    assert import_button(user).enabled
    click(user, import_button(user))
    assert notified(user, "Imported 3 transactions")
    imported = sorted((tx for tx in ctx.store.transactions.all() if tx.import_ref), key=lambda tx: tx.date)
    assert [tx.category for tx in imported] == ["01.1", "11.1", "09.7"]
    assert len(ctx.store.rules.all()) == 1


async def test_a_camt_statement_gets_the_same_suggestions(user: User, ctx, setup) -> None:
    rules_svc.save(ctx, RuleInput(match="migros", category="01.1"))
    rules_svc.save(ctx, RuleInput(match="lohn", type=TX_INCOME, category=setup["salary"].id))
    await user.open("/import")
    [element] = user.find(kind=ui.upload).elements
    await element.handle_uploads([SmallFileUpload("september.xml", "text/xml", CAMT053)])
    await settle()
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await settle()
    assert [(r["category"], r["reason"]) for r in rows(user)][:2] == [
        ("01.1 Groceries", "Rule: contains “migros”"),
        ("Income · Salary", "Rule: contains “lohn”"),
    ]
