from dataclasses import replace
from datetime import date

import pytest

from budget_app.domain.models import PROJECTION_BUDGET, PROJECTION_HIGHER, TX_EXPENDITURE, TX_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput

TODAY = date(2026, 10, 18)  # the day the fixed exchange rates in conftest.py lead up to


def test_series_expansion_and_forever_extension(ctx, setup):
    rows = budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-11",
            forever=True,
            recur_months=2,
            type="expenditure",
            currency="CHF",
            amount=100,
            category="04",
        ),
    )
    assert [r.month for r in rows] == ["2026-11", "2027-01", "2027-03"]
    added = budget_svc.set_horizon(ctx, "2027-08")
    assert added == 2
    assert sorted(b.month for b in ctx.store.budget.all()) == ["2026-11", "2027-01", "2027-03", "2027-05", "2027-07"]
    # shrinking keeps all rows; extending again does not duplicate them
    assert budget_svc.set_horizon(ctx, "2027-02") == 0
    assert budget_svc.set_horizon(ctx, "2027-08") == 0
    assert len(ctx.store.budget.all()) == 5


def test_reschedule_replans_from_the_edited_month(ctx, setup):
    rows = budget_svc.create(
        ctx,
        BudgetInput(start_month="2026-10", forever=True, type="expenditure", currency="CHF", amount=100, category="04"),
    )
    by_month = {r.month: r for r in rows}
    assert budget_svc.schedule_of(ctx, by_month["2026-12"]) == budget_svc.Schedule("", True, 1)

    # from December: every quarter until March (the horizon), 150 CHF, with a note
    new = budget_svc.reschedule(
        ctx,
        by_month["2026-12"].id,
        BudgetInput(end_month="2027-03", recur_months=3, currency="CHF", amount=150, comment="new rent"),
    )
    assert [r.month for r in new] == ["2026-12", "2027-03"]
    assert all(r.amount == 150 and r.comment == "new rent" for r in new)
    assert budget_svc.schedule_of(ctx, new[0]) == budget_svc.Schedule("2027-03", False, 3)
    kept = sorted((b.month, b.amount) for b in ctx.store.budget.all() if b.series_id == rows[0].series_id)
    assert kept == [("2026-10", 100), ("2026-11", 100)]  # earlier months untouched ...
    assert budget_svc.schedule_of(ctx, by_month["2026-10"]) == budget_svc.Schedule("2026-11", False, 1)  # ... and end

    # extending the horizon continues neither the old nor the finished new plan
    assert budget_svc.set_horizon(ctx, "2027-12") == 0
    with pytest.raises(ValidationError):  # after the horizon
        budget_svc.reschedule(ctx, new[0].id, BudgetInput(end_month="2028-01", currency="CHF", amount=1))


def test_forever_series_resume_without_resurrecting_deleted_months(ctx, setup):
    rows = budget_svc.create(
        ctx,
        BudgetInput(start_month="2026-11", forever=True, type="expenditure", currency="CHF", amount=100, category="04"),
    )
    assert rows[-1].month == "2027-03"
    # delete the tail and overwrite the month before it with a one-off entry
    budget_svc.delete(ctx, [rows[-1].id])
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2027-02",
            end_month="2027-02",
            type="expenditure",
            currency="CHF",
            amount=999,
            category="04",
            overwrite=True,
        ),
    )
    budget_svc.set_horizon(ctx, "2027-05")
    by_month = sorted((b.month, b.amount) for b in ctx.store.budget.all())
    assert by_month == [
        ("2026-11", 100),
        ("2026-12", 100),
        ("2027-01", 100),
        ("2027-02", 999),
        ("2027-04", 100),
        ("2027-05", 100),
    ]


def test_future_forever_series_survives_horizon_shrink(ctx, setup):
    budget_svc.create(
        ctx,
        BudgetInput(start_month="2027-02", forever=True, type="expenditure", currency="CHF", amount=10, category="04"),
    )
    budget_svc.set_horizon(ctx, "2026-12")
    budget_svc.set_horizon(ctx, "2027-05")
    assert sorted(b.month for b in ctx.store.budget.all()) == ["2027-02", "2027-03", "2027-04", "2027-05"]


def test_final_month_limited_to_horizon_and_overwrite(ctx, setup):
    with pytest.raises(ValidationError):
        budget_svc.create(
            ctx,
            BudgetInput(
                start_month="2026-11", end_month="2027-12", type="expenditure", currency="CHF", amount=1, category="04"
            ),
        )
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-11", end_month="2026-12", type="expenditure", currency="CHF", amount=50, category="04"
        ),
    )
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-12",
            end_month="2027-01",
            type="expenditure",
            currency="CHF",
            amount=70,
            category="04",
            overwrite=True,
        ),
    )
    by_month = {(b.month, b.amount) for b in ctx.store.budget.all()}
    assert by_month == {("2026-11", 50), ("2026-12", 70), ("2027-01", 70)}


def test_overwrite_with_a_forever_entry_ends_the_older_forever_series(ctx, setup):
    old = budget_svc.create(
        ctx,
        BudgetInput(start_month="2026-10", forever=True, type="expenditure", currency="CHF", amount=100, category="04"),
    )
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2027-01",
            forever=True,
            type="expenditure",
            currency="CHF",
            amount=120,
            category="04",
            overwrite=True,
        ),
    )
    kept = [b for b in ctx.store.budget.all() if b.series_id == old[0].series_id]
    assert [(b.month, b.forever, b.generated_until) for b in kept] == [
        ("2026-10", False, ""),
        ("2026-11", False, ""),
        ("2026-12", False, ""),
    ]
    budget_svc.set_horizon(ctx, "2027-05")  # only the new amount continues: no month twice
    assert sorted((b.month, b.amount) for b in ctx.store.budget.all() if b.month >= "2027-01") == [
        (m, 120) for m in ("2027-01", "2027-02", "2027-03", "2027-04", "2027-05")
    ]


def _plan(ctx, setup):
    rent = budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-08", forever=True, type="expenditure", currency="CHF", amount=1900, category="04"
        ),
    )
    salary = budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-08", forever=True, type="income", currency="CHF", amount=6200, category=setup["salary"].id
        ),
    )
    trip = budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-12", end_month="2026-12", type="expenditure", currency="EUR", amount=500, category="09.6"
        ),
    )
    return rent, salary, trip


def test_series_group_the_plan(ctx, setup):
    rent, _, trip = _plan(ctx, setup)
    lone = budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-08", end_month="2026-09", type="expenditure", currency="CHF", amount=80, category="01.1"
        ),
    )
    plan = budget_svc.series(ctx, "2026-10")
    assert [(s.type, s.category) for s in plan] == [
        ("income", setup["salary"].id),
        ("expenditure", "01.1"),
        ("expenditure", "04"),
        ("expenditure", "09.6"),
    ]
    inc, food, home, holiday = plan
    assert (inc.first, inc.last, inc.forever, inc.once, inc.current.month) == (
        "2026-08",
        "2027-03",
        True,
        False,
        "2026-10",
    )
    assert (food.current.id, food.ended("2026-10"), food.ended("2026-09")) == (lone[-1].id, True, False)
    assert (holiday.once, holiday.current.id, holiday.differing) == (True, trip[0].id, 0)
    assert len(home.entries) == len(rent) and not home.ended("2026-10")

    # a month changed by hand differs; a month moved to another category leaves the series
    budget_svc.update_entry(ctx, rent[3].id, 2000)
    budget_svc.update_entry(ctx, rent[4].id, 1900, category="04.1")
    home = budget_svc.find_series(ctx, budget_svc.series_key(rent[0]))
    assert (home.differing, len(home.entries)) == (1, len(rent) - 1)
    moved = budget_svc.find_series(ctx, budget_svc.series_key(ctx.store.budget.get(rent[4].id)))
    assert [b.id for b in moved.entries] == [rent[4].id]
    # an entry without a series id is a series of its own
    loose = ctx.store.budget.update(replace(ctx.store.budget.get(trip[0].id), series_id=""))
    assert budget_svc.series_key(loose) == f"{loose.id}|expenditure|09.6"

    assert budget_svc.planned(ctx, budget_svc.series(ctx), "2026-12", "CHF") == pytest.approx(
        {"income": 6200, "expenditure": 1900 + 450}
    )
    assert budget_svc.planned(ctx, [], "2026-12", "CHF") == {"income": 0, "expenditure": 0}


def test_replan_a_series_from_a_month(ctx, setup):
    rent, _, _ = _plan(ctx, setup)
    key = budget_svc.series_key(rent[0])
    new = budget_svc.replan_series(
        ctx, key, BudgetInput(start_month="2026-11", forever=True, currency="EUR", amount=2000, comment="new flat")
    )
    assert [b.month for b in new] == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    assert all((b.amount, b.currency, b.category, b.forever) == (2000, "EUR", "04", True) for b in new)
    before = [(b.month, b.amount, b.forever) for b in budget_svc.find_series(ctx, key).entries]
    assert before == [("2026-08", 1900, False), ("2026-09", 1900, False), ("2026-10", 1900, False)]
    assert undo_svc.latest(ctx).label == "Replan a budget"

    # a start before the first month replaces the whole series; a quarter until a month, in another category
    key = budget_svc.series_key(new[0])
    again = budget_svc.replan_series(
        ctx,
        key,
        BudgetInput(
            start_month="2026-10", end_month="2027-01", recur_months=3, currency="CHF", amount=5, category="04.1"
        ),
    )
    assert [(b.month, b.category, b.recur_months) for b in again] == [("2026-10", "04.1", 3), ("2027-01", "04.1", 3)]
    assert budget_svc.find_series(ctx, key) is None
    with pytest.raises(ValidationError, match="Budget entry not found"):
        budget_svc.replan_series(ctx, key, BudgetInput(start_month="2026-10", currency="CHF", amount=5))
    with pytest.raises(ValidationError, match="final month must not be before"):
        budget_svc.replan_series(
            ctx,
            budget_svc.series_key(again[0]),
            BudgetInput(start_month="2026-12", end_month="2026-11", currency="CHF", amount=5),
        )


def test_delete_a_series_in_one_undoable_step(ctx, setup):
    _, salary, _ = _plan(ctx, setup)
    total = len(ctx.store.budget.all())
    assert budget_svc.delete_series(ctx, budget_svc.series_key(salary[0])) == len(salary)
    assert len(ctx.store.budget.all()) == total - len(salary)
    act = undo_svc.latest(ctx)
    assert act.label == "Delete a budget"
    undo_svc.undo(ctx, act.id)
    assert len(ctx.store.budget.all()) == total
    assert budget_svc.delete_series(ctx, "gone") == 0


def test_wealth_projection_and_unaccounted(ctx, setup):
    chf, eur = setup["chf"], setup["eur"]
    wealth_svc.upsert_entry(ctx, chf.id, "2026-08", 1000)
    wealth_svc.upsert_entry(ctx, eur.id, "2026-08", 100)  # 100 EUR @0.95 = 95 CHF (Aug rate)
    wealth_svc.upsert_entry(ctx, chf.id, "2026-09", 1500)  # EUR carried forward: 100 @0.94
    assert wealth_svc.wealth(ctx, "2026-08", "CHF") == pytest.approx(1095.0)
    assert wealth_svc.wealth(ctx, "2026-09", "CHF") == pytest.approx(1594.0)
    assert wealth_svc.current_wealth(ctx, "CHF", TODAY) == pytest.approx(1594.0)
    rows = wealth_svc.account_rows(ctx, "2026-09", "CHF")
    assert {r.account.name: r.needs_entry for r in rows} == {"Bank": False, "Euro account": True}

    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=800,
            currency="CHF",
            category=setup["salary"].id,
            to_account=chf.id,
        ),
    )
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 5), type=TX_EXPENDITURE, amount=200, currency="CHF", category="01.1", from_account=chf.id
        ),
    )
    rep = reports.month_report(ctx, "2026-09", "CHF", TODAY)
    # change in balances 499, recorded net 800 - 200 = 600; the 100 EUR lost 1 CHF of value when the
    # rate fell from 0.95 to 0.94 (a currency loss, decision 4) -> 100 left without a record (F42)
    assert rep.balance_change == pytest.approx(499.0)
    assert rep.fx_translation == pytest.approx(-1.0)
    assert rep.unexplained == pytest.approx(-100.0)
    assert rep.expenditure_tree["01"].actual == pytest.approx(200.0)
    assert rep.total_expenditure.actual == pytest.approx(200.0)  # recorded spending only
    assert [(r.account.name, r.opening, r.change) for r in rep.accounts] == [
        ("Bank", 1000.0, 500.0),
        ("Euro account", 100.0, 0.0),
    ]

    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-10", forever=True, type="income", currency="CHF", amount=1000, category=setup["salary"].id
        ),
    )
    budget_svc.create(
        ctx,
        BudgetInput(start_month="2026-10", forever=True, type="expenditure", currency="EUR", amount=100, category="01"),
    )  # 100 EUR @0.90 latest = 90 CHF
    # last recorded month 2026-09 -> projected Dec = 1594 + 3 * (1000 - 90)
    assert wealth_svc.projected_wealth(ctx, "2026-12", "CHF") == pytest.approx(1594 + 3 * 910)
    series = wealth_svc.wealth_series(ctx, "CHF", "2026-12", TODAY)
    assert [(m, p) for m, _, p in series] == [
        ("2026-08", False),
        ("2026-09", False),
        ("2026-10", True),
        ("2026-11", True),
        ("2026-12", True),
    ]
    oct_rep = reports.month_report(ctx, "2026-10", "CHF", TODAY)
    assert not oct_rep.complete and oct_rep.expenditure_tree["01"].budget == pytest.approx(90.0)
    assert oct_rep.projected_wealth == pytest.approx(1594 + 910)


def _recorded_beside_the_plan(ctx, setup) -> None:
    """Balances to August; budgets food 100, transport 200, salary 1000 from September; September food 300 spent,
    October food 50 spent and salary 1200 received."""
    chf, salary = setup["chf"].id, setup["salary"].id
    wealth_svc.upsert_entry(ctx, chf, "2026-08", 1000)  # the last recorded balances
    for cat, amount in (("01", 100), ("07", 200)):
        budget_svc.create(
            ctx,
            BudgetInput(
                start_month="2026-09", forever=True, type="expenditure", currency="CHF", amount=amount, category=cat
            ),
        )
    budget_svc.create(
        ctx,
        BudgetInput(start_month="2026-09", forever=True, type="income", currency="CHF", amount=1000, category=salary),
    )

    def spend(day: date, amount: float, category: str) -> None:
        tx_svc.save(
            ctx,
            TransactionInput(
                date=day, type=TX_EXPENDITURE, amount=amount, currency="CHF", category=category, from_account=chf
            ),
        )

    # September is over but has no balances (decision 5: same rule): food 300 > 100, transport 0 < 200
    spend(date(2026, 9, 5), 300, "01.1")
    # October is running: food 50 < 100; income 1200 > 1000
    spend(date(2026, 10, 3), 50, "01.1")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 1), type=TX_INCOME, amount=1200, currency="CHF", category=salary, to_account=chf
        ),
    )


def test_projection_takes_the_higher_of_recorded_and_budget(ctx, setup):
    """F47: per category, recorded overspending or extra income counts; an unused budget still counts."""
    _recorded_beside_the_plan(ctx, setup)
    sept = 1000 - (300 + 200)
    october = 1200 - (100 + 200)
    november = 1000 - (100 + 200)
    assert wealth_svc.projected_wealth(ctx, "2026-09", "CHF") == pytest.approx(1000 + sept)
    assert wealth_svc.projected_wealth(ctx, "2026-11", "CHF") == pytest.approx(1000 + sept + october + november)
    series = {m: v for m, v, projected in wealth_svc.wealth_series(ctx, "CHF", "2026-11", TODAY) if projected}
    assert series["2026-10"] == pytest.approx(1000 + sept + october)
    assert reports.month_report(ctx, "2026-10", "CHF", TODAY).projected_wealth == pytest.approx(1000 + sept + october)


def test_projection_with_the_budgets_alone(ctx, setup):
    """Option 1 in Settings: every projected month adds its budgeted income minus its budgeted expenditure."""
    _recorded_beside_the_plan(ctx, setup)
    ctx.store.settings.projection = PROJECTION_BUDGET
    month = 1000 - (100 + 200)  # what is recorded doesn't count, above or below its budget
    assert wealth_svc.projected_wealth(ctx, "2026-09", "CHF") == pytest.approx(1000 + month)
    assert wealth_svc.projected_wealth(ctx, "2026-11", "CHF") == pytest.approx(1000 + 3 * month)
    series = {m: v for m, v, projected in wealth_svc.wealth_series(ctx, "CHF", "2026-11", TODAY) if projected}
    assert series["2026-10"] == pytest.approx(1000 + 2 * month)
    ctx.store.settings.projection = PROJECTION_HIGHER  # back to F47's rule
    assert wealth_svc.projected_wealth(ctx, "2026-09", "CHF") == pytest.approx(1000 + 1000 - (300 + 200))


def test_revalue_entries_values_balances_at_their_month_end(ctx, setup):
    eur, chf = setup["eur"].id, setup["chf"].id
    entry = wealth_svc.upsert_entry(ctx, eur, "2026-09", 1000)
    plain = wealth_svc.upsert_entry(ctx, chf, "2026-09", 500)
    month_end = ctx.month_rate_date("2026-09")
    right = round(ctx.convert_on(1000, "EUR", "CHF", month_end), 6)
    assert entry.amount_chf == pytest.approx(right) and wealth_svc.revalue_entries(ctx) == 0
    # as an older version or a hand-edited file left them: the wrong rate, or the right date with another amount
    entry.amount_chf, entry.fx_date = 900.0, date(2026, 8, 29)
    plain.amount_chf = 499.0
    ctx.store.account_entries.update_many([entry, plain])
    assert wealth_svc.revalue_entries(ctx) == 2
    fixed = {e.id: e for e in ctx.store.account_entries.all()}
    assert fixed[entry.id].fx_date == month_end and fixed[entry.id].amount_chf == pytest.approx(right)
    assert fixed[plain.id].amount_chf == pytest.approx(500)
    assert wealth_svc.revalue_entries(ctx) == 0


def test_revalue_entries_needs_rates(ctx, setup, monkeypatch):
    wealth_svc.upsert_entry(ctx, setup["eur"].id, "2026-09", 1000)
    monkeypatch.setattr(ctx.fx, "latest_date", lambda: None)
    assert wealth_svc.revalue_entries(ctx) == 0
    monkeypatch.undo()
    monkeypatch.setattr(ctx.fx, "rate_date_on_or_before", lambda d: None)  # a month before the first rate
    assert wealth_svc.revalue_entries(ctx) == 0


def test_projection_without_any_balances_starts_with_the_first_activity(ctx, setup):
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-11",
            end_month="2026-12",
            type="income",
            currency="CHF",
            amount=500,
            category=setup["salary"].id,
        ),
    )
    assert wealth_svc.projected_wealth(ctx, "2026-10", "CHF") == 0
    assert wealth_svc.projected_wealth(ctx, "2026-12", "CHF") == pytest.approx(1000)


def test_everything_recorded_reconciles_to_zero(ctx, setup):
    """Change in balances = recorded net + revaluations: income, foreign spending, an exchange with a
    spread (F44), an investment gain (F43) and exchange-rate moves (decision 4) leave nothing unexplained."""
    chf, eur, salary = setup["chf"].id, setup["eur"].id, setup["salary"].id
    wealth_svc.upsert_entry(ctx, chf, "2026-08", 1000)
    wealth_svc.upsert_entry(ctx, eur, "2026-08", 100)

    def save(**kw):
        return tx_svc.save(ctx, TransactionInput(**kw))

    save(date=date(2026, 9, 25), type=TX_INCOME, amount=800, currency="CHF", category=salary, to_account=chf)
    save(date=date(2026, 9, 10), type=TX_EXPENDITURE, amount=50, currency="EUR", category="01.1", from_account=eur)
    save(
        date=date(2026, 9, 1), type="exchange", amount=90, currency="CHF",
        from_account=chf, to_account=eur, to_amount=93.80,
    )  # fmt: skip
    save(
        date=date(2026, 9, 30), type="revaluation", currency="EUR", category="REVAL_INVEST",
        reval_account=eur, reval_change=20,
    )  # fmt: skip
    # the true month-end balances
    wealth_svc.upsert_entry(ctx, chf, "2026-09", 1000 + 800 - 90)
    wealth_svc.upsert_entry(ctx, eur, "2026-09", 100 - 50 + 93.80 + 20)

    for view in ("CHF", "EUR"):
        rep = reports.month_report(ctx, "2026-09", view, TODAY)
        assert rep.unexplained == pytest.approx(0, abs=0.02), view
        assert all(r.unexplained == pytest.approx(0, abs=0.005) for r in rep.accounts), view
        fx, invest = rep.revaluation_lines
        assert invest.key == "REVAL_INVEST" and fx.key == "REVAL_FX"
    rep = reports.month_report(ctx, "2026-09", "CHF", TODAY)
    # in CHF the EUR account gains from the rate move (0.95 -> 0.94 CHF per EUR is a loss on 100 EUR)
    assert rep.fx_translation != 0 and rep.accounts[1].fx_gain == pytest.approx(rep.fx_translation)
    assert rep.accounts[0].fx_gain == 0  # a CHF account has no exchange-rate effect in CHF
    assert rep.total_revaluation.actual == pytest.approx(sum(line.actual for line in rep.revaluation_lines))
    # net income ignores revaluations (decision 3)
    assert rep.total_income.actual - rep.total_expenditure.actual == pytest.approx(800 - 50 * 0.95)


def test_unexplained_per_account_is_in_its_own_currency(ctx, setup):
    eur = setup["eur"].id
    wealth_svc.upsert_entry(ctx, eur, "2026-08", 100)
    wealth_svc.upsert_entry(ctx, eur, "2026-09", 70)  # 30 EUR left without a record
    rep = reports.month_report(ctx, "2026-09", "CHF", TODAY)
    [row] = rep.accounts
    assert (row.opening, row.flows, row.unexplained) == (100, 0, -30)
    assert rep.unexplained == pytest.approx(-30 * 0.94)  # valued at the September month-end rate
    assert reports.unexplained(ctx, "2026-09", "CHF", TODAY) == rep.unexplained
