from datetime import date

import pytest

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
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
    # change in balances 499, recorded net 800 - 200 = 600 -> 101 left without a record (F42)
    assert rep.balance_change == pytest.approx(499.0)
    assert rep.unexplained == pytest.approx(-101.0)
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


def test_projection_takes_the_higher_of_recorded_and_budget(ctx, setup):
    """F47: per category, recorded overspending or extra income counts; an unused budget still counts."""
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
    sept = 1000 - (300 + 200)
    october = 1200 - (100 + 200)
    november = 1000 - (100 + 200)
    assert wealth_svc.projected_wealth(ctx, "2026-09", "CHF") == pytest.approx(1000 + sept)
    assert wealth_svc.projected_wealth(ctx, "2026-11", "CHF") == pytest.approx(1000 + sept + october + november)
    series = {m: v for m, v, projected in wealth_svc.wealth_series(ctx, "CHF", "2026-11", TODAY) if projected}
    assert series["2026-10"] == pytest.approx(1000 + sept + october)
    assert reports.month_report(ctx, "2026-10", "CHF", TODAY).projected_wealth == pytest.approx(1000 + sept + october)


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
