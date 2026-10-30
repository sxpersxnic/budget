"""Payees in the interface (F09): the Payees page (rename, merge, delete), the payee field of the transaction
form and the recurring sheet, the Transactions page's column, search and filter, and the import preview."""

from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_faster_entry_ui import menu_items
from test_import_page import import_button, upload
from test_transactions_page import rows as grid_rows
from test_transactions_page import show_filters

from budget_app.domain.models import Payee
from budget_app.services import payees as payee_svc
from budget_app.services import templates as templates_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.widgets.selects import PayeeInput
from budget_app.ui.widgets.transaction_form import open_transaction_sheet


# ------------------------------------------------------------------ helpers
def newest(user: User, kind: type, label: str | None = None, text: str | None = None):
    found = [
        e
        for e in user.find(kind=kind).elements
        if (label is None or e.props.get("label") == label) and (text is None or getattr(e, "text", None) == text)
    ]
    assert found, f"no {kind.__name__} {label or text!r}"
    return max(found, key=lambda e: e.id)  # closed sheets stay in the simulation: the newest is the open one


def put(user: User, element, value) -> None:
    with user:
        element.value = value


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def icon_buttons(user: User, icon: str) -> list[ui.button]:
    return sorted((b for b in user.find(kind=ui.button).elements if b.props.get("icon") == icon), key=lambda b: b.id)


def checkboxes(user: User) -> list[ui.checkbox]:
    return sorted(user.find(kind=ui.checkbox).elements, key=lambda c: c.id)


def spend(setup, payee: str, amount: float = 20.0, **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        type="expenditure",
        amount=amount,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
        payee=payee,
    )
    base.update(kw)
    return TransactionInput(**base)


def shops(ctx, setup) -> None:
    """Coop twice, Migros once, an income from Muster AG, and an unused payee."""
    tx_svc.save(ctx, spend(setup, "Coop", 30))
    tx_svc.save(ctx, spend(setup, "Coop", 12.5))
    tx_svc.save(ctx, spend(setup, "Migros", 45.2))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type="income",
            amount=100,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            payee="Muster AG",
        ),
    )
    ctx.store.payees.add(Payee(name="Unused Shop"))


# ------------------------------------------------------------------ the Payees page
async def test_the_page_lists_payees_with_counts_and_amounts(user: User, ctx, setup) -> None:
    await user.open("/payees")
    await user.should_see("No payees yet.")
    await user.should_see("Add one on a transaction, or import a bank file")  # the page's explanation, while empty
    assert ("/payees", "Payees") in layout.SECTIONS["/settings"]  # a list, among the settings' tabs

    shops(ctx, setup)
    await user.open("/payees")
    await user.should_see("4 payees")
    await user.should_see("2 transactions · spent 42.50 CHF")
    await user.should_see("1 transaction · received 100.00 CHF")
    await user.should_see("No transactions")
    assert len(icon_buttons(user, "o_delete")) == 1  # only the unused payee can be deleted

    payee_svc.learn(ctx, [("Einkauf MIGROS ZUERICH HB 1234", "Migros")])
    put(user, newest(user, ui.input), "zuerich")  # the search also finds bank texts
    await user.should_see("1 of 4 payees")
    await user.should_see("Migros")
    await user.should_not_see("Coop")
    put(user, newest(user, ui.input), "nothing like it")
    await user.should_see("No payee matches.")


async def test_rename_a_payee_and_its_transactions(user: User, ctx, setup) -> None:
    shops(ctx, setup)
    await user.open("/payees")
    click(user, icon_buttons(user, "o_edit")[0])  # Coop
    name = newest(user, ui.input, "Name")
    assert name.value == "Coop"
    put(user, name, "Migros")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "A payee called “Migros” already exists. Merge the two instead.")

    put(user, name, "Coop Supermarkt")
    UserInteraction(user, {name}, None).trigger("keydown.enter")
    assert notified(user, "Renamed to “Coop Supermarkt”")
    assert sorted(tx.payee for tx in ctx.store.transactions.all())[:2] == ["Coop Supermarkt", "Coop Supermarkt"]
    await user.should_see("Coop Supermarkt")


async def test_merge_selected_payees(user: User, ctx, setup) -> None:
    shops(ctx, setup)
    await user.open("/payees")
    boxes = checkboxes(user)  # Coop, Migros, Muster AG, Unused Shop
    click(user, boxes[0])
    await user.should_see("1 selected")
    assert not newest(user, ui.button, text="Merge…").enabled
    click(user, boxes[1])
    await user.should_see("2 selected")
    click(user, newest(user, ui.button, text="Merge…"))
    keep = newest(user, ui.radio)
    assert keep.options == {payee_svc.find(ctx, "Coop").id: "Coop", payee_svc.find(ctx, "Migros").id: "Migros"}
    assert keep.value == payee_svc.find(ctx, "Coop").id  # the one used most
    put(user, keep, payee_svc.find(ctx, "Migros").id)
    click(user, newest(user, ui.button, text="Merge"))
    assert notified(user, "Merged 2 payees into “Migros”")
    assert {p.name for p in payee_svc.all_payees(ctx)} == {"Migros", "Muster AG", "Unused Shop"}
    assert payee_svc.find(ctx, "Migros").alias_list == ["coop"]
    await user.should_see("3 payees")
    await user.should_not_see("2 selected")

    # a payee merged elsewhere while the sheet is open
    click(user, checkboxes(user)[0])
    click(user, checkboxes(user)[1])
    click(user, newest(user, ui.button, text="Merge…"))
    ctx.store.payees.delete([payee_svc.find(ctx, "Muster AG").id])
    click(user, newest(user, ui.button, text="Merge"))
    assert notified(user, "Select at least two payees to merge")
    click(user, newest(user, ui.button, text="Clear Selection"))
    await user.should_not_see("2 selected")

    click(user, checkboxes(user)[-1])
    click(user, checkboxes(user)[-1])  # unticked again
    click(user, checkboxes(user)[-1])
    await user.should_see("1 selected")
    click(user, newest(user, ui.button, text="Clear Selection"))
    await user.should_not_see("1 selected")


async def test_delete_an_unused_payee(user: User, ctx, setup) -> None:
    shops(ctx, setup)
    await user.open("/payees")
    click(user, icon_buttons(user, "o_delete")[0])
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "Deleted Unused Shop")
    assert payee_svc.find(ctx, "Unused Shop") is None

    ctx.store.payees.add(Payee(name="Soon Used"))
    await user.open("/payees")
    tx_svc.save(ctx, spend(setup, "Soon Used"))  # used in the meantime
    click(user, icon_buttons(user, "o_delete")[-1])
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "“Soon Used” is still in use and can’t be deleted")


# ------------------------------------------------------------------ the transaction form and the recurring sheet
async def test_the_form_saves_a_typed_payee_and_suggests_recent_ones(user: User, ctx, setup) -> None:
    tx_svc.save(ctx, spend(setup, "Older", date=date(2026, 8, 1)))
    tx_svc.save(ctx, spend(setup, "Newer", date=date(2026, 9, 1)))
    await user.open("/add-transaction")
    payee = newest(user, PayeeInput, "Payee")
    assert payee.props["_autocomplete"] == ["Newer", "Older"]
    assert payee.visible
    put(user, newest(user, ui.toggle), "transfer")
    assert not payee.visible
    put(user, newest(user, ui.toggle), "expenditure")

    put(user, newest(user, ui.number, "Amount"), 4.8)
    put(user, newest(user, ui.select, "Category"), "01.1")
    put(user, newest(user, ui.select, "From Account"), setup["chf"].id)
    UserInteraction(user, {payee}, None).type("Brezelkönig")
    click(user, newest(user, ui.button, text="Add Transaction"))
    assert notified(user, "Transaction added")
    saved = next(tx for tx in ctx.store.transactions.all() if tx.amount == 4.8)
    assert saved.payee == "Brezelkönig" and payee_svc.find(ctx, "brezelkonig") is not None
    assert payee.value == ""  # the form resets, and knows the new payee
    assert "Brezelkönig" in payee.props["_autocomplete"]


async def test_templates_and_duplicates_carry_the_payee(user: User, ctx, setup) -> None:
    templates_svc.save(ctx, "Rent", spend(setup, "Immo AG", 1900))
    await user.open("/add-transaction")
    rent = next(m for m in menu_items(user) if "Rent" in [getattr(e, "text", "") for e in m.descendants()])
    click(user, rent)
    assert newest(user, PayeeInput, "Payee").value == "Immo AG"

    tx = tx_svc.save(ctx, spend(setup, "Immo AG", 1900))
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, prefill=tx_svc.duplicate_input(ctx, tx, date.today()))
    assert newest(user, PayeeInput, "Payee").value == "Immo AG"


async def test_the_recurring_sheet_has_a_payee(user: User, ctx, setup) -> None:
    await user.open("/recurring")
    click(user, newest(user, ui.button, text="New Recurring Transaction"))
    payee = newest(user, PayeeInput, "Payee")
    put(user, newest(user, ui.number, "Amount"), 49.9)
    put(user, newest(user, ui.select, "Category"), "08.3")
    put(user, newest(user, ui.select, "From Account"), setup["chf"].id)
    put(user, payee, "Swisscom")
    click(user, newest(user, ui.button, text="Add Recurring Transaction"))
    assert notified(user, "Recurring transaction added")
    [series] = ctx.store.recurring.all()
    assert series.payee == "Swisscom"
    await user.should_see("Swisscom · 08.3 Phone and internet")


# ------------------------------------------------------------------ the Transactions page
async def test_transactions_show_search_and_filter_payees(user: User, ctx, setup) -> None:
    shops(ctx, setup)
    await user.open("/transactions")
    assert {r["payee"] for r in grid_rows(user)} == {"Coop", "Migros", "Muster AG"}
    search = next(i for i in user.find(kind=ui.input).elements if i.props.get("placeholder") == "Search")
    put(user, search, "migros")
    assert [r["payee"] for r in grid_rows(user)] == ["Migros"]
    put(user, search, "")

    show_filters(user)
    payee = newest(user, ui.select, "Payee")
    assert payee.options == ["Coop", "Migros", "Muster AG", "Unused Shop"]
    put(user, payee, "Coop")
    assert [r["payee"] for r in grid_rows(user)] == ["Coop", "Coop"]
    await user.should_see("2 of 4 transactions")


# ------------------------------------------------------------------ the import preview
async def test_an_import_finds_payees_and_remembers_the_bank_texts(user: User, ctx, setup) -> None:
    data = (
        b"Buchungsdatum;Buchungstext;Betrag\n"
        b"02.10.2026;Einkauf Debitkarte 02.10.2026 MIGROS ZUERICH HB 1234;-45.20\n"
        b"03.10.2026;TWINT *Brezelkoenig;-4.80\n"
    )
    await user.open("/import")
    await upload(user, "bank.csv", data)
    put(user, newest(user, ui.select, "Account"), setup["chf"].id)
    click(user, import_button(user))
    await user.should_see("2 transactions imported", retries=30)
    assert sorted(tx.payee for tx in ctx.store.transactions.all()) == ["Brezelkönig", "Migros"]
    assert "migros zuerich hb" in payee_svc.find(ctx, "Migros").alias_list
