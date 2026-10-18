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
    assert tx.amount_chf == pytest.approx(90.0)  # 100 EUR * 0.90 (latest rate)
    assert tx.fx_date == date(2026, 10, 16)


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
        ),
    )
    assert ex.to_currency == "EUR" and ex.to_amount == pytest.approx(100.0)
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


def test_imported_transactions_use_the_rate_of_their_booking_date(ctx, setup):
    base = dict(type="expenditure", amount=100, currency="EUR", category="01.1", from_account=setup["eur"].id)
    manual = tx_svc.save(ctx, TransactionInput(date=date(2026, 9, 15), **base))
    imported = tx_svc.save(ctx, TransactionInput(date=date(2026, 9, 15), import_ref="f1", **base))
    # manual entries: latest ECB rate (16 Oct: 0.90 CHF per EUR); imports: last rate on or before 15 Sep
    assert manual.fx_date == date(2026, 10, 16) and manual.amount_chf == pytest.approx(90.0)
    assert imported.fx_date == date(2026, 8, 29) and imported.amount_chf == pytest.approx(95.0)

    # editing the date of an imported row revalues it; editing the note does not
    moved = tx_svc.save(ctx, TransactionInput(date=date(2026, 10, 1), import_ref="f1", **base), editing_id=imported.id)
    assert moved.fx_date == date(2026, 9, 30) and moved.amount_chf == pytest.approx(94.0)
    noted = tx_svc.save(
        ctx,
        TransactionInput(date=date(2026, 10, 1), import_ref="f1", comment="x", **base),
        editing_id=imported.id,
    )
    assert noted.fx_date == date(2026, 9, 30)
