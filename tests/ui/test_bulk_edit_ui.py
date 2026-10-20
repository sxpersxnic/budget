"""The bulk edit sheet (F13) on the Transactions page: fields per type, hints, live checks, saving and undo."""

import re
from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app.services import accounts as acc_svc
from budget_app.services import bulk as bulk_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.bulk_edit import open_bulk_edit_sheet

SELECTED = re.compile(r".*getSelectedRows")


def newest(user: User, kind: type, label: str):
    """The most recently created visible element of `kind` labelled `label` (closed sheets stay in simulation)."""
    found = [e for e in user.find(kind=kind).elements if e.props.get("label") == label]
    assert found, f"no {kind.__name__} labelled {label!r}"
    return max(found, key=lambda e: e.id)


def button(user: User, text: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.text == text]
    assert found, f"no visible button {text!r}"
    return max(found, key=lambda b: b.id)


def put(user: User, element, value) -> None:
    with user:
        element.value = value


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def key(user: User, keyboard: ui.keyboard, name: str, **mods) -> None:
    args = {
        "action": "keydown",
        "repeat": False,
        "altKey": False,
        "ctrlKey": mods.get("ctrl", False),
        "metaKey": mods.get("meta", False),
        "shiftKey": False,
        "key": name,
        "code": name,
        "location": 0,
    }
    UserInteraction(user, {keyboard}, None).trigger("key", args)


async def select(user: User, *ids: str) -> None:
    """Select grid rows the way the browser reports them."""
    [grid] = user.find(kind=ui.aggrid).elements
    user.javascript_rules[SELECTED] = lambda _: [{"id": i} for i in ids]
    UserInteraction(user, {grid}, None).trigger("selectionChanged")
    await user.should_see(f"{len(ids)} selected")


@pytest.fixture
def rows(ctx, setup):
    """Two CHF expenditures, a CHF salary, a EUR expenditure, a transfer and an exchange with its spread."""
    savings = acc_svc.add_account(ctx, "Savings", "CHF")

    def save(**kw):
        base = dict(date=date(2026, 10, 3), amount=20, currency="CHF", category="01.1", from_account=setup["chf"].id)
        return tx_svc.save(ctx, TransactionInput(**(base | kw)))

    out = {
        "food": save(comment="Market"),
        "rail": save(amount=80, category="07.3"),
        "salary": save(
            type="income", amount=6200, category=setup["salary"].id, from_account="", to_account=setup["chf"].id
        ),
        "euro": save(amount=50, currency="EUR", from_account=setup["eur"].id),
        "transfer": save(type="transfer", amount=500, category="", to_account=savings.id),
        "exchange": save(
            date=date(2026, 10, 16),
            type="exchange",
            amount=100,
            category="",
            to_account=setup["eur"].id,
            to_amount=110.5,
        ),
    }
    out["spread"] = ctx.store.transactions.get(out["exchange"].linked_id)
    return setup | out | {"savings": savings}


# ------------------------------------------------------------------ from the selection bar
async def test_edit_with_several_rows_changes_them_in_one_step(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    await select(user, rows["food"].id, rows["rail"].id, rows["salary"].id)
    click(user, button(user, "Edit"))
    await user.should_see("Edit 3 Transactions")
    await user.should_see("1 of 3 rows is not expenditure and keeps its category")
    await user.should_see("2 of 3 rows are not income and keep their category")
    await user.should_see("For expenditure the From account, for income the To account.")
    assert not button(user, "Save").enabled  # every field starts as "leave unchanged"
    category = newest(user, ui.select, "Expenditure category")
    assert "placeholder" in category.props

    put(user, category, "TAX")
    await user.should_see("Changes 2 transactions")
    put(user, newest(user, ui.select, "Account"), rows["savings"].id)
    await user.should_see("Changes 3 transactions")
    click(user, button(user, "Save"))
    assert notified(user, "Changed 3 transactions")
    assert ctx.store.transactions.get(rows["food"].id).category == "TAX"
    assert ctx.store.transactions.get(rows["salary"].id).to_account == rows["savings"].id
    [grid] = user.find(kind=ui.aggrid).elements
    assert next(r for r in grid.options["rowData"] if r["id"] == rows["rail"].id)["from"] == "Savings (CHF)"
    assert undo_svc.latest(ctx).label == "Edit several transactions"


async def test_one_row_still_opens_the_editor(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    await select(user, rows["food"].id)
    click(user, button(user, "Edit"))
    await user.should_see("Edit Transaction")
    await user.should_not_see("Edit 1 Transaction")


# ------------------------------------------------------------------ the sheet
async def test_mixed_currencies_disable_the_account(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    with user:
        open_bulk_edit_sheet(ctx, [rows["food"].id, rows["euro"].id, rows["transfer"].id])
    await user.should_see("Edit 3 Transactions")
    account = newest(user, ui.select, "From Account")
    assert not account.enabled and account.options == {}
    await user.should_see(
        "The rows are in different currencies (CHF, EUR), so their account can’t be changed together. "
        "1 of 3 rows is a transfer, exchange or revaluation and keeps its accounts."
    )
    await user.should_see("1 of 3 rows is not expenditure and keeps its category")


async def test_income_only_offers_the_to_accounts_in_its_currency(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    second = tx_svc.save(ctx, tx_svc.duplicate_input(ctx, rows["salary"], date(2026, 9, 25)))
    with user:
        open_bulk_edit_sheet(ctx, [rows["salary"].id, second.id])
    account = newest(user, ui.select, "To Account")
    assert set(account.options) == {rows["chf"].id, rows["savings"].id}
    assert newest(user, ui.select, "Income category")
    assert not [e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Category"]


async def test_transfers_and_spreads_keep_what_they_have(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [rows["transfer"].id, rows["exchange"].id, rows["spread"].id])
    await user.should_see("Transfers, exchanges and revaluations keep their accounts.")
    await user.should_see("1 spread booking of a currency exchange keeps its note; it changes with its exchange.")
    assert sheet.exp_cat is None and sheet.inc_cat is None and sheet.account is None
    assert not sheet.note.visible

    put(user, sheet.note_mode, "append")
    assert sheet.note.visible
    put(user, sheet.note, "Trip")
    await user.should_see("Changes 2 transactions")  # the spread follows its exchange
    keyboard = max(user.find(kind=ui.keyboard).elements, key=lambda k: k.id)
    key(user, keyboard, "Enter")  # plain Enter does nothing
    assert ctx.store.transactions.get(rows["transfer"].id).comment == ""
    key(user, keyboard, "Enter", meta=True)
    assert notified(user, "Changed 2 transactions")
    assert ctx.store.transactions.get(rows["exchange"].id).comment == "Trip"
    assert ctx.store.transactions.get(rows["spread"].id) == rows["spread"]


async def test_a_row_already_matching_says_nothing_changes(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [rows["food"].id, rows["transfer"].id])
    await user.should_see("1 of 2 rows is not expenditure and keeps its category")
    put(user, sheet.exp_cat, "01.1")
    await user.should_see("Nothing changes: the rows already look like this.")
    assert not sheet.save_btn.enabled
    put(user, sheet.exp_cat, None)
    assert sheet.status.text == ""


async def test_repayments_are_named_in_the_income_hint(user: User, ctx, rows) -> None:
    advance = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 12),
            amount=90,
            currency="CHF",
            category="11.1",
            from_account=rows["chf"].id,
            is_advance=True,
            advance_value=50,
            debtor="Clara",
        ),
    )
    repay = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 14),
            type="income",
            amount=45,
            currency="CHF",
            to_account=rows["chf"].id,
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    await user.open("/transactions")
    with user:
        open_bulk_edit_sheet(ctx, [rows["salary"].id, repay.id, advance.id])
    await user.should_see("2 of 3 rows are not income, or repay an advance, and keep their category")


async def test_failing_rows_are_listed_and_block_saving(user: User, ctx, rows) -> None:
    broken = ctx.store.transactions.get(rows["rail"].id)
    broken.category = "99.9"  # no longer a category: the row can't be saved as it is
    ctx.store.transactions.update(broken)
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [rows["food"].id, rows["rail"].id])
    put(user, sheet.note_mode, "replace")
    put(user, sheet.note, "Week")
    await user.should_see("1 transaction can’t be changed, so nothing is saved:")
    await user.should_see("3 Oct 2026 · 80.00 CHF: Select an expenditure category")
    assert not sheet.save_btn.enabled
    assert ctx.store.transactions.get(rows["food"].id).comment == "Market"

    put(user, sheet.exp_cat, "07.3")  # a valid category repairs it
    await user.should_see("Changes 2 transactions")
    assert sheet.problems.text == ""


async def test_a_row_changed_after_the_check_stops_the_save(user: User, ctx, rows) -> None:
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [rows["food"].id, rows["rail"].id])
    put(user, sheet.account, rows["savings"].id)
    assert sheet.save_btn.enabled
    savings = ctx.store.accounts.get(rows["savings"].id)
    savings.currency = "EUR"  # changed elsewhere in the meantime
    ctx.store.accounts.update(savings)
    with user:
        sheet._save()
    assert notified(user, "2 transactions can’t be changed, so nothing is saved")
    assert not sheet.save_btn.enabled
    assert ctx.store.transactions.get(rows["food"].id).from_account == rows["chf"].id


async def test_other_errors_while_saving_are_shown(user: User, ctx, rows, monkeypatch) -> None:
    await user.open("/transactions")
    with user:
        sheet = open_bulk_edit_sheet(ctx, [rows["food"].id, rows["rail"].id])
    put(user, sheet.note_mode, "clear")

    def fail(*_args, **_kw):
        raise ValidationError("Disk full")

    monkeypatch.setattr(bulk_svc, "bulk_edit", fail)
    with user:
        sheet._save()
    assert notified(user, "Disk full")
