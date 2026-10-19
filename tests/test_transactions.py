from dataclasses import replace
from datetime import date

import pytest

from budget_app.domain.coicop import ADV
from budget_app.domain.models import ACCOUNT_ADVANCE, TX_EXCHANGE, TX_EXPENDITURE, TX_INCOME, TX_TRANSFER
from budget_app.services import accounts as acc_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput


def expense(setup, **kw):
    base = dict(
        date=date(2026, 9, 10),
        type=TX_EXPENDITURE,
        amount=100.0,
        currency="CHF",
        category="01.1.1",
        from_account=setup["chf"].id,
    )
    base.update(kw)
    return TransactionInput(**base)


def test_expenditure_stores_base_amount_and_fx_date(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup, currency="EUR", from_account=setup["eur"].id))
    assert tx.amount_chf == pytest.approx(95.0)  # 100 EUR * 0.95, the rate of 29 Aug, last before 10 Sep
    assert tx.fx_date == date(2026, 8, 29)


def test_validation_rules(ctx, setup):
    with pytest.raises(ValidationError):
        tx_svc.save(ctx, expense(setup, amount=-5))
    with pytest.raises(ValidationError):  # account currency mismatch
        tx_svc.save(ctx, expense(setup, from_account=setup["eur"].id))
    with pytest.raises(ValidationError):
        tx_svc.save(ctx, expense(setup, category=""))


def test_advance_payment_creates_default_account_and_splits(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup, is_advance=True, advance_mode="percent", advance_value=60, debtor="Anna"))
    adv_acc = ctx.store.accounts.get(tx.advance_account)
    assert adv_acc.kind == ACCOUNT_ADVANCE and adv_acc.currency == "CHF"
    assert tx.advance_amount == 60.0
    assert reports.expenditure_parts(ctx, tx, "CHF") == [("01.1.1", pytest.approx(40.0)), (ADV, pytest.approx(60.0))]

    open_items = tx_svc.open_advances(ctx, adv_acc.id)
    assert [o.outstanding for o in open_items] == [60.0]

    repay = TransactionInput(
        date=date(2026, 9, 20),
        type=TX_INCOME,
        amount=25.0,
        currency="CHF",
        to_account=setup["chf"].id,
        is_advance=True,
        advance_account=adv_acc.id,
        settles_id=tx.id,
    )
    tx_svc.save(ctx, repay)
    assert tx_svc.advance_account_balance(ctx, adv_acc.id) == 35.0
    with pytest.raises(ValidationError):  # more than outstanding
        tx_svc.save(ctx, TransactionInput(**{**repay.__dict__, "amount": 40.0}))
    with pytest.raises(ValidationError):  # advance with repayment cannot be deleted alone
        tx_svc.delete(ctx, [tx.id])
    with pytest.raises(ValidationError):  # account with transactions cannot be deleted
        acc_svc.delete_account(ctx, adv_acc.id)


def test_transfer_and_exchange(ctx, setup):
    other = acc_svc.add_account(ctx, "Savings", "CHF")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type=TX_TRANSFER,
            amount=10,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=other.id,
        ),
    )
    ex = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type=TX_EXCHANGE,
            amount=90,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=93.80,
        ),
    )
    # 93.80 EUR received, the ECB rate gives 94.74: the exchange keeps the ECB amount and the
    # 0.94 EUR spread is booked as a linked revaluation (a loss) on the To account (F44)
    assert ex.to_currency == "EUR" and ex.to_amount == pytest.approx(94.74)
    spread = ctx.store.transactions.get(ex.linked_id)
    assert spread.type == "revaluation" and spread.category == "REVAL_FX" and spread.linked_id == ex.id
    assert (spread.amount, spread.from_account, spread.to_account) == (pytest.approx(0.94), setup["eur"].id, "")
    assert tx_svc.received_amount(ctx, ex) == pytest.approx(93.80)
    assert tx_svc.edit_input(ctx, ex).to_amount == pytest.approx(93.80)
    # a spread booking can't be edited or deleted on its own
    with pytest.raises(ValidationError, match="belongs to a currency exchange"):
        tx_svc.save(ctx, tx_svc.to_input(spread), editing_id=spread.id)
    with pytest.raises(ValidationError, match="Delete the exchange instead"):
        tx_svc.delete(ctx, [spread.id])
    # editing the exchange to the ECB amount removes the spread booking; a gain books it the other way
    edited = tx_svc.save(ctx, replace(tx_svc.edit_input(ctx, ex), to_amount=94.74), editing_id=ex.id)
    assert edited.linked_id == "" and ctx.store.transactions.get(spread.id) is None
    gain = tx_svc.save(ctx, replace(tx_svc.edit_input(ctx, edited), to_amount=95.00), editing_id=ex.id)
    booked = ctx.store.transactions.get(gain.linked_id)
    assert (booked.to_account, booked.amount) == (setup["eur"].id, pytest.approx(0.26))
    # deleting the exchange takes its spread booking along
    assert tx_svc.delete(ctx, [ex.id]) == 2
    with pytest.raises(ValidationError, match="amount received"):
        tx_svc.save(
            ctx,
            TransactionInput(
                date=date(2026, 9, 1),
                type=TX_EXCHANGE,
                amount=90,
                currency="CHF",
                from_account=setup["chf"].id,
                to_account=setup["eur"].id,
            ),
        )
    with pytest.raises(ValidationError):
        tx_svc.save(
            ctx,
            TransactionInput(
                date=date(2026, 9, 1),
                type=TX_TRANSFER,
                amount=10,
                currency="CHF",
                from_account=setup["chf"].id,
                to_account=setup["chf"].id,
            ),
        )


def test_income_category_and_account_deletion_rules(ctx, setup):
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=5000,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    with pytest.raises(ValidationError):
        acc_svc.delete_income_category(ctx, setup["salary"].id)
    unused = acc_svc.add_income_category(ctx, "Gifts")
    acc_svc.delete_income_category(ctx, unused.id)
    with pytest.raises(ValidationError):
        acc_svc.delete_account(ctx, setup["chf"].id)


def test_edit_keeps_valuation_and_locks_repaid_advance(ctx, setup, rates):
    tx = tx_svc.save(ctx, expense(setup, is_advance=True, advance_mode="amount", advance_value=50, debtor="Ben"))
    original = (tx.amount_chf, tx.fx_date)
    adv_acc = tx.advance_account
    other_adv = acc_svc.add_account(ctx, "Trip", "CHF", ACCOUNT_ADVANCE)
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 20),
            type=TX_INCOME,
            amount=10.0,
            currency="CHF",
            to_account=setup["chf"].id,
            is_advance=True,
            advance_account=adv_acc,
            settles_id=tx.id,
        ),
    )
    edit = tx_svc.to_input(tx)
    edit.comment = "changed"
    edited = tx_svc.save(ctx, edit, editing_id=tx.id)
    assert (edited.amount_chf, edited.fx_date) == original and edited.comment == "changed"
    edit.advance_account = other_adv.id
    with pytest.raises(ValidationError):
        tx_svc.save(ctx, edit, editing_id=tx.id)


def test_store_get_returns_copy(ctx, setup):
    acc = ctx.store.accounts.get(setup["chf"].id)
    acc.name = "mutated"
    assert ctx.store.accounts.get(setup["chf"].id).name == "Bank"


def test_transactions_use_the_rate_of_their_date(ctx, setup):
    base = dict(type="expenditure", amount=100, currency="EUR", category="01.1", from_account=setup["eur"].id)
    manual = tx_svc.save(ctx, TransactionInput(date=date(2026, 9, 15), **base))
    imported = tx_svc.save(ctx, TransactionInput(date=date(2026, 9, 15), import_ref="f1", **base))
    # manual and imported alike: the last rate on or before 15 Sep (29 Aug: 0.95 CHF per EUR)
    for tx in (manual, imported):
        assert tx.fx_date == date(2026, 8, 29) and tx.amount_chf == pytest.approx(95.0)
    future = tx_svc.save(ctx, TransactionInput(date=date(2026, 12, 24), **base))
    assert future.fx_date == date(2026, 10, 16)  # no rate yet: the latest available

    # editing the date revalues; editing the note does not
    moved = tx_svc.save(ctx, TransactionInput(date=date(2026, 10, 1), **base), editing_id=manual.id)
    assert moved.fx_date == date(2026, 9, 30) and moved.amount_chf == pytest.approx(94.0)
    noted = tx_svc.save(ctx, TransactionInput(date=date(2026, 10, 1), comment="x", **base), editing_id=manual.id)
    assert noted.fx_date == date(2026, 9, 30)


def test_revalue_to_transaction_dates(ctx, setup):
    base = dict(type="expenditure", amount=100, currency="EUR", category="01.1", from_account=setup["eur"].id)
    tx = tx_svc.save(ctx, TransactionInput(date=date(2026, 9, 15), **base))
    chf = tx_svc.save(ctx, expense(setup, date=date(2026, 9, 15)))
    # as an older version stored them: valued at the then latest rate
    for old in (tx, chf):
        old.fx_date = date(2026, 10, 16)
        old.amount_chf = 90.0 if old.currency == "EUR" else old.amount_chf
    ctx.store.transactions.update_many([tx, chf])

    assert tx_svc.revalue_to_transaction_dates(ctx) == 2
    fixed = ctx.store.transactions.get(tx.id)
    assert fixed.fx_date == date(2026, 8, 29) and fixed.amount_chf == pytest.approx(95.0)
    assert ctx.store.transactions.get(chf.id).fx_date == date(2026, 8, 29)  # CHF rows: same amount, new date
    assert tx_svc.revalue_to_transaction_dates(ctx) == 0  # nothing left to do


def test_revaluation_entered_as_values_or_difference(ctx, setup):
    eur = setup["eur"].id
    base = dict(date=date(2026, 9, 30), type="revaluation", currency="EUR", category="REVAL_INVEST", reval_account=eur)
    gain = tx_svc.save(ctx, TransactionInput(reval_mode="values", reval_old=1000, reval_new=1080, **base))
    assert (gain.amount, gain.to_account, gain.from_account) == (80, eur, "")
    loss = tx_svc.save(ctx, TransactionInput(reval_change=-25.5, **base))
    assert (loss.amount, loss.to_account, loss.from_account) == (25.5, "", eur)
    assert tx_svc.signed_revaluation(loss) == -25.5 and tx_svc.revaluation_account(loss) == eur
    back = tx_svc.to_input(loss)
    assert (back.reval_mode, back.reval_change, back.reval_account) == ("difference", -25.5, eur)
    # turning a gain into a loss moves it to the other side
    flipped = tx_svc.save(ctx, TransactionInput(reval_change=-80, **base), editing_id=gain.id)
    assert (flipped.to_account, flipped.from_account) == ("", eur)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (dict(reval_change=None), "Enter the change in value"),
        (dict(reval_mode="values", reval_old=5), "Enter the old and the new value"),
        (dict(reval_change=0.001), "did not change"),
        (dict(reval_change=5, category="01.1"), "kind of revaluation"),
        (dict(reval_change=5, currency="CHF"), "currency of its account"),
        (dict(reval_change=5, reval_account=""), "Select an account"),
    ],
)
def test_revaluation_validation(ctx, setup, change, message):
    inp = dict(
        date=date(2026, 9, 30), type="revaluation", currency="EUR", category="REVAL_FX", reval_account=setup["eur"].id
    )
    inp.update(change)
    with pytest.raises(ValidationError, match=message):
        tx_svc.save(ctx, TransactionInput(**inp))


def test_a_new_rate_splits_the_exchange_again(ctx, setup):
    ex = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type=TX_EXCHANGE,
            amount=90,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=93.80,
        ),
    )
    # as if it had been saved with the newest rate (16 Oct: 0.90 CHF per EUR -> 100 EUR)
    stale = ctx.store.transactions.get(ex.id)
    stale.fx_date, stale.to_amount = date(2026, 10, 16), 100.0
    ctx.store.transactions.update(stale)
    spread = ctx.store.transactions.get(ex.linked_id)
    spread.amount, spread.from_account = 6.2, setup["eur"].id
    ctx.store.transactions.update(spread)

    assert tx_svc.revalue_to_transaction_dates(ctx) >= 1
    fixed = ctx.store.transactions.get(ex.id)
    assert fixed.fx_date == date(2026, 8, 29) and fixed.to_amount == pytest.approx(94.74)
    assert tx_svc.received_amount(ctx, fixed) == pytest.approx(93.80)  # what the bank credited stays
