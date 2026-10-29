"""Loans and mortgages (F22): interest and schedules, the debt from a loan's bookings and the balance of its account,
due payments, booking and skipping them, adding, changing and deleting loans with undo, and the places that know about
loan accounts (wealth, balances, Settings' rules, the export)."""

import json
from datetime import date

import pytest

from budget_app.domain import loans as plan
from budget_app.domain.models import ASSET_CASH, ASSET_LOAN, TX_EXPENDITURE, TX_TRANSFER
from budget_app.services import accounts as acc_svc
from budget_app.services import export as export_svc
from budget_app.services import loans as loan_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import ValidationError
from budget_app.services.loans import LoanInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store

D = date
TODAY = D(2026, 10, 18)


def mortgage(setup, **kw) -> LoanInput:
    base = dict(
        name="Mortgage",
        currency="CHF",
        principal=500_000,
        start=D(2026, 1, 1),
        rate=1.5,
        recur_months=3,
        first_due=D(2026, 3, 31),
        amortization=2_500,
        cash_account=setup["chf"].id,
        lender="Bank XY",
    )
    return LoanInput(**(base | kw))


def transfer(setup, src: str, dst: str, amount: float, day: date) -> TransactionInput:
    return TransactionInput(date=day, type=TX_TRANSFER, amount=amount, currency="CHF", from_account=src, to_account=dst)


@pytest.fixture
def loan(ctx, setup):
    return loan_svc.add(ctx, mortgage(setup))


# ------------------------------------------------------------------ domain
def test_interest_for_a_period():
    assert plan.interest(500_000, 1.5, 3) == 1875.0
    assert plan.interest(10_000, 6, 1) == 50.0
    assert plan.interest(1_000.5, 1, 12) == 10.01  # 10.005 rounds half up, not to even
    assert plan.interest(0, 5, 1) == 0.0
    assert plan.interest(-20, 5, 1) == 0.0
    assert plan.interest(1000, 0, 1) == 0.0
    assert plan.interest(1000, 5, 0) == 0.0


def test_payment_dates_follow_the_month_end_rule():
    assert [plan.occurrence(D(2026, 1, 31), 1, k) for k in range(3)] == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31)]
    assert plan.occurrence(D(2026, 3, 31), 3, 1) == D(2026, 6, 30)


def test_a_schedule_amortizes_until_the_debt_is_gone():
    payments = plan.schedule(10_000, 6, 1, 2_500, D(2026, 11, 1))
    assert [p.interest for p in payments] == [50.0, 37.5, 25.0, 12.5]
    assert [p.amortization for p in payments] == [2_500] * 4
    assert [p.debt_after for p in payments] == [7_500, 5_000, 2_500, 0]
    assert [p.date for p in payments] == [D(2026, 11, 1), D(2026, 12, 1), D(2027, 1, 1), D(2027, 2, 1)]
    assert payments[0].total == 2_550.0
    assert plan.payoff(payments) == D(2027, 2, 1)
    assert plan.interest_total(payments) == 125.0
    odd = plan.schedule(1_000, 0, 1, 400, D(2026, 11, 1))  # the last payment takes what is left
    assert [p.amortization for p in odd] == [400, 400, 200] and odd[-1].debt_after == 0
    assert plan.schedule(0, 5, 1, 100, D(2026, 11, 1)) == []


def test_an_interest_only_schedule_stops_at_the_limit_or_a_date():
    forever = plan.schedule(200_000, 1.2, 3, 0, D(2026, 3, 31), limit=5)
    assert len(forever) == 5 and all(p.debt_after == 200_000 for p in forever)
    assert plan.payoff(forever) is None
    assert forever[0].interest == 600.0
    until = plan.schedule(200_000, 1.2, 3, 0, D(2026, 3, 31), until=D(2026, 10, 1))
    assert [p.date for p in until] == [D(2026, 3, 31), D(2026, 6, 30), D(2026, 9, 30)]
    assert len(plan.schedule(100, 1, 1, 0, D(2026, 1, 1))) == plan.MAX_PAYMENTS


def test_repaid_share():
    assert plan.repaid_share(1000, 250) == 0.75
    assert plan.repaid_share(1000, 1200) == 0.0  # more debt than at the start (a drawdown)
    assert plan.repaid_share(1000, -5) == 1.0
    assert plan.repaid_share(0, 500) == 0.0


# ------------------------------------------------------------------ adding and the debt
def test_add_a_loan_with_its_account(ctx, setup, loan):
    acc = ctx.store.accounts.get(loan.account_id)
    assert (acc.name, acc.currency, acc.asset_class, acc.is_loan) == ("Mortgage", "CHF", ASSET_LOAN, True)
    assert (loan.principal, loan.rate, loan.next_due, loan.first_due, loan.lender) == (
        500_000,
        1.5,
        D(2026, 3, 31),
        D(2026, 3, 31),
        "Bank XY",
    )
    assert loan.interest_category == "12.2.1"
    assert loan_svc.title(ctx, loan) == "Mortgage"
    assert loan_svc.of_account(ctx, acc.id) == loan
    assert undo_svc.latest(ctx).label == "Add a loan"
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)  # one step removes both
    assert ctx.store.loans.all() == [] and ctx.store.accounts.get(acc.id) is None
    assert "loans" in ctx.store.tables()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": " "}, "Name is required"),
        ({"name": "Bank"}, "already exists"),
        ({"currency": ""}, "Currency is required"),
        ({"principal": None}, "Enter the debt at the start"),
        ({"principal": -1}, "Enter the debt at the start"),
        ({"start": None}, "Select the start date"),
        ({"rate": None}, "Enter the interest rate"),
        ({"rate": -0.5}, "Enter the interest rate"),
        ({"recur_months": 0}, "at least every month"),
        ({"first_due": None}, "Select the first payment date"),
        ({"first_due": D(2025, 12, 31)}, "must not be before the start"),
        ({"amortization": -1}, "can’t be negative"),
        ({"cash_account": ""}, "Select the paying account"),
        ({"cash_account": "nope"}, "Select the paying account"),
        ({"interest_category": "99.9"}, "Select the interest category"),
        ({"fixed_until": D(2025, 1, 1)}, "Fixed until must not be before the start"),
    ],
)
def test_add_refuses(ctx, setup, changes, message):
    with pytest.raises(ValidationError, match=message):
        loan_svc.add(ctx, mortgage(setup, **changes))
    assert ctx.store.loans.all() == [] and len(ctx.store.accounts.all()) == 2


def test_the_paying_account_must_be_a_cash_account_in_the_currency(ctx, setup, loan):
    with pytest.raises(ValidationError, match="must be in CHF"):
        loan_svc.add(ctx, mortgage(setup, name="Car", cash_account=setup["eur"].id))
    with pytest.raises(ValidationError, match="Select the paying account"):
        loan_svc.add(ctx, mortgage(setup, name="Car", cash_account=loan.account_id))  # not from another loan
    with pytest.raises(ValidationError, match="Name is required"):
        loan_svc.validate(ctx, LoanInput())


def test_the_debt_follows_the_bookings(ctx, setup, loan):
    bank = setup["chf"].id
    assert loan_svc.debt(ctx, loan, TODAY) == 500_000
    tx_svc.save(ctx, transfer(setup, bank, loan.account_id, 2_500, D(2026, 3, 31)))
    tx_svc.save(ctx, transfer(setup, loan.account_id, bank, 10_000, D(2026, 5, 1)))  # a further drawdown
    assert loan_svc.debt(ctx, loan, D(2026, 3, 30)) == 500_000
    assert loan_svc.debt(ctx, loan, D(2026, 3, 31)) == 497_500
    assert loan_svc.debt(ctx, loan, TODAY) == 507_500
    assert loan_svc.debt(ctx, loan) == 507_500  # today


def test_a_payout_into_an_account_starts_from_zero(ctx, setup):
    car = loan_svc.add(ctx, mortgage(setup, name="Car", principal=0, start=D(2026, 9, 1), first_due=D(2026, 10, 1)))
    assert loan_svc.debt(ctx, car) == 0
    tx_svc.save(ctx, transfer(setup, car.account_id, setup["chf"].id, 20_000, D(2026, 9, 1)))
    assert loan_svc.debt(ctx, car) == 20_000
    assert wealth_svc.balance_entry(ctx, car.account_id, "2026-09").amount == -20_000


def test_the_account_balance_is_minus_the_debt(ctx, setup, loan):
    assert wealth_svc.balance_entry(ctx, loan.account_id, "2025-12") is None  # before the start
    entry = wealth_svc.balance_entry(ctx, loan.account_id, "2026-01")
    assert (entry.amount, entry.currency, entry.month, entry.id) == (
        -500_000,
        "CHF",
        "2026-01",
        f"loan:{loan.account_id}:2026-01",
    )
    tx_svc.save(ctx, transfer(setup, setup["chf"].id, loan.account_id, 2_500, D(2026, 3, 31)))
    assert wealth_svc.balance_entry(ctx, loan.account_id, "2026-02").amount == -500_000
    assert wealth_svc.balance_entry(ctx, loan.account_id, "2026-03").amount == -497_500
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-03", 10_000)
    assert wealth_svc.wealth(ctx, "2026-03", "CHF") == -487_500
    assert wealth_svc.by_class(ctx, "2026-03", "CHF") == {ASSET_CASH: 10_000, ASSET_LOAN: -497_500}
    assert wealth_svc.uses_classes(ctx)
    row = next(r for r in wealth_svc.account_rows(ctx, "2026-03", "CHF") if r.account.id == loan.account_id)
    assert (row.valued, row.needs_entry, row.has_entry) == (True, False, True)
    assert wealth_svc.first_recorded_month(ctx) == "2026-01"  # the loan's start, before the first recorded balance
    assert wealth_svc.last_recorded_month(ctx) == "2026-03"  # loans don't move the projection's start
    with pytest.raises(ValidationError, match="outstanding debt"):
        wealth_svc.upsert_entry(ctx, loan.account_id, "2026-03", -400_000)


def test_a_paid_off_loan_shows_zero(ctx, setup):
    car = loan_svc.add(ctx, mortgage(setup, name="Car", principal=1_000, start=D(2026, 9, 1), first_due=D(2026, 10, 1)))
    tx_svc.save(ctx, transfer(setup, setup["chf"].id, car.account_id, 1_000, D(2026, 9, 15)))
    st = loan_svc.status(ctx, car, TODAY)
    assert (st.debt, st.paid_off, st.upcoming, st.payoff, st.repaid, st.share) == (0, True, [], None, 1_000, 1.0)
    assert wealth_svc.balance_entry(ctx, car.account_id, "2026-09").amount == 0
    assert loan_svc.due(ctx, TODAY) == []


# ------------------------------------------------------------------ due payments, booking and skipping
def test_due_payments_with_the_interest_on_the_debt_before_each(ctx, setup, loan):
    due = loan_svc.due(ctx, TODAY)
    assert [(d.date, d.interest, d.amortization, d.first) for d in due] == [
        (D(2026, 3, 31), 1_875.0, 2_500, True),
        (D(2026, 6, 30), 1_865.63, 2_500, False),  # 497'500 × 1.5 % / 4 = 1'865.625, half up
        (D(2026, 9, 30), 1_856.25, 2_500, False),
    ]
    assert loan_svc.due_count(ctx, TODAY) == 3
    assert loan_svc.due(ctx, D(2026, 3, 30)) == []
    assert [d.date for d in loan_svc.due(ctx, TODAY, "other")] == []


def test_book_a_payment(ctx, setup, loan):
    booked = loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    assert [tx.type for tx in booked] == [TX_TRANSFER, TX_EXPENDITURE]
    repay, interest = booked
    assert (repay.amount, repay.from_account, repay.to_account, repay.loan_id) == (
        2_500,
        setup["chf"].id,
        loan.account_id,
        loan.id,
    )
    assert repay.comment == "Repayment Mortgage"
    assert (interest.amount, interest.category, interest.from_account, interest.payee, interest.loan_id) == (
        1_875.0,
        "12.2.1",
        setup["chf"].id,
        "Bank XY",
        loan.id,
    )
    assert interest.date == D(2026, 3, 31) and interest.comment == "Interest Mortgage"
    assert [p.name for p in ctx.store.payees.all()] == ["Bank XY"]
    fresh = ctx.store.loans.get(loan.id)
    assert fresh.next_due == D(2026, 6, 30)
    assert loan_svc.debt(ctx, fresh) == 497_500
    assert undo_svc.latest(ctx).label == "Book a loan payment"
    assert [d.date for d in loan_svc.due(ctx, TODAY)] == [D(2026, 6, 30), D(2026, 9, 30)]
    assert loan_svc.payments(ctx, loan.id) == [interest, repay] or loan_svc.payments(ctx, loan.id) == [repay, interest]
    assert loan_svc.interest_paid(ctx, loan.id, 2026) == 1_875.0 and loan_svc.interest_paid(ctx, loan.id, 2025) == 0
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.transactions.all() == [] and ctx.store.loans.get(loan.id).next_due == D(2026, 3, 31)


def test_book_with_other_amounts_and_date(ctx, setup, loan):
    booked = loan_svc.book(ctx, loan.id, D(2026, 3, 31), interest=1_880.4, amortization=0, booking_date=D(2026, 4, 2))
    assert [(tx.type, tx.amount, tx.date) for tx in booked] == [(TX_EXPENDITURE, 1_880.4, D(2026, 4, 2))]
    assert loan_svc.debt(ctx, loan) == 500_000
    nothing = loan_svc.book(ctx, loan.id, D(2026, 6, 30), interest=0, amortization=0)
    assert nothing == [] and ctx.store.loans.get(loan.id).next_due == D(2026, 9, 30)


def test_booking_is_checked(ctx, setup, loan):
    with pytest.raises(ValidationError, match="Book or skip the earlier payments"):
        loan_svc.book(ctx, loan.id, D(2026, 6, 30))
    with pytest.raises(ValidationError, match=r"exceeds the outstanding debt \(500,000.00 CHF\)"):
        loan_svc.book(ctx, loan.id, D(2026, 3, 31), amortization=500_001)
    with pytest.raises(ValidationError, match="can’t be negative"):
        loan_svc.book(ctx, loan.id, D(2026, 3, 31), interest=-1)
    loan_svc.skip(ctx, loan.id, D(2026, 3, 31))
    assert undo_svc.latest(ctx).label == "Skip a loan payment"
    assert ctx.store.loans.get(loan.id).next_due == D(2026, 6, 30)
    with pytest.raises(ValidationError, match="already been booked or skipped"):
        loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    with pytest.raises(ValidationError, match="This loan no longer exists"):
        loan_svc.book(ctx, "missing", D(2026, 6, 30))
    assert ctx.store.transactions.all() == []


def test_the_last_payment_takes_what_is_left(ctx, setup):
    car = loan_svc.add(
        ctx,
        mortgage(
            setup,
            name="Car",
            principal=1_000,
            rate=12,
            recur_months=1,
            start=D(2026, 9, 1),
            first_due=D(2026, 9, 30),
            amortization=600,
        ),
    )
    loan_svc.book(ctx, car.id, D(2026, 9, 30))
    [last] = loan_svc.due(ctx, D(2026, 10, 31))
    assert (last.date, last.interest, last.amortization) == (D(2026, 10, 30), 4.0, 400)  # the 30th, like the first
    loan_svc.book(ctx, car.id, D(2026, 10, 30))
    end = D(2026, 10, 31)
    assert loan_svc.debt(ctx, car, end) == 0 and loan_svc.status(ctx, ctx.store.loans.get(car.id), end).paid_off


def test_book_all_leaves_a_failing_loan_due(ctx, setup, loan):
    car = loan_svc.add(ctx, mortgage(setup, name="Car", principal=5_000, start=D(2026, 8, 1), first_due=D(2026, 9, 1)))
    broken = ctx.store.loans.get(car.id)
    broken.cash_account = "gone"
    ctx.store.loans.update(broken)
    result = loan_svc.book_all(ctx, TODAY, {(loan.id, D(2026, 6, 30)): (None, 3_000, D(2026, 7, 1))})
    assert [(tx.date, tx.amount) for tx in result.booked if tx.type == TX_TRANSFER] == [
        (D(2026, 3, 31), 2_500),
        (D(2026, 7, 1), 3_000),
        (D(2026, 9, 30), 2_500),
    ]
    assert [(d.loan.id, d.date, reason) for d, reason in result.failed] == [
        (car.id, D(2026, 9, 1), "Select the From account")
    ]
    assert [d.loan.id for d in loan_svc.due(ctx, TODAY)] == [car.id]  # September, still due
    assert undo_svc.latest(ctx).label == "Book loan payments"
    assert loan_svc.debt(ctx, loan) == 492_000


# ------------------------------------------------------------------ status
def test_status_and_renewals(ctx, setup, loan):
    loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    loan_svc.book(ctx, loan.id, D(2026, 6, 30), booking_date=D(2026, 7, 1))
    st = loan_svc.status(ctx, ctx.store.loans.get(loan.id), TODAY)
    assert (st.debt, st.repaid, st.share) == (495_000, 5_000, 0.01)
    assert st.interest_year == 1_875.0 + 1_865.63
    assert len(st.upcoming) == plan.PREVIEW and st.next.date == D(2026, 9, 30)
    assert st.next.interest == plan.interest(495_000, 1.5, 3) and st.next.debt_after == 492_500
    assert st.payoff == plan.occurrence(D(2026, 9, 30), 3, 197)  # 495'000 / 2'500 = 198 payments
    assert [s.loan.id for s in loan_svc.statuses(ctx, TODAY)] == [loan.id]
    assert loan_svc.renewals(ctx, TODAY) == []
    soon = loan_svc.edit(ctx, loan.id, mortgage(setup, fixed_until=D(2026, 12, 31)))
    later = loan_svc.add(ctx, mortgage(setup, name="Second", fixed_until=D(2028, 1, 1)))
    assert [ln.id for ln in loan_svc.renewals(ctx, TODAY)] == [soon.id]
    assert [ln.id for ln in loan_svc.renewals(ctx, TODAY, within=600)] == [soon.id, later.id]


# ------------------------------------------------------------------ changing and deleting
def test_edit_a_loan(ctx, setup, loan):
    edited = loan_svc.edit(
        ctx, loan.id, mortgage(setup, name="Home", rate=1.25, first_due=D(2026, 4, 30), amortization=3_000)
    )
    assert ctx.store.accounts.get(loan.account_id).name == "Home"
    assert (edited.rate, edited.amortization, edited.first_due, edited.next_due) == (
        1.25,
        3_000,
        D(2026, 4, 30),
        D(2026, 4, 30),
    )
    assert undo_svc.latest(ctx).label == "Change a loan"
    loan_svc.book(ctx, loan.id, D(2026, 4, 30))
    moved = loan_svc.edit(ctx, loan.id, mortgage(setup, name="Home", first_due=D(2026, 4, 15), recur_months=1))
    assert (moved.first_due, moved.next_due) == (D(2026, 4, 15), D(2026, 5, 15))  # after the payment handled in April
    with pytest.raises(ValidationError, match="currency cannot be changed"):
        loan_svc.edit(ctx, loan.id, mortgage(setup, name="Home", currency="EUR", cash_account=setup["eur"].id))
    with pytest.raises(ValidationError, match="This loan no longer exists"):
        loan_svc.edit(ctx, "missing", mortgage(setup))
    with pytest.raises(ValidationError, match="Select the start date"):
        loan_svc.planned_next_due(ctx, LoanInput(), "missing") and loan_svc.validate(
            ctx, LoanInput(name="x", currency="CHF", principal=1)
        )


def test_delete_a_loan(ctx, setup, loan):
    loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    with pytest.raises(ValidationError, match="This loan has transactions"):
        loan_svc.delete(ctx, loan.id)
    tx_svc.delete(ctx, [tx.id for tx in ctx.store.transactions.all()])
    loan_svc.delete(ctx, loan.id)
    assert ctx.store.loans.all() == [] and ctx.store.accounts.get(loan.account_id) is None
    assert undo_svc.latest(ctx).label == "Delete a loan"
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.loans.get(loan.id) is not None and ctx.store.accounts.get(loan.account_id) is not None
    with pytest.raises(ValidationError, match="This loan no longer exists"):
        loan_svc.delete(ctx, "missing")


def test_the_loan_account_is_the_loans_own(ctx, setup, loan):
    with pytest.raises(ValidationError, match="added with its loan on the Loans page"):
        acc_svc.add_account(ctx, "Debt", "CHF", asset_class=ASSET_LOAN)
    with pytest.raises(ValidationError, match="added with its loan on the Loans page"):
        acc_svc.edit_account(ctx, setup["chf"].id, "Bank", "CHF", ASSET_LOAN)
    with pytest.raises(ValidationError, match="belongs to a loan"):
        acc_svc.edit_account(ctx, loan.account_id, "Mortgage", "CHF", ASSET_CASH)
    with pytest.raises(ValidationError, match="belongs to a loan"):
        acc_svc.delete_account(ctx, loan.account_id)
    renamed = acc_svc.edit_account(ctx, loan.account_id, "Home loan", "CHF")  # the class stays
    assert (renamed.name, renamed.is_loan) == ("Home loan", True)


def test_booked_rows_keep_their_loan_through_edits(ctx, setup, loan):
    repay, interest = loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    inp = tx_svc.edit_input(ctx, interest)
    assert inp.loan_id == loan.id
    inp.loan_id, inp.amount = "", 1_900
    saved = tx_svc.save(ctx, inp, interest.id)
    assert (saved.loan_id, saved.amount) == (loan.id, 1_900)  # the form doesn't carry the loan: it is kept
    tx_svc.delete(ctx, [repay.id])  # an ordinary transaction
    assert loan_svc.debt(ctx, loan) == 500_000


def test_the_month_of_a_payment_reconciles(ctx, setup, loan):
    bank = setup["chf"].id
    wealth_svc.upsert_entry(ctx, bank, "2026-02", 20_000)
    loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    wealth_svc.upsert_entry(ctx, bank, "2026-03", 20_000 - 2_500 - 1_875)
    for view in ("CHF", "EUR"):
        rep = reports.month_report(ctx, "2026-03", view, today=TODAY)
        assert rep.unexplained == pytest.approx(0, abs=0.01), view
        row = next(r for r in rep.accounts if r.account.id == loan.account_id)
        assert (row.opening, row.amount, row.unexplained, row.valued) == (-500_000, -497_500, 0, True)
    first = reports.month_report(ctx, "2026-01", "CHF", today=TODAY)
    row = next(r for r in first.accounts if r.account.id == loan.account_id)
    assert (row.opening, row.unexplained) == (0, -500_000)  # the debt appears: its payout is not in the app


def test_loans_are_stored_and_exported(ctx, setup, loan):
    loan_svc.book(ctx, loan.id, D(2026, 3, 31))
    again = Store(ctx.store.data_dir)
    [stored] = again.loans.all()
    assert stored == ctx.store.loans.get(loan.id) and stored.next_due == D(2026, 6, 30)
    assert {tx.loan_id for tx in again.transactions.all()} == {loan.id}
    doc = json.loads(export_svc.everything_json(ctx))
    assert doc["loans"][0]["account_id"] == loan.account_id
    assert doc["transactions"][0]["loan_id"] == loan.id
    added = next(a for a in ctx.store.history.actions(limit=10) if a.label == "Add a loan")
    change = next(c for c in added.changes if c.table == "loans")
    assert undo_svc.describe_change(ctx, change).startswith("Loan added: Mortgage")
