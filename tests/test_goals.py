"""Savings goals (F14): whole months, the monthly amount needed, the on-track line, the reserve, the target of an
emergency fund, an account's balance today, conversion at the latest rate, and adding, changing, archiving and
deleting goals with undo."""

import json
from datetime import date

import pytest

from budget_app.domain import goals as plan
from budget_app.domain import months
from budget_app.domain.models import ASSET_SECURITIES, GOAL_MONTHS
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import export as export_svc
from budget_app.services import goals as goal_svc
from budget_app.services import investments as inv_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import ValidationError
from budget_app.services.goals import GoalInput
from budget_app.services.investments import TradeInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store

TODAY = date(2026, 10, 18)
D = date


def spend(setup, day: date, amount: float, **kw) -> TransactionInput:
    base = dict(
        date=day, type="expenditure", amount=amount, currency="CHF", category="01.1", from_account=setup["chf"].id
    )
    return TransactionInput(**(base | kw))


def holiday(**kw) -> GoalInput:
    return GoalInput(**(dict(name="Holiday", currency="CHF", target=4000, target_date=D(2027, 6, 30)) | kw))


# ------------------------------------------------------------------ domain: months and the amount needed
@pytest.mark.parametrize(
    ("due", "n"),
    [
        (D(2027, 6, 30), 8),  # the 30th comes after the 18th: June counts
        (D(2027, 6, 18), 8),
        (D(2027, 6, 17), 7),  # the last month is not complete
        (D(2026, 11, 1), 1),  # less than a month: still one
        (D(2026, 10, 18), 1),  # today
        (D(2026, 10, 17), 0),  # passed
        (D(2027, 10, 18), 12),
    ],
)
def test_whole_months(due, n):
    assert plan.whole_months(TODAY, due) == n


def test_the_amount_needed_is_rounded_up_to_the_cent():
    assert plan.monthly_needed(1000, 3) == 333.34  # three times 333.33 would fall short
    assert plan.monthly_needed(900, 3) == 300.0  # no stray cent from floating point
    assert plan.monthly_needed(0.1 + 0.2, 1) == 0.3
    assert plan.monthly_needed(0.004, 2) == 0.0  # nothing left
    assert plan.monthly_needed(-5, 2) == 0.0
    assert plan.monthly_needed(10, 0) == 10.0


def test_progress_on_track_and_behind():
    start = D(2026, 1, 18)
    due = D(2027, 1, 18)  # a year from 0 to 1'200: 100 a month, 900 by mid-October
    on = plan.progress(950, 1200, TODAY, due, start, 0)
    assert (on.status, on.months_left, on.needed, on.expected) == (plan.ON_TRACK, 3, 83.34, pytest.approx(897.53))
    assert (on.remaining, on.ratio, on.percent) == (250, pytest.approx(950 / 1200), 79)
    behind = plan.progress(600, 1200, TODAY, due, start, 0)
    assert (behind.status, behind.needed) == (plan.BEHIND, 200.0)
    exact = plan.progress(behind.expected, 1200, TODAY, due, start, 0)
    assert exact.status == plan.ON_TRACK  # on the line counts as on track
    late_start = plan.progress(300, 1200, TODAY, due, D(2026, 11, 1), 300)
    assert late_start.expected == 300  # the line starts where the goal started


def test_progress_reached_overdue_and_without_a_date():
    reached = plan.progress(4000.004, 4000, TODAY, D(2027, 6, 30))
    assert (reached.status, reached.needed, reached.remaining, reached.ratio, reached.percent) == (
        plan.REACHED,
        0.0,
        0,
        1.0,
        100,
    )
    more = plan.progress(5000, 4000, TODAY)
    assert (more.status, more.needed, more.ratio, more.percent) == (plan.REACHED, None, 1.0, 125)
    almost = plan.progress(3999.9, 4000, TODAY, D(2027, 6, 30))
    assert almost.percent == 99  # never 100 % before it is reached
    overdue = plan.progress(1000, 4000, TODAY, D(2026, 9, 30))
    assert (overdue.status, overdue.months_left, overdue.needed, overdue.remaining) == (plan.OVERDUE, 0, None, 3000)
    open_ended = plan.progress(1000, 4000, TODAY)
    assert (open_ended.status, open_ended.needed, open_ended.expected) == (plan.NO_DATE, None, None)
    nothing = plan.progress(500, 0, TODAY, D(2027, 1, 1))
    assert (nothing.status, nothing.ratio, nothing.percent, nothing.remaining) == (plan.NO_TARGET, 0.0, 0, 0)
    negative = plan.progress(-50, 100, TODAY)
    assert (negative.ratio, negative.percent) == (0.0, 0)


def test_the_line_of_a_goal_that_started_on_its_date():
    assert plan.expected(D(2026, 5, 1), 10, D(2026, 5, 1), 100, TODAY) == 100
    assert plan.expected(D(2026, 11, 1), 10, D(2027, 5, 1), 100, TODAY) == 10  # not started yet


def test_planned_reserve():
    p = plan.progress(1000, 4000, TODAY, D(2027, 6, 30))  # 8 months: 375 a month
    assert [plan.planned(p, n) for n in (-2, 0, 1, 7, 8, 20)] == [1000, 1000, 1375, 3625, 4000, 4000]
    early = plan.progress(1000, 4000, TODAY, D(2027, 6, 10))  # 7 whole months: full by May
    assert early.needed == 428.58 and plan.planned(early, 7) == 4000 and plan.planned(early, 6) < 4000
    soon = plan.progress(100, 400, TODAY, D(2026, 10, 30))  # due this month: reserved in full at its end
    assert plan.planned(soon, 0) == 400
    assert plan.planned(plan.progress(1000, 4000, TODAY), 5) == 1000  # no date: what it holds
    assert plan.planned(plan.progress(5000, 4000, TODAY), 0) == 4000  # never more than the target
    assert plan.planned(plan.progress(1000, 4000, TODAY, D(2026, 1, 1)), 0) == 4000  # overdue: in full
    assert plan.planned(plan.progress(-20, 4000, TODAY), 3) == 0
    assert plan.planned(plan.progress(100, 0, TODAY), 3) == 0


# ------------------------------------------------------------------ adding and changing
def test_add_a_goal_by_hand(ctx):
    goal = goal_svc.add(ctx, holiday(saved=1000), today=TODAY)
    assert (goal.name, goal.target, goal.saved, goal.target_date, goal.start, goal.start_amount) == (
        "Holiday",
        4000,
        1000,
        "2027-06-30",
        TODAY,
        1000,
    )
    st = goal_svc.status(ctx, goal, TODAY)
    assert (st.progress.status, st.progress.needed, st.open) == (plan.ON_TRACK, 375.0, True)
    assert undo_svc.latest(ctx).label == "Add a savings goal"
    assert "goals" in ctx.store.tables()


@pytest.mark.parametrize(
    ("inp", "message"),
    [
        (holiday(name="  "), "Name is required"),
        (holiday(currency=""), "Currency is required"),
        (holiday(kind="weekly"), "Choose the kind of goal"),
        (holiday(target=0), "Enter a target amount above 0"),
        (holiday(target=None), "Enter a target amount above 0"),
        (holiday(kind=GOAL_MONTHS, months=0), "Enter a whole number of months from 1 to 36"),
        (holiday(kind=GOAL_MONTHS, months=37), "Enter a whole number of months from 1 to 36"),
        (holiday(kind=GOAL_MONTHS, months=2.5), "Enter a whole number of months from 1 to 36"),
        (holiday(kind=GOAL_MONTHS), "Enter a whole number of months from 1 to 36"),
        (holiday(target_date=D(2026, 10, 17)), "The target date must not be in the past"),
        (holiday(account_id="nope"), "Select an account"),
        (holiday(saved=-1), "The amount saved must not be negative"),
    ],
)
def test_add_refuses(ctx, inp, message):
    with pytest.raises(ValidationError, match=message):
        goal_svc.add(ctx, inp, today=TODAY)


def test_names_are_unique_and_tidied(ctx):
    goal = goal_svc.add(ctx, holiday(name="  Holiday   Italy "), today=TODAY)
    assert goal.name == "Holiday Italy"
    with pytest.raises(ValidationError, match="A goal called “Holiday Italy” already exists"):
        goal_svc.add(ctx, holiday(name="holiday italy"), today=TODAY)
    assert goal_svc.edit(ctx, goal.id, holiday(name="Holiday Italy", target=4500), today=TODAY).target == 4500


def test_an_advance_account_cannot_be_linked(ctx, setup):
    adv = acc_svc.default_advance_account(ctx, "CHF")
    with pytest.raises(ValidationError, match="Select an account"):
        goal_svc.add(ctx, holiday(account_id=adv.id), today=TODAY)


def test_edit_keeps_or_restarts_the_line(ctx, setup):
    goal = goal_svc.add(ctx, holiday(saved=1000), today=D(2026, 9, 1))
    later = D(2026, 10, 1)
    edited = goal_svc.edit(ctx, goal.id, holiday(saved=1500, name="Summer"), today=later)
    assert (edited.name, edited.saved, edited.start, edited.start_amount) == ("Summer", 1500, D(2026, 9, 1), 1000)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 2000)
    linked = goal_svc.edit(ctx, goal.id, holiday(name="Summer", account_id=setup["chf"].id, saved=99), today=later)
    assert (linked.saved, linked.start, linked.start_amount) == (0, later, 2000)  # a new start, from the account
    euro = goal_svc.edit(ctx, goal.id, holiday(name="Summer", currency="EUR", account_id=setup["chf"].id), today=TODAY)
    assert (euro.start, euro.start_amount) == (TODAY, pytest.approx(2000 / 0.90, abs=0.01))
    assert undo_svc.latest(ctx).label == "Change a savings goal"
    with pytest.raises(ValidationError, match="This goal no longer exists"):
        goal_svc.edit(ctx, "missing", holiday(), today=TODAY)


def test_an_overdue_goal_can_be_edited_without_a_new_date(ctx):
    goal = goal_svc.add(ctx, holiday(target_date=D(2026, 11, 30)), today=TODAY)
    after = D(2026, 12, 15)
    assert goal_svc.edit(ctx, goal.id, holiday(target_date=D(2026, 11, 30), target=3000), today=after).target == 3000
    with pytest.raises(ValidationError, match="The target date must not be in the past"):
        goal_svc.edit(ctx, goal.id, holiday(target_date=D(2026, 12, 1)), today=after)
    assert goal_svc.status(ctx, ctx.store.goals.get(goal.id), after).progress.status == plan.OVERDUE


def test_archive_restore_and_delete_with_undo(ctx):
    a = goal_svc.add(ctx, holiday(), today=TODAY)
    b = goal_svc.add(ctx, holiday(name="Car", target_date=None), today=TODAY)
    c = goal_svc.add(ctx, holiday(name="Bike", target_date=D(2027, 1, 1)), today=TODAY)
    assert [g.name for g in goal_svc.goals(ctx)] == ["Bike", "Holiday", "Car"]  # by date, without one last
    goal_svc.archive(ctx, a.id)
    assert [g.name for g in goal_svc.goals(ctx)] == ["Bike", "Car"]
    assert [g.name for g in goal_svc.goals(ctx, archived=True)] == ["Holiday"]
    assert undo_svc.latest(ctx).label == "Archive a savings goal"
    goal_svc.restore(ctx, a.id)
    assert undo_svc.latest(ctx).label == "Restore a savings goal"
    assert not ctx.store.goals.get(a.id).archived
    goal_svc.delete(ctx, b.id)
    assert ctx.store.goals.get(b.id) is None
    act = undo_svc.latest(ctx)
    assert act.label == "Delete a savings goal"
    assert undo_svc.describe_change(ctx, act.changes[0]) == "Savings goal deleted: Car"
    undo_svc.undo(ctx, act.id)
    assert ctx.store.goals.get(b.id).name == "Car"
    with pytest.raises(ValidationError, match="This goal no longer exists"):
        goal_svc.archive(ctx, "missing")
    assert c.id in {g.id for g in goal_svc.goals(ctx)}


def test_goals_are_saved_and_exported(ctx, tmp_path):
    goal = goal_svc.add(ctx, holiday(kind=GOAL_MONTHS, months=3, reserve=True), today=TODAY)
    again = Store(tmp_path).goals.get(goal.id)
    assert (again.kind, again.months, again.target, again.reserve, again.start) == (GOAL_MONTHS, 3, 0, True, TODAY)
    doc = json.loads(export_svc.everything_json(ctx))
    assert doc["goals"][0]["name"] == "Holiday" and doc["goals"][0]["start"] == "2026-10-18"


# ------------------------------------------------------------------ amounts: account balance, conversion, months
def test_a_linked_account_counts_its_balance_today(ctx, setup):
    chf = setup["chf"].id
    assert goal_svc.account_balance(ctx, "missing", TODAY) is None
    tx_svc.save(ctx, spend(setup, D(2026, 8, 5), 50))  # before any balance: from 0
    assert goal_svc.account_balance(ctx, chf, TODAY) == -50
    wealth_svc.upsert_entry(ctx, chf, "2026-08", 1000)
    tx_svc.save(ctx, spend(setup, D(2026, 8, 20), 70))  # inside the recorded month: already in the balance
    tx_svc.save(ctx, spend(setup, D(2026, 9, 3), 100))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=D(2026, 10, 2),
            type="exchange",
            amount=200,
            currency="EUR",
            from_account=setup["eur"].id,
            to_account=chf,
            to_amount=180,
        ),
    )
    tx_svc.save(ctx, spend(setup, D(2026, 10, 20), 40))  # after today: not yet
    assert goal_svc.account_balance(ctx, chf, TODAY) == 1000 - 100 + 180
    assert goal_svc.account_balance(ctx, setup["eur"].id, TODAY) == -200
    wealth_svc.upsert_entry(ctx, chf, "2026-10", 1234)  # a balance for this month already: it is the balance
    assert goal_svc.account_balance(ctx, chf, TODAY) == 1234


def test_conversion_at_the_latest_rate(ctx, setup):
    wealth_svc.upsert_entry(ctx, setup["eur"].id, "2026-09", 1000)
    goal = goal_svc.add(ctx, holiday(account_id=setup["eur"].id), today=TODAY)
    assert goal_svc.current_of(ctx, goal, TODAY) == 900  # 1'000 EUR at 0.90, not September's 0.94
    usd = goal_svc.add(ctx, holiday(name="Trip", currency="USD", account_id=setup["eur"].id), today=TODAY)
    assert goal_svc.current_of(ctx, usd, TODAY) == 1250
    ctx.store.accounts.delete([setup["eur"].id])  # a linked account that is gone: the amount typed in
    assert goal_svc.current_of(ctx, ctx.store.goals.get(goal.id), TODAY) == 0


def test_a_securities_account_counts_its_holdings(ctx, setup):
    depot = acc_svc.add_account(ctx, "Depot", "CHF", asset_class=ASSET_SECURITIES)
    etf = inv_svc.add_holding(ctx, depot.id, "World ETF", "VWRL")
    inv_svc.save_trade(
        ctx,
        TradeInput(
            kind="buy", holding_id=etf.id, date=D(2026, 9, 10), quantity=10, price=100, cash_account=setup["chf"].id
        ),
    )
    assert goal_svc.account_balance(ctx, depot.id, TODAY) == 1000
    empty = acc_svc.add_account(ctx, "Crypto", "CHF", asset_class=ASSET_SECURITIES)
    assert goal_svc.account_balance(ctx, empty.id, TODAY) == 0


def test_months_of_spending(ctx, setup):
    assert goal_svc.spending_months(TODAY) == months.month_range("2025-10", "2026-09")
    assert goal_svc.monthly_spending(ctx, "CHF", TODAY) == 0
    tx_svc.save(ctx, spend(setup, D(2026, 10, 2), 999))  # this month is not complete yet
    assert goal_svc.monthly_spending(ctx, "CHF", TODAY) == 0
    for month in ("2026-08", "2026-09"):
        tx_svc.save(ctx, spend(setup, months.first_day(month), 1000))
    tx_svc.save(  # half paid for someone else: only the own share counts
        ctx, spend(setup, D(2026, 9, 12), 400, is_advance=True, advance_mode="percent", advance_value=50, debtor="Anna")
    )
    tx_svc.save(ctx, spend(setup, D(2026, 9, 14), 100, category="TAX"))  # taxes count
    assert goal_svc.monthly_spending(ctx, "CHF", TODAY) == 1150  # (1'000 + 1'000 + 200 + 100) / 2
    tx_svc.save(ctx, spend(setup, D(2025, 3, 1), 1))  # data from before the window: all 12 months count
    assert goal_svc.monthly_spending(ctx, "CHF", TODAY) == round(2300 / 12, 2)  # March 2025 is outside
    goal = goal_svc.add(ctx, holiday(kind=GOAL_MONTHS, months=3, target_date=None, saved=200), today=TODAY)
    assert goal_svc.target_of(ctx, goal, TODAY) == round(3 * goal_svc.monthly_spending(ctx, "CHF", TODAY), 2)
    chf, eur = goal_svc.monthly_spending(ctx, "CHF", TODAY), goal_svc.monthly_spending(ctx, "EUR", TODAY)
    assert chf / 0.95 - 0.02 < eur < chf / 0.90  # each transaction at the rate of its date


def test_an_emergency_fund_without_spending_has_no_target(ctx):
    goal = goal_svc.add(ctx, holiday(kind=GOAL_MONTHS, months=3, saved=500), today=TODAY)
    assert goal_svc.status(ctx, goal, TODAY).progress.status == plan.NO_TARGET


def test_the_overview_shows_open_goals_first(ctx):
    done = goal_svc.add(ctx, holiday(name="Done", saved=5000, target_date=D(2026, 11, 1)), today=TODAY)
    goal_svc.add(ctx, holiday(name="Later", target_date=None), today=TODAY)
    goal_svc.add(ctx, holiday(name="Soon", target_date=D(2026, 12, 1)), today=TODAY)
    goal_svc.add(ctx, holiday(name="Next", target_date=D(2027, 1, 1)), today=TODAY)
    goal_svc.archive(ctx, goal_svc.add(ctx, holiday(name="Old"), today=TODAY).id)
    assert [s.goal.name for s in goal_svc.top_open(ctx, today=TODAY)] == ["Soon", "Next", "Later"]
    assert [s.goal.name for s in goal_svc.top_open(ctx, n=5, today=TODAY)] == ["Soon", "Next", "Later", "Done"]
    assert not goal_svc.status(ctx, done, TODAY).open


# ------------------------------------------------------------------ the reserve and free wealth
def test_the_reserve_in_the_projection(ctx, setup):
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 10_000)
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-10", forever=True, type="income", currency="CHF", amount=1000, category=setup["salary"].id
        ),
    )
    goal_svc.add(ctx, holiday(saved=1000, target=4000, target_date=D(2027, 2, 28), reserve=True), today=TODAY)
    fund_goal = holiday(name="Fund", currency="EUR", target=1000, target_date=None, saved=500, reserve=True)
    goal_svc.add(ctx, fund_goal, today=TODAY)
    goal_svc.add(ctx, holiday(name="Not reserved", saved=3000), today=TODAY)
    goal_svc.archive(ctx, goal_svc.add(ctx, holiday(name="Archived", saved=3000, reserve=True), today=TODAY).id)
    span = months.month_range("2026-10", "2027-03")
    reserve = goal_svc.reserve_series(ctx, span, "CHF", TODAY)
    fund = 500 * 0.90  # the EUR goal without a date: what it holds, at the latest rate
    assert reserve == {
        "2026-10": round(1000 + fund, 2),
        "2026-11": round(1750 + fund, 2),  # 4 months: 750 a month
        "2026-12": round(2500 + fund, 2),
        "2027-01": round(3250 + fund, 2),
        "2027-02": round(4000 + fund, 2),
        "2027-03": round(4000 + fund, 2),
    }
    assert goal_svc.reserve_at(ctx, "2026-12", "EUR", TODAY) == round(2500 / 0.90 + 500, 2)
    projected = wealth_svc.projected_wealth(ctx, "2027-03", "CHF")
    assert projected == 16_000  # the projection itself is untouched (F47)
    assert goal_svc.free_wealth(ctx, "2027-03", "CHF", TODAY) == pytest.approx(projected - 4000 - fund)
    assert [g.name for g in goal_svc.reserving(ctx)] == ["Holiday", "Fund"]
