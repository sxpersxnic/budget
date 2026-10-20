"""Undo in the confirmation toast and the Change History page (F28)."""

from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import undo_toasts

from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui.layout import undoable


def save_expense(ctx, setup, amount: float = 42.0):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 10),
            type="expenditure",
            amount=amount,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
        ),
    )


async def test_the_toast_offers_undo_for_the_last_change(user: User, ctx, setup) -> None:
    await user.open("/transactions")
    tx = save_expense(ctx, setup)
    with user:
        undoable("Transaction added")
    [toast] = undo_toasts(user)
    options = toast.props["options"]
    assert options["message"] == "Transaction added"
    assert options["actions"][0]["label"] == "Undo" and "emit('undo')" in options["actions"][0][":handler"]
    UserInteraction(user, {toast}, None).trigger("undo")  # what the button's handler sends
    assert ctx.store.transactions.get(tx.id) is None
    await user.should_see("Undone: Save a transaction")


async def test_undo_that_fails_is_reported(user: User, ctx, setup) -> None:
    await user.open("/transactions")
    tx = save_expense(ctx, setup)
    with user:
        undoable("Transaction added")
    save_expense(ctx, setup, amount=1)  # a later change: the toast's action is no longer the last one
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 10),
            type="expenditure",
            amount=77,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
        ),
        editing_id=tx.id,
    )
    [toast] = undo_toasts(user)
    UserInteraction(user, {toast}, None).trigger("undo")
    await user.should_see("This change can’t be undone")
    assert ctx.store.transactions.get(tx.id).amount == 77


async def test_no_undo_button_without_an_open_action(user: User, ctx) -> None:
    await user.open("/transactions")
    with user:
        undoable("Nothing to undo")
    assert undo_toasts(user) == []
    assert user.notify.contains("Nothing to undo")


async def test_history_page_lists_changes_and_undoes_them(user: User, ctx, setup) -> None:
    tx = save_expense(ctx, setup)
    await user.open("/history")
    await user.should_see("Change History")
    await user.should_see("Save a transaction")
    await user.should_see("Add an income category")  # from the setup fixture
    await user.should_see("Transaction added: 2026-09-10 · 42.0 CHF")  # in the details
    newest = min((b for b in user.find(kind=ui.button).elements if b.text == "Undo"), key=lambda b: b.id)
    UserInteraction(user, {newest}, None).click()  # rows are built newest first
    assert ctx.store.transactions.get(tx.id) is None
    await user.should_see("Undo: Save a transaction")
    await user.should_see("undone")


async def test_history_page_reports_a_failed_undo(user: User, ctx, setup) -> None:
    tx = save_expense(ctx, setup)
    await user.open("/history")
    tx_svc.delete(ctx, [tx.id])  # changed behind the page's back
    newest = min((b for b in user.find(kind=ui.button).elements if b.text == "Undo"), key=lambda b: b.id)
    UserInteraction(user, {newest}, None).click()
    await user.should_see("This change can’t be undone")


async def test_empty_history_and_the_settings_link(user: User, ctx) -> None:
    assert undo_svc.recent(ctx) == []
    await user.open("/history")
    await user.should_see("No changes yet.")
    await user.open("/settings")
    await user.should_see("Change History")
    user.find("Show…").click()
    await user.should_see("The last changes to your records. Undo puts the records back")
