"""The transaction form (page and sheet) and its product list, driven like a user would."""

from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app.domain.fx import FxError
from budget_app.domain.models import ACCOUNT_ADVANCE
from budget_app.services import accounts as acc_svc
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.transaction_form import AUTO_ADVANCE, open_transaction_sheet

TODAY = date.today()


# ------------------------------------------------------------------ helpers
def field(user: User, kind: type, label: str):
    """The one visible element of `kind` whose label is exactly `label`."""
    found = [e for e in user.find(kind=kind).elements if e.props.get("label") == label]
    assert len(found) == 1, f"expected one {kind.__name__} labelled {label!r}, found {len(found)}"
    return found[0]


def put(user: User, element, value) -> None:
    with user:
        element.value = value


def button(user: User, text: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.text == text]
    assert found, f"no visible button {text!r}"
    return max(found, key=lambda b: b.id)  # the newest (page actions and sheets come after the sidebar)


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def type_toggle(user: User) -> ui.toggle:
    return next(e for e in user.find(kind=ui.toggle).elements if "income" in e.options)


def status(user: User) -> str:
    """The text next to the save button that explains why it is disabled."""
    save = next(b for b in user.find(kind=ui.button).elements if b.text in ("Add Transaction", "Save Changes"))
    row = save.parent_slot.parent
    return next(c.text for c in row.default_slot.children if isinstance(c, ui.label))


def save_button(user: User) -> ui.button:
    return next(b for b in user.find(kind=ui.button).elements if b.text in ("Add Transaction", "Save Changes"))


def caption_texts(user: User) -> list[str]:
    return [e.text for e in user.find(kind=ui.label).elements if e.text]


def fill_expenditure(user: User, account_id: str, amount: float = 50, category: str = "01.1") -> None:
    put(user, field(user, ui.number, "Amount"), amount)
    put(user, field(user, ui.select, "Category"), category)
    put(user, field(user, ui.select, "From Account"), account_id)


def item_rows(user: User) -> list[tuple[ui.input, ui.number, ui.number]]:
    names = [e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "e.g. Milk"]
    rows = []
    for name in sorted(names, key=lambda e: e.id):
        siblings = name.parent_slot.children
        numbers = [c for c in siblings if isinstance(c, ui.number)]
        rows.append((name, numbers[0], numbers[1]))
    return rows


@pytest.fixture
def advance(ctx, setup):
    """An expenditure of 80 CHF of which 30 CHF were paid for Anna."""
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 1),
            type="expenditure",
            amount=80,
            currency="CHF",
            category="11.1",
            from_account=setup["chf"].id,
            is_advance=True,
            advance_mode="amount",
            advance_value=30,
            debtor="Anna",
        ),
    )


# ------------------------------------------------------------------ expenditure
async def test_expenditure_is_saved_and_the_form_resets(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    await user.should_see("New Transaction")
    assert status(user) == ""  # nothing explained before the user starts
    assert not save_button(user).enabled

    fill_expenditure(user, setup["chf"].id, amount=42.5)
    put(user, field(user, ui.textarea, "Note"), "  Weekly shop  ")
    assert save_button(user).enabled and status(user) == ""
    click(user, save_button(user))

    assert user.notify.contains("Transaction added")
    [tx] = ctx.store.transactions.all()
    assert (tx.type, tx.amount, tx.currency, tx.category, tx.from_account) == (
        "expenditure",
        42.5,
        "CHF",
        "01.1",
        setup["chf"].id,
    )
    assert tx.comment == "Weekly shop" and tx.date == TODAY and not tx.is_advance
    # reset: everything but date, currency and type is cleared, and no error is shown yet
    assert field(user, ui.number, "Amount").value is None
    assert field(user, ui.select, "Category").value is None
    assert field(user, ui.textarea, "Note").value == ""
    assert field(user, ui.select, "Currency").value == "CHF"
    assert type_toggle(user).value == "expenditure"
    assert status(user) == "" and not save_button(user).enabled


async def test_save_is_disabled_with_a_reason_until_the_form_is_valid(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    amount = field(user, ui.number, "Amount")
    put(user, amount, 0)
    assert status(user) == "Amount must be a positive number" and not save_button(user).enabled
    put(user, amount, 20)
    assert status(user) == "Select an expenditure category"
    put(user, field(user, ui.select, "Category"), "01.1")
    assert status(user) == "Select the From account"
    put(user, field(user, ui.input, "Date"), "")
    assert status(user) == "Date is required"
    put(user, field(user, ui.input, "Date"), "2026-10-10")
    put(user, field(user, ui.select, "From Account"), setup["chf"].id)
    assert status(user) == "" and save_button(user).enabled

    # the account list follows the currency: a CHF account does not fit a EUR amount
    put(user, field(user, ui.select, "Currency"), "EUR")
    from_acc = field(user, ui.select, "From Account")
    assert from_acc.value is None and list(from_acc.options) == [setup["eur"].id]
    assert status(user) == "Select the From account"
    assert ctx.store.transactions.all() == []


async def test_foreign_amount_shows_its_value_in_chf(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, field(user, ui.select, "Currency"), "EUR")
    put(user, field(user, ui.number, "Amount"), 100)
    put(user, field(user, ui.input, "Date"), "2026-10-17")
    assert any(text.startswith("= 90.00 CHF (ECB rate of") for text in caption_texts(user))


async def test_a_missing_exchange_rate_hides_the_hint_and_blocks_saving(user: User, ctx, setup, monkeypatch) -> None:
    def no_rate(*_args):
        raise FxError("No ECB rate for currency EUR")

    monkeypatch.setattr(ctx, "to_base_on", no_rate)
    await user.open("/add-transaction")
    put(user, field(user, ui.select, "Currency"), "EUR")
    fill_expenditure(user, setup["eur"].id, amount=100)
    assert not any("(ECB rate of" in text for text in caption_texts(user))
    click(user, save_button(user))
    assert user.notify.contains("No ECB rate for currency EUR")
    assert ctx.store.transactions.all() == []


async def test_advance_payment_in_percent_creates_the_default_advance_account(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    fill_expenditure(user, setup["chf"].id, amount=50)
    switch = next(e for e in user.find(kind=ui.switch).elements if "advance payment" in e.text)
    put(user, switch, True)
    assert field(user, ui.select, "Advance Payment Account").value == AUTO_ADVANCE
    put(user, field(user, ui.number, "Paid for others"), 0)
    assert status(user) == "The advance share must be greater than 0 and at most the amount"
    put(user, field(user, ui.number, "Paid for others"), 40)  # percent is the default mode
    assert "Others: 20.00 CHF · own share: 30.00 CHF" in caption_texts(user)
    assert status(user) == "Enter the debtor"
    put(user, field(user, ui.input, "Debtor"), " Anna ")
    click(user, save_button(user))

    [tx] = ctx.store.transactions.all()
    assert tx.is_advance and tx.advance_amount == 20 and tx.debtor == "Anna"
    adv_acc = ctx.store.accounts.get(tx.advance_account)
    assert (adv_acc.name, adv_acc.currency, adv_acc.kind) == ("Advance payments", "CHF", ACCOUNT_ADVANCE)


async def test_advance_payment_as_an_amount_uses_the_existing_advance_account(user: User, ctx, setup) -> None:
    default = acc_svc.default_advance_account(ctx, "CHF")
    await user.open("/add-transaction")
    fill_expenditure(user, setup["chf"].id, amount=50)
    put(user, next(e for e in user.find(kind=ui.switch).elements if "advance payment" in e.text), True)
    adv_account = field(user, ui.select, "Advance Payment Account")
    assert adv_account.value == default.id and AUTO_ADVANCE not in adv_account.options
    mode = next(e for e in user.find(kind=ui.toggle).elements if "percent" in e.options)
    put(user, mode, "amount")
    put(user, field(user, ui.number, "Paid for others"), 60)
    assert status(user) == "The advance share must be greater than 0 and at most the amount"
    put(user, field(user, ui.number, "Paid for others"), 12.5)
    put(user, field(user, ui.input, "Debtor"), "Ben")
    click(user, save_button(user))

    [tx] = ctx.store.transactions.all()
    assert (tx.advance_amount, tx.advance_account, tx.debtor) == (12.5, default.id, "Ben")
    assert len(acc_svc.accounts(ctx, ACCOUNT_ADVANCE)) == 1


# ------------------------------------------------------------------ income
async def test_income_with_a_category(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, type_toggle(user), "income")
    put(user, field(user, ui.number, "Amount"), 6200)
    assert status(user) == "Select the To account"
    put(user, field(user, ui.select, "To Account"), setup["chf"].id)
    assert status(user) == "Select an income category"
    put(user, field(user, ui.select, "Income category"), setup["salary"].id)
    click(user, save_button(user))

    [tx] = ctx.store.transactions.all()
    assert (tx.type, tx.amount, tx.to_account, tx.category) == ("income", 6200, setup["chf"].id, setup["salary"].id)
    assert type_toggle(user).value == "income"  # the type survives the reset


async def test_income_repays_an_open_advance(user: User, ctx, setup, advance) -> None:
    await user.open("/add-transaction")
    put(user, type_toggle(user), "income")
    put(user, field(user, ui.select, "To Account"), setup["chf"].id)
    put(user, next(e for e in user.find(kind=ui.switch).elements if e.text == "Repays an advance payment"), True)
    assert not [e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Income category"]

    put(user, field(user, ui.select, "Advance Payment Account"), advance.advance_account)
    open_items = field(user, ui.select, "Outstanding Payment")
    assert list(open_items.options) == [advance.id]
    assert "Anna · 30.00 CHF open" in open_items.options[advance.id]
    put(user, open_items, advance.id)
    assert field(user, ui.number, "Amount").value == 30  # the open amount is proposed
    put(user, field(user, ui.number, "Amount"), 35)
    assert status(user).startswith("The repayment exceeds the outstanding amount (30.00 CHF)")
    put(user, field(user, ui.number, "Amount"), 10)
    click(user, save_button(user))

    repay = next(tx for tx in ctx.store.transactions.all() if tx.type == "income")
    assert repay.is_advance and repay.settles_id == advance.id and repay.debtor == "Anna" and repay.amount == 10
    assert tx_svc.settled_amount(ctx, advance.id) == 10

    # switching the advance account forgets the chosen payment
    put(user, next(e for e in user.find(kind=ui.switch).elements if e.text == "Repays an advance payment"), True)
    put(user, field(user, ui.select, "Advance Payment Account"), advance.advance_account)
    put(user, field(user, ui.select, "Outstanding Payment"), advance.id)
    assert field(user, ui.number, "Amount").value == 20  # what is still open
    put(user, field(user, ui.select, "Advance Payment Account"), None)
    assert field(user, ui.select, "Outstanding Payment").value is None


# ------------------------------------------------------------------ transfer and exchange
async def test_account_transfer(user: User, ctx, setup) -> None:
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    await user.open("/add-transaction")
    put(user, type_toggle(user), "transfer")
    assert not [e for e in user.find(kind=ui.button).elements if e.text == "Add Item"]  # no product list
    put(user, field(user, ui.number, "Amount"), 500)
    put(user, field(user, ui.select, "From"), setup["chf"].id)
    put(user, field(user, ui.select, "To"), setup["chf"].id)
    assert status(user) == "From and To account must differ"
    assert setup["eur"].id not in field(user, ui.select, "To").options  # only accounts in the currency
    put(user, field(user, ui.select, "To"), savings.id)
    click(user, save_button(user))

    [tx] = ctx.store.transactions.all()
    assert (tx.type, tx.from_account, tx.to_account, tx.amount) == ("transfer", setup["chf"].id, savings.id, 500)


async def test_currency_exchange_needs_the_amount_received(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, type_toggle(user), "exchange")
    put(user, field(user, ui.input, "Date"), "2026-10-17")
    put(user, field(user, ui.select, "From"), setup["eur"].id)
    currency = field(user, ui.select, "Currency")
    assert currency.value == "EUR" and not currency.enabled  # the From account decides the currency
    assert list(field(user, ui.select, "To").options) == [setup["chf"].id]
    put(user, field(user, ui.number, "Amount"), 100)
    put(user, field(user, ui.select, "To"), setup["chf"].id)
    received = field(user, ui.number, "Amount Received")
    assert received.props.get("suffix") == "CHF"
    assert status(user) == "Enter the amount received in CHF"
    assert any(text.startswith("ECB rate of") and "→ 90.00 CHF" in text for text in caption_texts(user))

    put(user, received, 88)
    hint = next(text for text in caption_texts(user) if text.startswith("ECB rate of"))
    assert "Rate received: 1 EUR = 0.8800 CHF" in hint and "−2.00 CHF against the ECB rate" in hint
    click(user, save_button(user))

    [tx] = ctx.store.transactions.all()
    assert (tx.type, tx.amount, tx.currency, tx.to_amount, tx.to_currency) == ("exchange", 100, "EUR", 88, "CHF")
    assert (tx.from_account, tx.to_account) == (setup["eur"].id, setup["chf"].id)
    assert currency.value == "EUR"  # the reset keeps the currency

    put(user, type_toggle(user), "expenditure")
    assert field(user, ui.select, "Currency").enabled


# ------------------------------------------------------------------ items
async def test_items_are_totalled_and_saved_with_the_transaction(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    click(user, button(user, "Add Item"))
    [(name, qty, price)] = item_rows(user)
    assert qty.value == 1
    put(user, name, "Milk")
    put(user, qty, 2)
    put(user, price, 1.6)
    await user.should_see("1 item · 3.20 CHF")
    assert next(c for c in name.parent_slot.children if isinstance(c, ui.label)).text == "3.20"  # line total

    # Enter on the price starts the next line
    UserInteraction(user, {price}, None).trigger("keydown.enter")
    assert len(item_rows(user)) == 2
    _, _, price2 = item_rows(user)[1]
    put(user, item_rows(user)[1][0], "Bread")
    put(user, price2, 4.3)
    await user.should_see("2 items · 7.50 CHF")

    click(user, button(user, "Use Total as Amount"))
    assert field(user, ui.number, "Amount").value == 7.5
    await user.should_see("2 items · matches the amount")
    put(user, field(user, ui.number, "Amount"), 10)
    await user.should_see("2 items · 2.50 CHF not itemised")
    put(user, field(user, ui.number, "Amount"), 7)
    await user.should_see("2 items · 0.50 CHF more than the amount")

    put(user, field(user, ui.number, "Amount"), 9.95)
    put(user, field(user, ui.select, "Category"), "01.1")
    put(user, field(user, ui.select, "From Account"), setup["chf"].id)
    click(user, save_button(user))

    [tx] = ctx.store.transactions.all()
    assert tx.amount == 9.95  # items never change the amount
    stored = items_svc.items_for(ctx, tx.id)
    assert [(i.name, i.quantity, i.unit_price) for i in stored] == [("Milk", 2, 1.6), ("Bread", 1, 4.3)]
    assert item_rows(user) == []  # the reset empties the list


async def test_a_half_filled_item_blocks_saving(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    fill_expenditure(user, setup["chf"].id, amount=12)
    click(user, button(user, "Add Item"))
    put(user, item_rows(user)[0][0], "Cheese")
    click(user, save_button(user))
    assert user.notify.contains("Item 1: enter a price")
    assert ctx.store.transactions.all() == []

    # a blank line is ignored, a removed line is gone
    click(user, button(user, "Add Item"))
    name, _, _ = item_rows(user)[0]
    remove = next(c for c in name.parent_slot.children if isinstance(c, ui.button))
    click(user, remove)
    assert len(item_rows(user)) == 1
    click(user, save_button(user))
    [tx] = ctx.store.transactions.all()
    assert items_svc.items_for(ctx, tx.id) == []


async def test_switching_to_a_transfer_drops_the_items(user: User, ctx, setup) -> None:
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    await user.open("/add-transaction")
    click(user, button(user, "Add Item"))
    name, _, price = item_rows(user)[0]
    put(user, name, "Bonus")
    put(user, price, 100)
    put(user, type_toggle(user), "transfer")
    put(user, field(user, ui.number, "Amount"), 100)
    put(user, field(user, ui.select, "From"), setup["chf"].id)
    put(user, field(user, ui.select, "To"), savings.id)
    click(user, save_button(user))
    [tx] = ctx.store.transactions.all()
    assert items_svc.items_for(ctx, tx.id) == []  # transfers have no product list


# ------------------------------------------------------------------ editing in the sheet
async def test_editing_an_expenditure_in_the_sheet_keeps_its_items(user: User, ctx, setup) -> None:
    tx = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 5),
            amount=20,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            comment="Market",
        ),
    )
    items_svc.replace(ctx, tx.id, [ItemInput("Apples", 2, 3.0), ItemInput("Honey", 1, 14.0)])
    saved = []
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, on_saved=lambda: saved.append(True), editing=tx)
    await user.should_see("Edit Transaction")
    assert [(r[0].value, r[1].value, r[2].value) for r in item_rows(user)] == [("Apples", 2, 3.0), ("Honey", 1, 14.0)]
    await user.should_see("2 items · matches the amount")
    assert status(user) == ""

    put(user, field(user, ui.number, "Amount"), 0)
    assert status(user) == "Amount must be a positive number"  # an edit explains right away
    put(user, field(user, ui.number, "Amount"), 25)
    put(user, field(user, ui.textarea, "Note"), "Farmers market")
    click(user, button(user, "Save Changes"))

    assert user.notify.contains("Changes saved") and saved == [True]
    [stored] = ctx.store.transactions.all()
    assert (stored.id, stored.amount, stored.comment) == (tx.id, 25, "Farmers market")
    assert [i.name for i in items_svc.items_for(ctx, tx.id)] == ["Apples", "Honey"]


async def test_editing_an_advance_and_its_repayment(user: User, ctx, setup, advance) -> None:
    repay = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 8),
            type="income",
            amount=30,
            currency="CHF",
            to_account=setup["chf"].id,
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=repay)
    await user.should_see("Edit Transaction")
    # the advance is fully repaid, yet the repayment being edited still lists it
    open_items = field(user, ui.select, "Outstanding Payment")
    assert open_items.value == advance.id and "Anna" in open_items.options[advance.id]
    assert field(user, ui.select, "Advance Payment Account").value == advance.advance_account
    put(user, field(user, ui.number, "Amount"), 25)
    click(user, button(user, "Save Changes"))
    assert ctx.store.transactions.get(repay.id).amount == 25


async def test_editing_an_advance_expenditure_shows_its_share_as_an_amount(user: User, ctx, setup, advance) -> None:
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=advance)
    await user.should_see("Edit Transaction")
    assert field(user, ui.select, "Advance Payment Account").value == advance.advance_account
    assert field(user, ui.number, "Paid for others").value == 30
    assert field(user, ui.input, "Debtor").value == "Anna"
    assert "Others: 30.00 CHF · own share: 50.00 CHF" in caption_texts(user)
    put(user, field(user, ui.number, "Paid for others"), 40)
    click(user, button(user, "Save Changes"))
    assert ctx.store.transactions.get(advance.id).advance_amount == 40


async def test_a_failed_save_in_the_sheet_keeps_it_open(user: User, ctx, setup, advance) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 8),
            type="income",
            amount=30,
            currency="CHF",
            to_account=setup["chf"].id,
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=advance)
    await user.should_see("Edit Transaction")
    put(user, next(e for e in user.find(kind=ui.switch).elements if "advance payment" in e.text), False)
    click(user, button(user, "Save Changes"))
    assert user.notify.contains("of this advance has already been repaid")
    assert ctx.store.transactions.get(advance.id).is_advance

    click(user, button(user, "Cancel"))
    dialog = next(e for e in user.find(kind=ui.dialog).elements)
    assert not dialog.value
    # the browser fires 'hide' after the closing animation; the sheet then leaves the page (B10)
    user.find(kind=ui.dialog).trigger("hide")
    assert dialog.is_deleted


async def test_editing_an_exchange(user: User, ctx, setup) -> None:
    tx = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 17),
            type="exchange",
            amount=200,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=215,
        ),
    )
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=tx)
    await user.should_see("Edit Transaction")
    assert field(user, ui.number, "Amount Received").value == 215
    put(user, field(user, ui.number, "Amount Received"), 220)
    click(user, button(user, "Save Changes"))
    assert ctx.store.transactions.get(tx.id).to_amount == 220


async def test_the_sidebar_opens_a_new_transaction_sheet(user: User, ctx, setup) -> None:
    await user.open("/transactions")
    sidebar = min((b for b in user.find(kind=ui.button).elements if b.text == "New Transaction"), key=lambda b: b.id)
    click(user, sidebar)
    await user.should_see("Add Transaction")
    fill_expenditure(user, setup["chf"].id, amount=9)
    click(user, save_button(user))
    assert len(ctx.store.transactions.all()) == 1
    await user.should_see("1 transaction")  # the page behind the sheet refreshed
