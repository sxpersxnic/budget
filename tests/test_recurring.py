"""Recurring transactions (F05): schedule, the Due list, booking, skipping, editing and undo."""

import json
import logging
from datetime import date

import pytest

from budget_app.domain.models import Recurring
from budget_app.services import accounts as acc_svc
from budget_app.services import export as export_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store

TODAY = date(2026, 10, 18)


def rent(setup, **kw) -> RecurringInput:
    base = dict(
        type="expenditure",
        amount=1900,
        currency="CHF",
        category="04.1",
        from_account=setup["chf"].id,
        start=date(2026, 7, 31),
    )
    base.update(kw)
    return RecurringInput(**base)


def dates(occurrences) -> list[date]:
    return [o.date for o in occurrences]


# ------------------------------------------------------------------ schedule
def test_the_31st_falls_on_the_last_day_of_shorter_months():
    start = date(2027, 1, 31)
    assert [rec_svc.occurrence(start, 1, k) for k in range(4)] == [
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]
    assert rec_svc.occurrence(date(2028, 1, 31), 1, 1) == date(2028, 2, 29)  # leap year
    assert rec_svc.occurrence(date(2026, 11, 30), 3, 1) == date(2027, 2, 28)  # across the year


def test_next_and_previous_occurrence():
    start = date(2026, 1, 31)
    assert rec_svc.next_after(start, 1, date(2025, 12, 1)) == start
    assert rec_svc.next_after(start, 1, start) == date(2026, 2, 28)
    assert rec_svc.next_after(start, 1, date(2026, 2, 28)) == date(2026, 3, 31)
    assert rec_svc.next_after(start, 1, date(2026, 3, 15)) == date(2026, 3, 31)
    assert rec_svc.next_after(start, 12, date(2026, 6, 1)) == date(2027, 1, 31)
    assert rec_svc.previous_before(start, 1, start) is None
    assert rec_svc.previous_before(start, 1, date(2026, 2, 28)) == start
    assert rec_svc.previous_before(start, 1, date(2026, 3, 1)) == date(2026, 2, 28)
    assert rec_svc.previous_before(start, 1, date(2026, 3, 31)) == date(2026, 2, 28)
    assert rec_svc.previous_before(start, 3, date(2026, 6, 1)) == date(2026, 4, 30)


# ------------------------------------------------------------------ due list
def test_missed_months_are_each_due(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    assert series.next_due == date(2026, 7, 31)
    due = rec_svc.due(ctx, TODAY)
    assert dates(due) == [date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)]
    assert [o.first for o in due] == [True, False, False]
    assert rec_svc.due_count(ctx, date(2026, 7, 30)) == 0
    assert ctx.store.transactions.all() == []  # nothing is booked without being asked


def test_booking_creates_a_transaction_through_save(ctx, setup):
    series = rec_svc.save(ctx, rent(setup, currency="EUR", from_account=setup["eur"].id, amount=100, comment=" x "))
    tx = rec_svc.book(ctx, series.id, date(2026, 7, 31))
    assert (tx.date, tx.type, tx.amount, tx.currency, tx.category) == (
        date(2026, 7, 31),
        "expenditure",
        100,
        "EUR",
        "04.1",
    )
    assert tx.from_account == setup["eur"].id and tx.recurring_id == series.id and tx.comment == "x"
    assert tx.amount_chf == pytest.approx(95)  # valued like any transaction: the first ECB rate covers July
    assert ctx.store.recurring.get(series.id).next_due == date(2026, 8, 31)
    assert dates(rec_svc.due(ctx, TODAY)) == [date(2026, 8, 31), date(2026, 9, 30)]
    assert rec_svc.last_booked(ctx) == {series.id: date(2026, 7, 31)}


def test_amount_and_date_can_change_before_booking(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    tx = rec_svc.book(ctx, series.id, date(2026, 7, 31), amount=1950.5, booking_date=date(2026, 8, 2))
    assert (tx.amount, tx.date) == (1950.5, date(2026, 8, 2))
    assert ctx.store.recurring.get(series.id).amount == 1900  # the series keeps its amount
    with pytest.raises(ValidationError, match="positive"):
        rec_svc.book(ctx, series.id, date(2026, 8, 31), amount=0)
    assert ctx.store.recurring.get(series.id).next_due == date(2026, 8, 31)  # a failed booking changes nothing


def test_occurrences_are_handled_in_order(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    with pytest.raises(ValidationError, match="earlier occurrences"):
        rec_svc.book(ctx, series.id, date(2026, 8, 31))
    rec_svc.book(ctx, series.id, date(2026, 7, 31))
    with pytest.raises(ValidationError, match="already been booked or skipped"):
        rec_svc.book(ctx, series.id, date(2026, 7, 31))
    with pytest.raises(ValidationError, match="not found"):
        rec_svc.book(ctx, "nope", date(2026, 7, 31))
    assert len(ctx.store.transactions.all()) == 1


def test_skip_drops_one_occurrence(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    rec_svc.skip(ctx, series.id, date(2026, 7, 31))
    assert ctx.store.transactions.all() == []
    assert dates(rec_svc.due(ctx, TODAY)) == [date(2026, 8, 31), date(2026, 9, 30)]
    assert undo_svc.latest(ctx).label == "Skip a recurring transaction"


def test_a_series_ends_on_its_end_date(ctx, setup):
    series = rec_svc.save(ctx, rent(setup, start=date(2026, 8, 15), end=date(2026, 9, 20)))
    assert dates(rec_svc.due(ctx, TODAY)) == [date(2026, 8, 15), date(2026, 9, 15)]
    rec_svc.book(ctx, series.id, date(2026, 8, 15))
    rec_svc.skip(ctx, series.id, date(2026, 9, 15))
    series = ctx.store.recurring.get(series.id)
    assert rec_svc.ended(series) and rec_svc.due(ctx, date(2027, 1, 1)) == []
    with pytest.raises(ValidationError, match="has ended"):
        rec_svc.skip(ctx, series.id, series.next_due)
    running = rec_svc.save(ctx, rent(setup, start=date(2026, 12, 1)))
    assert [s.id for s in rec_svc.series(ctx)] == [running.id, series.id]  # ended ones last


def test_book_all_uses_changes_and_leaves_failures_due(ctx, setup):
    a = rec_svc.save(ctx, rent(setup, start=date(2026, 9, 30)))
    b = rec_svc.save(ctx, rent(setup, start=date(2026, 9, 1), amount=50, category="08.3"))
    broken = rec_svc.save(ctx, rent(setup, start=date(2026, 8, 1), amount=10))
    stored = ctx.store.recurring.get(broken.id)
    stored.from_account = "gone"  # e.g. edited by hand in the file
    ctx.store.recurring.update(stored)
    booking = rec_svc.book_all(ctx, TODAY, {(b.id, date(2026, 10, 1)): (55.0, date(2026, 10, 2))})
    assert sorted((t.date, t.amount) for t in booking.booked) == [
        (date(2026, 9, 1), 50),
        (date(2026, 9, 30), 1900),
        (date(2026, 10, 2), 55),
    ]
    [(occ, reason)] = booking.failed  # its later occurrences were not tried
    assert occ.series.id == broken.id and occ.date == date(2026, 8, 1) and "From account" in reason
    assert dates(rec_svc.due(ctx, TODAY)) == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
    assert undo_svc.latest(ctx).label == "Book recurring transactions"
    assert ctx.store.recurring.get(a.id).next_due == date(2026, 10, 30)  # the 30th, as it started


def test_automatic_booking_books_only_automatic_series_once(ctx, setup, caplog):
    auto = rec_svc.save(ctx, rent(setup, auto=True))
    rec_svc.save(ctx, rent(setup, amount=30, category="08.3", start=date(2026, 9, 5)))
    booking = rec_svc.book_automatic(ctx, TODAY)
    assert [t.date for t in booking.booked] == [date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30)]
    assert all(t.recurring_id == auto.id for t in booking.booked)
    assert rec_svc.book_automatic(ctx, TODAY).booked == []  # idempotent
    assert rec_svc.book_automatic(ctx, date(2026, 10, 31)).booked[0].date == date(2026, 10, 31)
    assert dates(rec_svc.due(ctx, TODAY)) == [date(2026, 9, 5), date(2026, 10, 5)]  # the manual one waits
    assert undo_svc.latest(ctx).label == "Book recurring transactions automatically"
    stored = ctx.store.recurring.get(auto.id)
    stored.category = "nonsense"
    ctx.store.recurring.update(stored)
    with caplog.at_level(logging.WARNING):
        assert rec_svc.book_automatic(ctx, date(2027, 1, 1)).failed
    assert "not booked" in caplog.text


def test_automatic_booking_of_one_series(ctx, setup):
    a = rec_svc.save(ctx, rent(setup, auto=True))
    rec_svc.save(ctx, rent(setup, auto=True, amount=5))
    booking = rec_svc.book_automatic(ctx, date(2026, 8, 1), a.id)
    assert [t.recurring_id for t in booking.booked] == [a.id]


# ------------------------------------------------------------------ editing
def test_editing_changes_future_occurrences_only(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    booked = rec_svc.book(ctx, series.id, date(2026, 7, 31))
    edited = rec_svc.save(ctx, rent(setup, amount=2000, comment="new rent"), editing_id=series.id)
    assert edited.id == series.id and edited.next_due == date(2026, 8, 31)
    assert ctx.store.transactions.get(booked.id).amount == 1900  # what it booked stays
    tx = rec_svc.book(ctx, series.id, date(2026, 8, 31))
    assert (tx.amount, tx.comment) == (2000, "new rent")


def test_a_new_schedule_continues_after_the_last_handled_occurrence(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    rec_svc.book(ctx, series.id, date(2026, 7, 31))
    edited = rec_svc.save(ctx, rent(setup, start=date(2026, 7, 15)), editing_id=series.id)
    assert edited.next_due == date(2026, 8, 15)  # August isn't lost by moving the day
    quarterly = rec_svc.save(ctx, rent(setup, start=date(2026, 7, 15), recur_months=3), editing_id=series.id)
    assert quarterly.next_due == date(2026, 10, 15)


def test_editing_an_untouched_series_restarts_it(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    assert rec_svc.save(ctx, rent(setup, start=date(2026, 5, 1)), editing_id=series.id).next_due == date(2026, 5, 1)
    with pytest.raises(ValidationError, match="not found"):
        rec_svc.save(ctx, rent(setup), editing_id="nope")


def test_planned_next_due_for_the_form(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    rec_svc.skip(ctx, series.id, date(2026, 7, 31))
    assert rec_svc.planned_next_due(ctx, rent(setup), series.id) == date(2026, 8, 31)
    assert rec_svc.planned_next_due(ctx, rent(setup)) == date(2026, 7, 31)


def test_validation(ctx, setup):
    with pytest.raises(ValidationError, match="expenditure, income or account transfer"):
        rec_svc.save(ctx, rent(setup, type="exchange"))
    with pytest.raises(ValidationError, match="first date"):
        rec_svc.save(ctx, rent(setup, start=None))
    with pytest.raises(ValidationError, match="at least every month"):
        rec_svc.save(ctx, rent(setup, recur_months=0))
    with pytest.raises(ValidationError, match="Until must not be before"):
        rec_svc.save(ctx, rent(setup, end=date(2026, 1, 1)))
    with pytest.raises(ValidationError, match="expenditure category"):
        rec_svc.save(ctx, rent(setup, category=""))
    with pytest.raises(ValidationError, match="must be in CHF"):
        rec_svc.save(ctx, rent(setup, from_account=setup["eur"].id))
    assert ctx.store.recurring.all() == []


def test_income_and_transfer_series(ctx, setup):
    second = acc_svc.add_account(ctx, "Savings", "CHF")
    salary = rec_svc.save(
        ctx,
        RecurringInput(
            type="income",
            amount=6200,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            from_account=second.id,  # dropped: income has no From account
            start=date(2026, 9, 25),
        ),
    )
    assert salary.from_account == "" and rec_svc.title(ctx, salary) == "Salary"
    move = rec_svc.save(
        ctx,
        RecurringInput(
            type="transfer",
            amount=500,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            to_account=second.id,
            start=date(2026, 9, 26),
        ),
    )
    assert move.category == "" and rec_svc.title(ctx, move) == "Bank → Savings"
    tx = rec_svc.book(ctx, move.id, date(2026, 9, 26))
    assert (tx.type, tx.from_account, tx.to_account) == ("transfer", setup["chf"].id, second.id)
    assert rec_svc.title(ctx, rec_svc.save(ctx, rent(setup))) == "04.1 Rent"


# ------------------------------------------------------------------ from a transaction, undo, deleting
def test_make_recurring_continues_a_transaction(ctx, setup):
    tx = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 30),
            type="expenditure",
            amount=89,
            currency="CHF",
            category="08.3",
            from_account=setup["chf"].id,
            comment="Phone",
        ),
    )
    inp = rec_svc.from_transaction(tx)
    assert (inp.start, inp.amount, inp.category, inp.comment) == (tx.date, 89, "08.3", "Phone")
    series = rec_svc.save(ctx, inp, first_booked=tx.id)
    assert series.next_due == date(2026, 10, 30)
    assert ctx.store.transactions.get(tx.id).recurring_id == series.id
    assert rec_svc.last_booked(ctx) == {series.id: tx.date}
    assert rec_svc.can_continue(tx)
    with pytest.raises(ValidationError, match="Transaction not found"):
        rec_svc.save(ctx, inp, first_booked="nope")


def test_editing_a_booked_transaction_keeps_its_series(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    tx = rec_svc.book(ctx, series.id, date(2026, 7, 31))
    form_input = TransactionInput(  # what the edit form saves: no series id
        date=tx.date, type="expenditure", amount=1800, currency="CHF", category="04.1", from_account=setup["chf"].id
    )
    edited = tx_svc.save(ctx, form_input, editing_id=tx.id)
    assert edited.recurring_id == series.id and tx_svc.to_input(edited).recurring_id == series.id


def test_undo_of_a_booking_restores_the_due_occurrence(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    rec_svc.book(ctx, series.id, date(2026, 7, 31))
    act = undo_svc.latest(ctx)
    assert act.label == "Book a recurring transaction" and act.tables == {"transactions", "recurring"}
    assert any(
        undo_svc.describe_change(ctx, c).startswith("Recurring transaction changed: 2026-08-31") for c in act.changes
    )
    undo_svc.undo(ctx, act.id)
    assert ctx.store.transactions.all() == []
    assert ctx.store.recurring.get(series.id).next_due == date(2026, 7, 31)


def test_deleting_a_series_keeps_its_transactions(ctx, setup):
    series = rec_svc.save(ctx, rent(setup))
    tx = rec_svc.book(ctx, series.id, date(2026, 7, 31))
    assert rec_svc.delete(ctx, [series.id]) == 1
    assert ctx.store.recurring.all() == [] and ctx.store.transactions.get(tx.id) is not None


def test_accounts_and_categories_used_by_a_series_stay(ctx, setup):
    rec_svc.save(ctx, rent(setup))
    rec_svc.save(
        ctx,
        RecurringInput(
            type="income",
            amount=1,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            start=TODAY,
        ),
    )
    assert acc_svc.account_in_use(ctx, setup["chf"].id) and not acc_svc.account_in_use(ctx, setup["eur"].id)
    with pytest.raises(ValidationError, match="recurring transactions cannot be deleted"):
        acc_svc.delete_account(ctx, setup["chf"].id)
    assert acc_svc.income_category_in_use(ctx, setup["salary"].id)


# ------------------------------------------------------------------ files
def test_series_are_stored_in_recurring_csv(tmp_path, ctx, setup):
    series = rec_svc.save(ctx, rent(setup, end=date(2027, 6, 30), auto=True))
    open_ended = rec_svc.save(ctx, rent(setup))
    again = Store(ctx.store.data_dir)
    loaded = again.recurring.get(series.id)
    assert loaded == series and loaded.end_date == date(2027, 6, 30) and loaded.auto is True
    assert again.recurring.get(open_ended.id).end_date is None
    header = (ctx.store.data_dir / "recurring.csv").read_text().splitlines()[0]
    assert header == (
        "type,amount,currency,start,next_due,category,from_account,to_account,comment,recur_months,end,auto,payee,tags,id"
    )
    doc = json.loads(export_svc.everything_json(ctx))
    assert {r["id"] for r in doc["recurring"]} == {series.id, open_ended.id}


def test_transactions_written_before_f05_still_load(tmp_path):
    (tmp_path / "transactions.csv").write_text(
        "date,type,amount,currency,amount_chf,fx_date,category,from_account,id\n"
        "2026-09-01,expenditure,10.0,CHF,10.0,2026-08-29,01.1,a1,t1\n",
        encoding="utf-8",
    )
    store = Store(tmp_path)
    assert store.transactions.get("t1").recurring_id == ""
    assert isinstance(Recurring.__dataclass_fields__["end"].default, str)
