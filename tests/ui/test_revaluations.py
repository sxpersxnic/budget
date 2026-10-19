"""Milestone 3 in the interface: revaluations (F43), exchange spreads (F44), the Revaluations card
(F45) and booking an account's unexplained difference (F46)."""

import io
from datetime import date

import pandas as pd
from nicegui import ui
from nicegui.testing import User
from test_budget_page import PREV, downloaded, menu_item, open_budget, open_dialog
from test_budget_page import click as click_async
from test_transaction_form import caption_texts, click, field, put, save_button, type_toggle

from budget_app.domain import months
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.transaction_form import open_transaction_sheet


def revaluations(ctx) -> list:
    return [tx for tx in ctx.store.transactions.all() if tx.type == "revaluation"]


# ------------------------------------------------------------------ the form (F43)
async def test_a_loss_entered_as_a_change(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, type_toggle(user), "revaluation")
    assert not field(user, ui.select, "Account").value
    assert not save_button(user).enabled
    put(user, field(user, ui.select, "Account"), setup["eur"].id)
    put(user, field(user, ui.select, "Kind"), "REVAL_FX")
    put(user, field(user, ui.number, "Change in value"), -25.5)
    assert "A loss of 25.50 EUR" in caption_texts(user)
    click(user, save_button(user))
    [loss] = revaluations(ctx)
    assert (loss.amount, loss.currency, loss.category) == (25.5, "EUR", "REVAL_FX")
    assert (loss.from_account, loss.to_account) == (setup["eur"].id, "")


async def test_a_gain_entered_as_old_and_new_value(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, type_toggle(user), "revaluation")
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    mode = next(e for e in user.find(kind=ui.toggle).elements if "values" in e.options)
    put(user, mode, "values")
    put(user, field(user, ui.number, "Old value"), 1000)
    assert not save_button(user).enabled  # the new value is still missing
    put(user, field(user, ui.number, "New value"), 1080)
    assert "A gain of 80.00 CHF" in caption_texts(user)
    click(user, save_button(user))
    [gain] = revaluations(ctx)
    assert (gain.amount, gain.to_account, gain.category) == (80, setup["chf"].id, "REVAL_INVEST")


async def test_editing_a_spread_booking_opens_its_exchange(user: User, ctx, setup) -> None:
    ex = tx_svc.save(
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
    spread = ctx.store.transactions.get(ex.linked_id)
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=spread)
    await user.should_see("Edit Transaction")
    assert type_toggle(user).value == "exchange"
    assert field(user, ui.number, "Amount Received").value == 93.80


async def test_the_list_shows_revaluations_and_received_amounts(user: User, ctx, setup) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 30),
            type="revaluation",
            currency="EUR",
            category="REVAL_INVEST",
            reval_account=setup["eur"].id,
            reval_change=12,
        ),
    )
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
    await user.open("/transactions")
    [grid] = user.find(kind=ui.aggrid).elements
    rows = grid.options["rowData"]
    gain = next(r for r in rows if r["category"] == "Capital gains on investment")
    assert gain["_type"] == "income" and gain["type"] == "Revaluation"  # shown like income: signed, green
    exchange = next(r for r in rows if r["type"] == "Currency exchange")
    assert "+93.80 EUR" in exchange["to"]  # what the bank credited, spread included


# ------------------------------------------------------------------ Budget page (F45, F46)
async def test_revaluations_card_and_booking_an_unexplained_difference(user: User, ctx, demo) -> None:
    rep = reports.month_report(ctx, PREV, "CHF")
    eur = next(r for r in rep.accounts if r.account.id == demo["eur"].id)
    assert eur.unexplained is not None and abs(eur.unexplained) > 0.005  # the demo balance moves unbooked
    await open_budget(user, PREV)
    await user.should_see("Revaluations")
    await user.should_see("Capital gains on currency exchange")
    await user.should_see("Capital gains on investment")
    await user.should_see("Gains and losses in value")  # the reconciliation's revaluation metric

    book = [b for b in user.find(kind=ui.button).elements if b.props.get("icon") == "o_post_add"]
    assert book, "no Book as revaluation button"
    await click_async(user, book[0])
    dialog = open_dialog(user)
    assert dialog.value
    assert type_toggle(user).value == "revaluation"
    booked_account = field(user, ui.select, "Account").value
    row = next(r for r in rep.accounts if r.account.id == booked_account)
    assert field(user, ui.number, "Change in value").value == row.unexplained
    click(user, save_button(user))
    [reval] = revaluations(ctx)
    assert reval.date == months.last_day(PREV) and reval.category == "REVAL_INVEST"
    after = reports.month_report(ctx, PREV, "CHF")
    assert next(r for r in after.accounts if r.account.id == booked_account).unexplained == 0


async def test_statement_export_lists_revaluations(user: User, ctx, demo) -> None:
    y, m = map(int, PREV.split("-"))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(y, m, 20),
            type="revaluation",
            currency="CHF",
            category="REVAL_INVEST",
            reval_account=demo["chf"].id,
            reval_change=150,
        ),
    )
    await open_budget(user, PREV)
    await menu_item(user, "Export as Excel…")
    sheets = pd.read_excel(io.BytesIO((await downloaded(user)).content), sheet_name=None)
    assert {"Reconciliation", "Revaluations", "Accounts"} <= set(sheets)
    reval = sheets["Revaluations"]
    assert 150 in set(reval.iloc[:, 1].round(2))
    assert "Unexplained" in sheets["Accounts"].columns
    assert "Revaluations" in set(sheets["Reconciliation"].iloc[:, 0])


async def test_a_month_without_revaluations_in_progress_hides_the_card(user: User, ctx, demo) -> None:
    await open_budget(user)  # the running month: no balances yet, nothing booked
    await user.should_not_see("Gains and losses in value, outside net income")
