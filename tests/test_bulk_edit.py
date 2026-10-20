"""Bulk edit (F13): category, account and note of several transactions, all or nothing, as one undo step."""

from datetime import date

import pytest

from budget_app.services import accounts as acc_svc
from budget_app.services import bulk as bulk_svc
from budget_app.services import items as items_svc
from budget_app.services import payees as payee_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.bulk import BulkChanges, BulkEditError
from budget_app.services.context import ValidationError
from budget_app.services.items import ItemInput
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput


def spend(ctx, account, amount=20.0, currency="CHF", category="01.1", day=3, **kw):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, day),
            amount=amount,
            currency=currency,
            category=category,
            from_account=account.id,
            **kw,
        ),
    )


def earn(ctx, setup, amount=6200.0, day=25, **kw):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, day),
            type="income",
            amount=amount,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            **kw,
        ),
    )


def get(ctx, tx):
    return ctx.store.transactions.get(tx.id)


@pytest.fixture
def mixed(ctx, setup):
    """Two CHF expenditures, a salary, a transfer, and a second CHF account."""
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    food = spend(ctx, setup["chf"], comment="Market")
    rail = spend(ctx, setup["chf"], 80, category="07.3", day=5)
    salary = earn(ctx, setup)
    transfer = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 6),
            type="transfer",
            amount=500,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=savings.id,
        ),
    )
    return setup | {"savings": savings, "food": food, "rail": rail, "salary": salary, "transfer": transfer}


def ids(*txs) -> list[str]:
    return [tx.id for tx in txs]


# ------------------------------------------------------------------ fields
def test_a_category_changes_only_the_rows_of_its_type(ctx, mixed):
    rows = ids(mixed["food"], mixed["rail"], mixed["salary"], mixed["transfer"])
    changed = bulk_svc.bulk_edit(ctx, rows, BulkChanges(expenditure_category="TAX"))
    assert {tx.id for tx in changed} == {mixed["food"].id, mixed["rail"].id}
    assert get(ctx, mixed["food"]).category == get(ctx, mixed["rail"]).category == "TAX"
    assert get(ctx, mixed["salary"]).category == mixed["salary"].category
    assert get(ctx, mixed["transfer"]) == mixed["transfer"]

    bonus = acc_svc.add_income_category(ctx, "Bonus")
    changed = bulk_svc.bulk_edit(ctx, rows, BulkChanges(income_category=bonus.id))
    assert [tx.id for tx in changed] == [mixed["salary"].id]
    assert get(ctx, mixed["salary"]).category == bonus.id
    assert get(ctx, mixed["food"]).category == "TAX"


def test_the_account_is_the_from_account_of_expenditure_and_the_to_account_of_income(ctx, mixed):
    rows = ids(mixed["food"], mixed["salary"], mixed["transfer"])
    changed = bulk_svc.bulk_edit(ctx, rows, BulkChanges(account=mixed["savings"].id))
    assert len(changed) == 2
    assert get(ctx, mixed["food"]).from_account == mixed["savings"].id
    assert get(ctx, mixed["salary"]).to_account == mixed["savings"].id
    assert get(ctx, mixed["transfer"]) == mixed["transfer"]  # transfers keep their accounts


def test_notes_are_replaced_appended_or_cleared(ctx, mixed):
    food, rail = mixed["food"], mixed["rail"]
    rows = ids(food, rail)
    bulk_svc.bulk_edit(ctx, rows, BulkChanges(note_mode=bulk_svc.NOTE_APPEND, note=" Holiday "))
    assert (get(ctx, food).comment, get(ctx, rail).comment) == ("Market Holiday", "Holiday")
    bulk_svc.bulk_edit(ctx, rows, BulkChanges(note_mode=bulk_svc.NOTE_REPLACE, note="Italy"))
    assert {get(ctx, food).comment, get(ctx, rail).comment} == {"Italy"}
    assert bulk_svc.plan(ctx, rows, BulkChanges(note_mode=bulk_svc.NOTE_APPEND, note=" ")).edits == []
    bulk_svc.bulk_edit(ctx, rows, BulkChanges(note_mode=bulk_svc.NOTE_CLEAR, note="ignored"))
    assert {get(ctx, food).comment, get(ctx, rail).comment} == {""}


def test_rows_that_already_have_the_values_drop_out(ctx, mixed):
    rows = ids(mixed["food"], mixed["rail"])
    assert bulk_svc.plan(ctx, rows, BulkChanges()).edits == []
    planned = bulk_svc.plan(ctx, rows, BulkChanges(expenditure_category="01.1"))  # food has it already
    assert [old.id for _, old in planned.edits] == [mixed["rail"].id]
    actions = len(undo_svc.recent(ctx))
    assert bulk_svc.bulk_edit(ctx, ids(mixed["food"]), BulkChanges(expenditure_category="01.1")) == []
    assert len(undo_svc.recent(ctx)) == actions  # nothing changed, nothing to undo


def test_the_selection_counts_each_row_once_and_skips_deleted_ones(ctx, mixed):
    rows = [mixed["food"].id, mixed["food"].id, "gone", mixed["salary"].id]
    sel = bulk_svc.selection(ctx, rows)
    assert [tx.id for tx in sel.rows] == ids(mixed["food"], mixed["salary"])
    assert sel.taking(bulk_svc.takes_expenditure_category) == [mixed["food"]]
    assert sel.account_currencies == ["CHF"]
    changed = bulk_svc.bulk_edit(ctx, rows, BulkChanges(note_mode=bulk_svc.NOTE_REPLACE, note="x"))
    assert len(changed) == 2


def test_an_edit_keeps_valuation_items_and_import_reference(ctx, mixed):
    euro = spend(ctx, mixed["eur"], 50, "EUR", import_ref="csv:abc")
    items_svc.replace(ctx, euro.id, [ItemInput("Bread", 1, 50)])
    second = acc_svc.add_account(ctx, "Wallet", "EUR")
    bulk_svc.bulk_edit(ctx, ids(euro), BulkChanges(account=second.id, expenditure_category="01.1.1"))
    after = get(ctx, euro)
    assert (after.amount_chf, after.fx_date, after.import_ref) == (euro.amount_chf, euro.fx_date, "csv:abc")
    assert after.from_account == second.id and after.category == "01.1.1"
    assert [i.name for i in items_svc.items_for(ctx, euro.id)] == ["Bread"]


# ------------------------------------------------------------------ all or nothing, undo
def test_one_failing_row_changes_nothing_and_names_the_row(ctx, mixed):
    euro = spend(ctx, mixed["eur"], 1234.5, "EUR", day=12)
    rows = ids(mixed["food"], euro, mixed["salary"])
    before = {tx.id: tx for tx in ctx.store.transactions.all()}
    actions = len(undo_svc.recent(ctx))
    with pytest.raises(BulkEditError) as exc:
        bulk_svc.bulk_edit(ctx, rows, BulkChanges(account=mixed["savings"].id, note_mode=bulk_svc.NOTE_CLEAR))
    assert [tx.id for tx, _ in exc.value.failures] == [euro.id]
    assert str(exc.value).splitlines() == [
        "1 transaction can’t be changed, so nothing is saved:",
        "12 Oct 2026 · 1'234.50 EUR: The From account must be in EUR",
    ]
    assert {tx.id: tx for tx in ctx.store.transactions.all()} == before
    assert len(undo_svc.recent(ctx)) == actions


def test_the_whole_edit_is_one_undo_step(ctx, mixed):
    rows = ids(mixed["food"], mixed["rail"], mixed["salary"])
    bulk_svc.bulk_edit(ctx, rows, BulkChanges(account=mixed["savings"].id, note_mode=bulk_svc.NOTE_REPLACE, note="x"))
    act = undo_svc.latest(ctx)
    assert act.label == "Edit several transactions" and len(act.changes) == 3
    undo_svc.undo(ctx, act.id)
    for key in ("food", "rail", "salary"):
        assert get(ctx, mixed[key]) == mixed[key]


# ------------------------------------------------------------------ linked and booked rows
def test_an_exchange_keeps_its_spread_in_step_and_a_spread_is_not_changed_alone(ctx, setup):
    exchange = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 16),
            type="exchange",
            amount=100,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=110.5,
        ),
    )
    spread = ctx.store.transactions.get(exchange.linked_id)
    assert spread is not None and bulk_svc.is_spread(spread)
    note = BulkChanges(note_mode=bulk_svc.NOTE_REPLACE, note="Holiday")
    assert bulk_svc.plan(ctx, [spread.id], note).edits == []  # a spread follows its exchange

    changed = bulk_svc.bulk_edit(ctx, [exchange.id, spread.id], note)
    assert [tx.id for tx in changed] == [exchange.id]
    after = get(ctx, exchange)
    assert after.comment == "Holiday" and after.linked_id == spread.id and after.to_amount == exchange.to_amount
    assert get(ctx, spread) == spread  # amount, link and its own note unchanged
    assert tx_svc.received_amount(ctx, after) == 110.5
    # an account or category never applies to an exchange
    assert (
        bulk_svc.plan(ctx, [exchange.id], BulkChanges(account=setup["chf"].id, expenditure_category="TAX")).edits == []
    )
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert get(ctx, exchange).comment == ""


def test_booked_rows_keep_their_series(ctx, setup):
    series = rec_svc.save(
        ctx,
        RecurringInput(
            type="expenditure",
            amount=1900,
            currency="CHF",
            category="04.1",
            from_account=setup["chf"].id,
            start=date(2026, 7, 31),
        ),
    )
    first = rec_svc.book(ctx, series.id, date(2026, 7, 31))
    second = rec_svc.book(ctx, series.id, date(2026, 8, 31))
    bulk_svc.bulk_edit(ctx, ids(first, second), BulkChanges(expenditure_category="04.1.1"))
    assert {get(ctx, first).recurring_id, get(ctx, second).recurring_id} == {series.id}
    assert get(ctx, first).category == "04.1.1"
    assert ctx.store.recurring.get(series.id) == rec_svc.series(ctx)[0]  # the series itself stays
    assert ctx.store.recurring.get(series.id).category == "04.1"
    assert rec_svc.last_booked(ctx)[series.id] == date(2026, 8, 31)


def test_advances_and_repayments_move_with_their_rules(ctx, setup):
    wallet = acc_svc.add_account(ctx, "Wallet", "EUR")
    advance = spend(ctx, setup["eur"], 90, "EUR", "11.1", day=12, is_advance=True, advance_value=50, debtor="Clara")
    repay = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 14),
            type="income",
            amount=45,
            currency="EUR",
            to_account=setup["eur"].id,
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    sel = bulk_svc.selection(ctx, ids(advance, repay))
    assert sel.taking(bulk_svc.takes_income_category) == []  # a repayment has no category
    bonus = acc_svc.add_income_category(ctx, "Bonus")
    changed = bulk_svc.bulk_edit(ctx, ids(advance, repay), BulkChanges(account=wallet.id, income_category=bonus.id))
    assert len(changed) == 2
    moved, repaid = get(ctx, advance), get(ctx, repay)
    assert (
        moved.from_account == wallet.id
        and moved.advance_amount == 45
        and moved.advance_account == advance.advance_account
    )
    assert repaid.to_account == wallet.id and repaid.settles_id == advance.id and repaid.category == ""
    assert tx_svc.settled_amount(ctx, advance.id) == 45


def test_row_label_uses_the_swiss_amount_format(ctx, setup):
    tx = spend(ctx, setup["chf"], 12345.6)
    assert bulk_svc.row_label(tx) == "3 Oct 2026 · 12'345.60 CHF"


# ------------------------------------------------------------------ payees (F09)
def test_a_payee_goes_to_expenditure_and_income_and_is_added_when_saved(ctx, mixed):
    rows = ids(mixed["food"], mixed["salary"], mixed["transfer"])
    planned = bulk_svc.plan(ctx, rows, BulkChanges(payee="Migros"))
    assert len(planned.edits) == 2 and payee_svc.find(ctx, "Migros") is None  # checking adds no payee
    changed = bulk_svc.bulk_edit(ctx, rows, BulkChanges(payee="Migros"))
    assert {tx.id for tx in changed} == {mixed["food"].id, mixed["salary"].id}
    assert get(ctx, mixed["food"]).payee == get(ctx, mixed["salary"]).payee == "Migros"
    assert get(ctx, mixed["transfer"]).payee == ""  # transfers have no payee
    assert [p.name for p in payee_svc.all_payees(ctx)] == ["Migros"]
    assert bulk_svc.plan(ctx, rows, BulkChanges(payee="MIGROS")).edits == []  # the same payee: nothing changes
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert get(ctx, mixed["food"]).payee == "" and payee_svc.all_payees(ctx) == []  # one step, the payee too


def test_a_failing_edit_adds_no_payee(ctx, setup):
    advance = spend(ctx, setup["chf"], 100, is_advance=True, advance_value=50, debtor="Anna")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 9),
            type="income",
            amount=50,
            currency="CHF",
            to_account=setup["chf"].id,
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    edited = tx_svc.edit_input(ctx, get(ctx, advance))
    edited.payee, edited.is_advance = "Brand New Shop", False  # already repaid: can't stop being an advance
    with pytest.raises(ValidationError, match="already been repaid"):
        tx_svc.save(ctx, edited, editing_id=advance.id)
    assert payee_svc.find(ctx, "Brand New Shop") is None
