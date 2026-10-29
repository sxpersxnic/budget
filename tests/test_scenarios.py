"""What-if scenarios (F15): scenarios and their changes with undo, the overlay over the plan's budgets, the projection
and the outcome against the plan, and adopting a scenario into the plan as one undo step."""

import json
from datetime import date

import pytest

from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services import export as export_svc
from budget_app.services import scenarios as scen_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.aggregates import projected_net
from budget_app.services.budget import BudgetInput
from budget_app.services.context import ValidationError
from budget_app.services.scenarios import ChangeInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store

TODAY = date(2026, 10, 18)


def budget(ctx, type_, category, amount, start="2026-11", end="", recur=1, currency="CHF"):
    return budget_svc.create(
        ctx,
        BudgetInput(
            start_month=start,
            end_month=end,
            forever=not end,
            recur_months=recur,
            type=type_,
            currency=currency,
            amount=amount,
            category=category,
        ),
    )


def salary_cut(setup, **kw) -> ChangeInput:
    base = dict(type=BUDGET_INCOME, category=setup["salary"].id, amount=4960, currency="CHF", start_month="2027-01")
    return ChangeInput(**(base | kw))


def car(**kw) -> ChangeInput:
    base = dict(category="07.1.1", amount=18500, currency="CHF", start_month="2027-02", end_month="2027-02")
    return ChangeInput(**(base | kw))


@pytest.fixture
def plan(ctx, setup):
    """A salary and two expenditure budgets from November on, and a balance recorded for September."""
    budget(ctx, BUDGET_INCOME, setup["salary"].id, 6200)
    budget(ctx, BUDGET_EXPENDITURE, "01.1", 700)
    budget(ctx, BUDGET_EXPENDITURE, "04.1", 1900)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 10_000)
    return setup


# ------------------------------------------------------------------ scenarios
def test_add_edit_show_and_delete_with_undo(ctx, tmp_path):
    s = scen_svc.add(ctx, "  80 %   work ", "four days", today=TODAY)
    assert (s.name, s.comment, s.shown, s.created) == ("80 % work", "four days", True, TODAY)
    assert undo_svc.latest(ctx).label == "Add a scenario"
    assert "scenarios" in ctx.store.tables() and "scenario_changes" in ctx.store.tables()
    with pytest.raises(ValidationError, match="Name is required"):
        scen_svc.add(ctx, "   ")
    with pytest.raises(ValidationError, match="A scenario called “80 % work” already exists"):
        scen_svc.add(ctx, "80 % WORK")
    other = scen_svc.add(ctx, "Car")
    with pytest.raises(ValidationError, match="already exists"):
        scen_svc.edit(ctx, other.id, "80 % work")
    assert scen_svc.edit(ctx, other.id, "A car", "used").comment == "used"
    assert undo_svc.latest(ctx).label == "Change a scenario"
    assert [x.name for x in scen_svc.scenarios(ctx)] == ["80 % work", "A car"]

    assert not scen_svc.set_shown(ctx, other.id, False).shown
    assert undo_svc.latest(ctx).label == "Show or hide a scenario on the Overview"
    assert [x.name for x in scen_svc.shown(ctx)] == ["80 % work"]

    change = scen_svc.add_change(ctx, s.id, car())
    scen_svc.delete(ctx, s.id)
    assert ctx.store.scenarios.get(s.id) is None and ctx.store.scenario_changes.get(change.id) is None
    act = undo_svc.latest(ctx)
    assert act.label == "Delete a scenario" and len(act.changes) == 2
    assert undo_svc.describe_change(ctx, act.changes[0]) == "Scenario change deleted: 2027-02 · 07.1.1 · 18500.0 CHF"
    assert undo_svc.describe_change(ctx, act.changes[1]) == "Scenario deleted: 80 % work"
    undo_svc.undo(ctx, act.id)
    assert ctx.store.scenarios.get(s.id).name == "80 % work" and ctx.store.scenario_changes.get(change.id)
    for missing in (scen_svc.get, scen_svc.delete):
        with pytest.raises(ValidationError, match="This scenario no longer exists"):
            missing(ctx, "nope")
    with pytest.raises(ValidationError, match="This change no longer exists"):
        scen_svc.delete_change(ctx, "nope")

    again = Store(tmp_path)
    assert again.scenarios.get(s.id).shown and again.scenario_changes.get(change.id).amount == 18500
    doc = json.loads(export_svc.everything_json(ctx))
    assert [x["name"] for x in doc["scenarios"]] == ["80 % work", "A car"]
    assert doc["scenario_changes"][0]["category"] == "07.1.1"


# ------------------------------------------------------------------ changes
@pytest.mark.parametrize(
    ("inp", "message"),
    [
        (car(amount=None), "Enter an amount; 0 takes the budget out of these months"),
        (car(amount=-1), "Enter an amount"),
        (car(currency=""), "Currency is required"),
        (car(category="nope"), "Select an expenditure category"),
        (car(type=BUDGET_INCOME, category="nope"), "Select an income category"),
        (car(type="other"), "Select income or expenditure"),
        (car(start_month=""), "Select the first month"),
        (car(end_month="2027-01"), "The final month must not be before the first month"),
        (car(end_month="2027-04"), "The final month cannot be after the budget horizon"),
        (car(start_month="2027-04", end_month=""), "The plan ends in Mar 2027, before the first month"),
    ],
)
def test_a_change_is_checked(ctx, setup, inp, message):
    s = scen_svc.add(ctx, "S")
    with pytest.raises(ValidationError, match=message):
        scen_svc.add_change(ctx, s.id, inp)
    assert ctx.store.scenario_changes.all() == []


def test_changes_their_months_and_labels(ctx, setup):
    s = scen_svc.add(ctx, "S")
    cut = scen_svc.add_change(ctx, s.id, salary_cut(setup, comment=" less "))
    assert (cut.type, cut.amount, cut.currency, cut.start_month, cut.end_month, cut.seq, cut.comment) == (
        BUDGET_INCOME,
        4960,
        "CHF",
        "2027-01",
        "",
        1,
        "less",
    )
    assert (cut.amount_chf, cut.fx_date) == (4960, date(2026, 10, 16))
    assert undo_svc.latest(ctx).label == "Add a scenario change"
    assert scen_svc.change_months(ctx, cut) == ["2027-01", "2027-02", "2027-03"]  # to the end of the plan
    quarterly = scen_svc.add_change(ctx, s.id, car(start_month="2026-10", end_month="2027-03", recur_months=3))
    assert quarterly.seq == 2 and scen_svc.change_months(ctx, quarterly) == ["2026-10", "2027-01"]
    euro = scen_svc.add_change(ctx, s.id, car(currency="EUR", amount=900))
    assert euro.amount_chf == pytest.approx(810)  # at the latest rate, like a budget entry
    assert [c.seq for c in scen_svc.changes(ctx, s.id)] == [1, 2, 3]
    assert scen_svc.category_label(ctx, cut) == "Salary"
    assert scen_svc.category_label(ctx, quarterly).startswith("07.1.1")
    gone = ctx.store.scenario_changes.get(cut.id)
    gone.category = "gone"
    assert scen_svc.category_label(ctx, gone) == "gone"

    edited = scen_svc.edit_change(ctx, cut.id, salary_cut(setup, amount=0, end_month="2027-01"))
    assert (edited.amount, edited.end_month, edited.seq) == (0, "2027-01", 1)
    assert undo_svc.latest(ctx).label == "Change a scenario change"
    scen_svc.delete_change(ctx, quarterly.id)
    assert undo_svc.latest(ctx).label == "Delete a scenario change"
    assert [c.id for c in scen_svc.changes(ctx, s.id)] == [cut.id, euro.id]
    ctx.store.settings.budget_horizon = "2026-12"  # the plan shrank: a change may lie beyond it
    assert scen_svc.change_months(ctx, euro) == ["2027-02"]
    assert scen_svc.change_months(ctx, ctx.store.scenario_changes.get(cut.id)) == ["2027-01"]
    late = scen_svc.add_change(ctx, s.id, salary_cut(setup, start_month="2026-11", end_month="2026-12"))
    ctx.store.settings.budget_horizon = "2026-10"
    assert scen_svc.change_months(ctx, late) == ["2026-11", "2026-12"]  # an end month stays; only "forever" shrinks
    with pytest.raises(ValidationError, match="before the first month"):
        scen_svc.add_change(ctx, s.id, salary_cut(setup, start_month="2026-11"))


# ------------------------------------------------------------------ the overlay and the projection
def test_the_overlay_replaces_the_plan_only_where_the_scenario_says(ctx, plan):
    salary = plan["salary"].id
    s = scen_svc.add(ctx, "S")
    scen_svc.add_change(ctx, s.id, salary_cut(plan))
    scen_svc.add_change(ctx, s.id, car())
    scen_svc.add_change(ctx, s.id, car(category="01.1", amount=0, start_month="2027-03", end_month="2027-03"))
    assert scen_svc.overlay(ctx, s.id, "2026-12", BUDGET_INCOME, "CHF") == {salary: 6200}  # before the change
    assert scen_svc.overlay(ctx, s.id, "2027-01", BUDGET_INCOME, "CHF") == {salary: 4960}
    assert scen_svc.overlay(ctx, s.id, "2027-01", BUDGET_INCOME, "EUR") == {salary: pytest.approx(4960 / 0.90)}
    assert scen_svc.overlay(ctx, s.id, "2027-01", BUDGET_EXPENDITURE, "CHF") == {"01.1": 700, "04.1": 1900}
    assert scen_svc.overlay(ctx, s.id, "2027-02", BUDGET_EXPENDITURE, "CHF") == {
        "01.1": 700,
        "04.1": 1900,
        "07.1.1": 18500,
    }
    assert scen_svc.overlay(ctx, s.id, "2027-03", BUDGET_EXPENDITURE, "CHF") == {"04.1": 1900}  # 0 takes it out
    assert scen_svc.plan_budget(ctx, BUDGET_EXPENDITURE, "01.1", "2027-03", "CHF") == 700  # the plan is untouched
    assert scen_svc.plan_budget(ctx, BUDGET_EXPENDITURE, "07.1.1", "2027-02", "CHF") == 0

    # the later change wins where two meet
    scen_svc.add_change(ctx, s.id, salary_cut(plan, amount=3100, start_month="2027-03", end_month="2027-03"))
    assert scen_svc.overlay(ctx, s.id, "2027-02", BUDGET_INCOME, "CHF") == {salary: 4960}
    assert scen_svc.overlay(ctx, s.id, "2027-03", BUDGET_INCOME, "CHF") == {salary: 3100}

    assert scen_svc.projected_net(ctx, s.id, "2026-12", "CHF") == projected_net(ctx, "2026-12", "CHF") == 3600
    assert scen_svc.projected_net(ctx, s.id, "2027-01", "CHF") == 4960 - 2600
    assert scen_svc.projected_net(ctx, s.id, "2027-02", "CHF") == pytest.approx(4960 - 2600 - 18500)
    assert scen_svc.projected_net(ctx, s.id, "2027-03", "CHF") == 3100 - 1900


def test_recorded_amounts_still_count_in_a_scenario(ctx, plan):
    """What has happened is higher than the scenario's budget: it counts, as in the plan (F47)."""
    s = scen_svc.add(ctx, "S")
    scen_svc.add_change(ctx, s.id, car(category="01.1", amount=100, start_month="2026-11", end_month="2026-11"))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 11, 5),
            type="expenditure",
            amount=250,
            currency="CHF",
            category="01.1.1",
            from_account=plan["chf"].id,
        ),
    )
    assert scen_svc.projected_net(ctx, s.id, "2026-11", "CHF") == 6200 - 250 - 1900
    assert projected_net(ctx, "2026-11", "CHF") == 6200 - 700 - 1900


def test_projected_wealth_series_and_outcome(ctx, plan):
    s = scen_svc.add(ctx, "S")
    scen_svc.add_change(ctx, s.id, salary_cut(plan))
    scen_svc.add_change(ctx, s.id, car())
    # the plan: 10'000 + 0 (Oct, nothing budgeted) + 3'600 × 5 months
    assert wealth_svc.projected_wealth(ctx, "2027-03", "CHF") == 10_000 + 3600 * 5
    assert scen_svc.projected_wealth(ctx, s.id, "2026-12", "CHF") == wealth_svc.projected_wealth(ctx, "2026-12", "CHF")
    assert scen_svc.projected_wealth(ctx, s.id, "2027-03", "CHF") == pytest.approx(10_000 + 3600 * 2 + 2360 * 3 - 18500)
    assert scen_svc.projected_wealth(ctx, s.id, "2026-09", "CHF") == 10_000  # recorded months are what they are
    out = scen_svc.outcome(ctx, s.id, "CHF")
    assert (out.month, out.plan, out.scenario) == ("2027-03", 28_000, pytest.approx(5780))
    assert out.delta == pytest.approx(-22_220)
    assert scen_svc.outcome(ctx, s.id, "CHF", "2027-01").delta == pytest.approx(-1240)
    series = scen_svc.series(ctx, s.id, "CHF")
    assert [m for m, _ in series] == ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    assert series[-1][1] == pytest.approx(out.scenario)
    plan_series = wealth_svc.wealth_series(ctx, "CHF")
    assert [(m, v) for m, v, p in plan_series if p][:3] == series[:3]  # the same until the first change
    assert len([m for m, _, p in plan_series if not p]) == 1  # the recorded month is not part of a scenario's series


def test_a_scenario_without_changes_is_the_plan(ctx, plan):
    s = scen_svc.add(ctx, "S")
    out = scen_svc.outcome(ctx, s.id, "EUR")
    assert out.delta == 0 and out.plan == pytest.approx(wealth_svc.projected_wealth(ctx, "2027-03", "EUR"))


# ------------------------------------------------------------------ adopt into the plan
def test_adopt_writes_the_changes_and_removes_the_scenario_in_one_step(ctx, plan):
    s = scen_svc.add(ctx, "S")
    scen_svc.add_change(ctx, s.id, salary_cut(plan, comment="80 %"))
    scen_svc.add_change(ctx, s.id, car())
    scen_svc.add_change(ctx, s.id, car(category="01.1", amount=0, start_month="2027-03", end_month="2027-03"))
    scen_svc.add_change(ctx, s.id, salary_cut(plan, amount=3100, start_month="2027-03", end_month="2027-03"))
    before = {b.id for b in ctx.store.budget.all()}

    added = scen_svc.adopt(ctx, s.id)
    assert added == 3 + 1 + 1  # the salary series (3 months), the car, March's salary
    income = sorted(
        (b.month, b.amount, b.comment, b.forever) for b in ctx.store.budget.all() if b.type == BUDGET_INCOME
    )
    assert income == [
        ("2026-11", 6200, "", False),  # the old salary ends where the cut begins: only the cut continues
        ("2026-12", 6200, "", False),
        ("2027-01", 4960, "80 %", True),
        ("2027-02", 4960, "80 %", True),
        ("2027-03", 3100, "", False),  # the later change won, as in the overlay
    ]
    groceries = sorted(b.month for b in ctx.store.budget.all() if b.category == "01.1")
    assert groceries == ["2026-11", "2026-12", "2027-01", "2027-02"]  # March taken out by the 0 change
    cars = [(b.month, b.amount) for b in ctx.store.budget.all() if b.category == "07.1.1"]
    assert cars == [("2027-02", 18500)]
    assert [b.amount for b in ctx.store.budget.all() if b.category == "04.1"] == [1900] * 5  # untouched
    # the Budget Plan's series view: the old salary ends, the cut continues, March stands alone
    income_series = [s for s in budget_svc.series(ctx, "2026-11") if s.type == BUDGET_INCOME]
    assert [(s.first, s.last, s.forever, s.current.amount) for s in income_series] == [
        ("2026-11", "2026-12", False, 6200),
        ("2027-01", "2027-02", True, 4960),
        ("2027-03", "2027-03", False, 3100),
    ]
    assert ctx.store.scenarios.get(s.id) is None and scen_svc.changes(ctx, s.id) == []
    # the adopted plan projects what the scenario did
    assert wealth_svc.projected_wealth(ctx, "2027-03", "CHF") == pytest.approx(
        10_000 + 3600 * 2 + 2360 * 2 - 18500 + 1200
    )

    act = undo_svc.latest(ctx)
    assert act.label == "Adopt a scenario into the plan"
    undo_svc.undo(ctx, act.id)
    assert {b.id for b in ctx.store.budget.all()} == before
    assert ctx.store.scenarios.get(s.id).name == "S" and len(scen_svc.changes(ctx, s.id)) == 4


def test_adopt_checks_every_change_before_writing(ctx, plan):
    s = scen_svc.add(ctx, "S")
    scen_svc.add_change(ctx, s.id, car())
    scen_svc.add_change(ctx, s.id, salary_cut(plan, start_month="2027-02", end_month="2027-03"))
    ctx.store.settings.budget_horizon = "2027-02"  # the plan shrank: the salary change now ends beyond it
    before = [(b.month, b.amount) for b in ctx.store.budget.all()]
    with pytest.raises(ValidationError, match="Salary: The final month cannot be after the budget horizon"):
        scen_svc.adopt(ctx, s.id)
    assert [(b.month, b.amount) for b in ctx.store.budget.all()] == before  # the car was not written either
    assert ctx.store.scenarios.get(s.id) is not None
    assert undo_svc.latest(ctx).label == "Add a scenario change"

    with pytest.raises(ValidationError, match="This scenario no longer exists"):
        scen_svc.adopt(ctx, "nope")
    empty = scen_svc.add(ctx, "Empty")
    assert scen_svc.adopt(ctx, empty.id) == 0 and ctx.store.scenarios.get(empty.id) is None
