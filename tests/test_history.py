"""Change history and undo (F28): recording, grouping, undo and its safety checks."""

from datetime import date

import pytest

from budget_app.domain.models import Account, TransactionItem
from budget_app.i18n import N_
from budget_app.services import budget as budget_svc
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import ValidationError
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.history import History, UndoConflict
from budget_app.storage.store import Store


def expense(setup, amount=42.0, **kw):
    base = dict(
        date=date(2026, 9, 10),
        type="expenditure",
        amount=amount,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
    )
    base.update(kw)
    return TransactionInput(**base)


def test_each_service_call_is_one_named_action(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup))
    act = undo_svc.latest(ctx)
    assert act.label == "Save a transaction" and [(c.table, c.op) for c in act.changes] == [("transactions", "add")]
    assert act.changes[0].after["amount"] == "42.0" and act.changes[0].before is None
    tx_svc.delete(ctx, [tx.id])
    assert undo_svc.latest(ctx).label == "Delete transactions"
    assert undo_svc.summary(undo_svc.latest(ctx)) == "1 change"


def test_nested_actions_join_the_outer_one(ctx, setup):
    with ctx.store.history.action(N_("Save a transaction")):
        tx = tx_svc.save(ctx, expense(setup))
        items_svc.replace(ctx, tx.id, [ItemInput("Bread", 1, 3.5), ItemInput("Milk", 2, 1.2)])
    act = undo_svc.latest(ctx)
    assert sorted({c.table for c in act.changes}) == ["transaction_items", "transactions"]
    assert len(act.changes) == 3
    undo_svc.undo(ctx, act.id)  # the transaction and its items go together
    assert ctx.store.transactions.all() == [] and ctx.store.transaction_items.all() == []


def test_undo_restores_an_edit_and_a_delete(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup))
    edited = tx_svc.save(ctx, expense(setup, amount=99, comment="typo"), editing_id=tx.id)
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.transactions.get(tx.id).amount == 42 and ctx.store.transactions.get(tx.id).comment == ""
    tx_svc.delete(ctx, [edited.id])
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.transactions.get(tx.id).amount == 42


def test_undo_refuses_when_records_changed_since(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup))
    first = undo_svc.latest(ctx)
    tx_svc.save(ctx, expense(setup, amount=50), editing_id=tx.id)
    with pytest.raises(ValidationError, match="changed afterwards"):
        undo_svc.undo(ctx, first.id)
    assert ctx.store.transactions.get(tx.id).amount == 50  # nothing was touched
    latest = undo_svc.latest(ctx)
    undo_svc.undo(ctx, latest.id)
    with pytest.raises(ValidationError, match="already been undone"):
        undo_svc.undo(ctx, latest.id)
    with pytest.raises(ValidationError, match="no longer in the history"):
        undo_svc.undo(ctx, "nope")


def test_undo_of_an_undo_redoes(ctx, setup):
    tx_svc.save(ctx, expense(setup))
    saved = undo_svc.latest(ctx)
    undo_svc.undo(ctx, saved.id)
    assert ctx.store.transactions.all() == []
    undoing = undo_svc.latest(ctx)
    assert undoing.undo_of == saved.id and undo_svc.label(ctx, undoing) == "Undo: Save a transaction"
    undo_svc.undo(ctx, undoing.id)
    assert len(ctx.store.transactions.all()) == 1
    assert undo_svc.label(ctx, undo_svc.latest(ctx)) == "Undo: Undo: Save a transaction"


def test_an_exchange_and_its_spread_undo_together(ctx, setup):
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type="exchange",
            amount=90,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=93.80,
        ),
    )
    act = undo_svc.latest(ctx)
    assert len(act.changes) == 2  # the exchange and its linked spread booking (F44)
    undo_svc.undo(ctx, act.id)
    assert ctx.store.transactions.all() == []


def test_other_services_are_recorded(ctx, setup):
    budget_svc.create(
        ctx, BudgetInput(start_month="2026-10", end_month="2026-12", currency="CHF", amount=100, category="01.1")
    )
    assert undo_svc.latest(ctx).label == "Add a budget entry" and len(undo_svc.latest(ctx).changes) == 3
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 1000)
    act = undo_svc.latest(ctx)
    assert act.label == "Record a balance"
    lines = [undo_svc.describe_change(ctx, c) for c in act.changes]
    assert lines == ["Balance added: 2026-09 · Bank · 1000.0"]
    undo_svc.undo(ctx, act.id)
    assert ctx.store.account_entries.all() == []


def test_describe_change_for_each_table(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup))
    ctx.store.transaction_items.add(TransactionItem(transaction_id=tx.id, name="Bread", quantity=1, unit_price=3))
    budget_svc.create(
        ctx, BudgetInput(start_month="2026-10", end_month="2026-10", currency="CHF", amount=5, category="01")
    )
    lines = [undo_svc.describe_change(ctx, c) for a in reversed(undo_svc.recent(ctx)) for c in a.changes]
    assert "Transaction added: 2026-09-10 · 42.0 CHF" in lines
    assert "Item added: Bread" in lines
    assert "Budget entry added: 2026-10 · 01 · 5.0 CHF" in lines
    assert any(line.startswith("Income category added: Salary") for line in lines)


def test_history_survives_a_restart_and_keeps_the_newest(tmp_path):
    store = Store(tmp_path)
    store.history.keep = 3
    for i in range(5):
        with store.history.action(N_("Add an account")):
            store.accounts.add(Account(name=f"A{i}", currency="CHF"))
    again = Store(tmp_path)
    names = [a.changes[0].after["name"] for a in again.history.actions()]
    assert names == ["A4", "A3", "A2"]  # newest first, only the last three
    # a change outside any action is an action of its own, without a name
    again.accounts.delete([again.accounts.all()[0].id])
    assert again.history.actions()[0].label == ""


def test_unchanged_updates_are_not_recorded(ctx, setup):
    acc = ctx.store.accounts.get(setup["chf"].id)
    before = len(ctx.store.history.actions())
    ctx.store.accounts.update(acc)
    assert len(ctx.store.history.actions()) == before


def test_storage_undo_errors(tmp_path):
    history = History(tmp_path / "h.jsonl")
    with pytest.raises(KeyError):
        history.undo("missing", {}, "Undo")
    store = Store(tmp_path / "data")
    store.accounts.add(Account(name="X", currency="CHF"))
    act = store.history.actions()[0]
    with pytest.raises(UndoConflict, match="unknown table"):
        store.history.undo(act.id, {}, "Undo")


def test_recorded_needs_the_context_first():
    @undo_svc.recorded(N_("Save items"))
    def broken(_x):
        return 1

    with pytest.raises(TypeError, match="first argument"):
        broken("not a context")
