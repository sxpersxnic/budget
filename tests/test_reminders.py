"""Reminders and alerts (F31): each kind drawn from its feature, the setting that turns kinds off, the count for
the sidebar, and nothing beyond what is in the app."""

import json
from datetime import date, timedelta

import pytest

from budget_app.domain.models import Bill
from budget_app.services import budget as budget_svc
from budget_app.services import goals as goal_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import reminders as rem_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.goals import GoalInput
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store

TODAY = date(2026, 10, 18)


def bill(ctx, creditor: str, due: date | None, amount: float = 120.0) -> Bill:
    return ctx.store.bills.add(
        Bill(creditor, "CH9300762011623852957", "CHF", amount, due=due.isoformat() if due else "")
    )


def kinds(found: dict) -> list[str]:
    return list(found)


def test_nothing_to_do_in_an_empty_folder(ctx):
    assert rem_svc.reminders(ctx, "CHF", TODAY) == {}
    assert rem_svc.count(ctx, TODAY) == 0


def test_balances_to_record(ctx, setup):
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-08", 1200)
    wealth_svc.upsert_entry(ctx, setup["eur"].id, "2026-09", 300)
    found = rem_svc.reminders(ctx, "CHF", TODAY)
    assert kinds(found) == [rem_svc.BALANCES]
    [item] = found[rem_svc.BALANCES]
    assert (item.kind, item.title, item.sub, item.link, item.amount, item.currency, item.tone) == (
        rem_svc.BALANCES,
        "Bank",
        "No balance for Sep 2026 · last 1'200.00 CHF",
        "/budget?month=2026-09",
        None,
        "CHF",
        rem_svc.WARN,
    )
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 0)  # recorded; a zero balance stays quiet afterwards
    assert rem_svc.reminders(ctx, "CHF", TODAY) == {}
    later = rem_svc.reminders(ctx, "CHF", date(2026, 11, 2))[rem_svc.BALANCES]
    assert [i.title for i in later] == ["Euro account"]  # the Bank's last balance is 0


def test_recurring_transactions_due(ctx, setup):
    rec_svc.save(
        ctx,
        RecurringInput(
            amount=1900, currency="CHF", category="04.1", from_account=setup["chf"].id, start=date(2026, 9, 1)
        ),
    )
    rec_svc.save(
        ctx,
        RecurringInput(
            type="income",
            amount=100,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            start=TODAY,
            payee="Boss",
        ),
    )
    found = rem_svc.reminders(ctx, "CHF", TODAY)[rem_svc.RECURRING]
    assert [(i.title, i.sub, i.amount, i.tone, i.link) for i in found] == [
        ("04.1 Rent", "Due since 1 Sep 2026", 1900, rem_svc.LATE, "/recurring"),
        ("04.1 Rent", "Due since 1 Oct 2026", 1900, rem_svc.LATE, "/recurring"),
        ("Boss · Salary", "Due today", 100, rem_svc.WARN, "/recurring"),
    ]
    assert rem_svc.count(ctx, TODAY) == 3


def test_bills_due(ctx, setup):
    bill(ctx, "Late", TODAY - timedelta(days=3))
    bill(ctx, "Today", TODAY, amount=0)
    bill(ctx, "Soon", TODAY + timedelta(days=7))
    bill(ctx, "Later", TODAY + timedelta(days=8))
    bill(ctx, "Undated", None)
    found = rem_svc.reminders(ctx, "CHF", TODAY)[rem_svc.BILLS]
    assert [(i.title, i.sub, i.amount, i.currency, i.tone) for i in found] == [
        ("Late", "Overdue since 15 Oct 2026", 120, "CHF", rem_svc.LATE),
        ("Today", "Due today", None, "CHF", rem_svc.WARN),
        ("Soon", "Due on 25 Oct 2026", 120, "CHF", rem_svc.WARN),
    ]
    assert all(i.link == "/bills" for i in found)


def test_budgets_exceeded_or_on_pace(ctx, setup):
    for code, amount in (("01.1", 500), ("07.3", 100)):
        budget_svc.create(
            ctx,
            BudgetInput(
                start_month="2026-10",
                end_month="2026-10",
                type="expenditure",
                currency="CHF",
                amount=amount,
                category=code,
            ),
        )
    for day, amount, code in ((2, 300, "01.1.1"), (9, 300, "01.1"), (3, 25, "07.3"), (8, 25, "07.3"), (12, 25, "07.3")):
        tx_svc.save(
            ctx,
            TransactionInput(
                date=date(2026, 10, day),
                type="expenditure",
                amount=amount,
                currency="CHF",
                category=code,
                from_account=setup["chf"].id,
            ),
        )
    found = rem_svc.reminders(ctx, "CHF", TODAY)[rem_svc.BUDGETS]
    assert [(i.title, i.sub, i.amount, i.tone) for i in found] == [
        ("01.1 Groceries", "100.00 over the 500.00 CHF budget", 600, rem_svc.LATE),
        ("07.3 Public transport", "On pace for 129.17 of the 100.00 CHF budget", 75, rem_svc.WARN),
    ]
    assert found[0].link == "/budget?month=2026-10&category=01.1"
    eur = rem_svc.reminders(ctx, "EUR", TODAY)[rem_svc.BUDGETS]
    assert eur[0].currency == "EUR" and eur[0].amount == pytest.approx(600 / 0.94)  # at the transactions' own rate


def test_goals_behind_or_overdue(ctx, setup):
    goal_svc.add(
        ctx, GoalInput(name="Fine", currency="CHF", target=1000, target_date=date(2027, 6, 30), saved=500), today=TODAY
    )
    behind = goal_svc.add(
        ctx, GoalInput(name="Behind", currency="CHF", target=1200, target_date=date(2027, 1, 18)), today=TODAY
    )
    g = ctx.store.goals.get(behind.id)
    g.start = date(2026, 1, 18)  # a year to save 1'200: 900 expected by now, nothing saved
    ctx.store.goals.update(g)
    late = goal_svc.add(
        ctx, GoalInput(name="Late", currency="EUR", target=400, target_date=date(2026, 11, 1), saved=100), today=TODAY
    )
    g = ctx.store.goals.get(late.id)
    g.target_date = "2026-10-01"
    ctx.store.goals.update(g)
    goal_svc.add(ctx, GoalInput(name="Open", currency="CHF", target=400, saved=0), today=TODAY)
    found = rem_svc.reminders(ctx, "CHF", TODAY)[rem_svc.GOALS]
    assert [(i.title, i.sub, i.amount, i.currency, i.tone, i.link) for i in found] == [
        ("Late", "Overdue since 1 Oct 2026 · 300.00 EUR to go", 100, "EUR", rem_svc.LATE, "/goals"),
        ("Behind", "Behind: 897.53 CHF short of the plan line", 0, "CHF", rem_svc.WARN, "/goals"),
    ]


def test_kinds_can_be_turned_off_and_the_setting_is_saved(ctx, setup, tmp_path):
    bill(ctx, "Late", TODAY - timedelta(days=1))
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-08", 10)
    assert kinds(rem_svc.reminders(ctx, "CHF", TODAY)) == [rem_svc.BALANCES, rem_svc.BILLS]
    assert rem_svc.enabled(ctx, rem_svc.BILLS)
    rem_svc.set_enabled(ctx, rem_svc.BILLS, False)
    rem_svc.set_enabled(ctx, rem_svc.GOALS, False)
    rem_svc.set_enabled(ctx, "nonsense", False)
    rem_svc.set_enabled(ctx, rem_svc.BALANCES, False)
    assert ctx.store.settings.reminders_off == [rem_svc.BALANCES, rem_svc.BILLS, rem_svc.GOALS]
    assert not rem_svc.enabled(ctx, rem_svc.BILLS)
    assert rem_svc.reminders(ctx, "CHF", TODAY) == {} and rem_svc.count(ctx, TODAY) == 0
    assert json.loads((tmp_path / "settings.json").read_text())["reminders_off"] == ["balances", "bills", "goals"]
    assert Store(tmp_path).settings.reminders_off == ["balances", "bills", "goals"]
    rem_svc.set_enabled(ctx, rem_svc.BILLS, True)
    rem_svc.set_enabled(ctx, rem_svc.BALANCES, True)
    assert ctx.store.settings.reminders_off == [rem_svc.GOALS]
    assert kinds(rem_svc.reminders(ctx, "CHF", TODAY)) == [rem_svc.BALANCES, rem_svc.BILLS]
    assert rem_svc.count(ctx, TODAY) == 2


def test_every_kind_has_its_texts():
    assert set(rem_svc.KINDS) == set(rem_svc.TITLES) == set(rem_svc.DESCRIPTIONS) == set(rem_svc.LINKS)
