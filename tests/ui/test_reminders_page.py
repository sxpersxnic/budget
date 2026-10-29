"""Reminders in the interface (F31): the Reminders page with its groups and links, the sidebar badge, and the
switches in Settings."""

from datetime import date, timedelta

from nicegui import ui
from nicegui.testing import User
from test_overview_page import settle
from test_payees_page import put

from budget_app.domain import months
from budget_app.domain.models import Bill
from budget_app.services import goals as goal_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import reminders as rem_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.goals import GoalInput
from budget_app.services.recurring import RecurringInput
from budget_app.ui import layout

TODAY = date.today()


def nav_badges(user: User) -> list[ui.badge]:
    with user:  # hidden ones too
        badges = [e for e in user.client.elements.values() if isinstance(e, ui.badge) and "nav-badge" in e.classes]
    return sorted(badges, key=lambda b: b.id)


def reminders_badge(user: User) -> ui.badge:
    return nav_badges(user)[2]  # Recurring, Bills, then Reminders


def links(user: User) -> list[str]:
    rows = [e for e in user.find(kind=ui.link).elements if "notable-row" in e.classes]
    return [e.props.get("href", "") for e in sorted(rows, key=lambda e: e.id)]


async def test_the_page_without_reminders(user: User, ctx) -> None:
    await user.open("/reminders")
    await user.should_see("Nothing to do right now.")
    paths = [p for p, _, _ in layout.NAV]
    assert ("/reminders", "Reminders", "o_notifications_none") in layout.NAV
    assert paths.index("/reminders") == paths.index("/bills") + 1
    assert not reminders_badge(user).visible


async def test_reminders_grouped_with_links_and_the_badge(user: User, ctx, setup) -> None:
    prev = months.previous_month()
    wealth_svc.upsert_entry(ctx, setup["chf"].id, months.add_months(prev, -1), 1200)
    rec_svc.save(
        ctx,
        RecurringInput(
            amount=1900,
            currency="CHF",
            category="04.1",
            from_account=setup["chf"].id,
            start=TODAY - timedelta(days=40),
            payee="Landlord",
        ),
    )
    ctx.store.bills.add(
        Bill("Swisscom", "CH9300762011623852957", "CHF", 89.9, due=(TODAY - timedelta(days=2)).isoformat())
    )
    goal = goal_svc.add(
        ctx, GoalInput(name="Holiday", currency="CHF", target=1000, target_date=TODAY + timedelta(days=200))
    )
    g = ctx.store.goals.get(goal.id)
    g.start = TODAY - timedelta(days=200)
    ctx.store.goals.update(g)
    rem_svc.set_enabled(ctx, rem_svc.BUDGETS, False)

    await user.open("/reminders")
    await user.should_see("Balances to record · 1")
    await user.should_see(f"No balance for {months.label(prev)} · last 1'200.00 CHF")
    await user.should_see("Recurring transactions due · 2")
    await user.should_see("Landlord · 04.1 Actual rental payments made for housing")
    await user.should_see("Bills due · 1")
    await user.should_see("Swisscom")
    await user.should_see("89.90 CHF")
    await user.should_see("Goals behind or overdue · 1")
    await user.should_see("Behind: ")
    await user.should_see("5 reminders")
    assert links(user) == [f"/budget?month={prev}", "/recurring", "/recurring", "/bills", "/goals"]
    assert reminders_badge(user).text == "5" and reminders_badge(user).visible
    late = [e for e in user.find(kind=ui.label).elements if "text-neg" in e.classes and e.text.startswith("Overdue")]
    assert len(late) == 1

    ctx.store.bills.delete([b.id for b in ctx.store.bills.all()])
    with user:
        layout.data_changed()
    await user.should_see("4 reminders")
    await user.should_not_see("Bills due · 1")
    assert reminders_badge(user).text == "4"


async def test_settings_switches_turn_kinds_off(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["chf"].id, months.add_months(months.previous_month(), -1), 50)
    await user.open("/settings")
    await user.should_see("Balances to record")
    await user.should_see("Accounts without a month-end balance for last month.")
    await user.should_see("Shown on the Reminders page and counted in the sidebar while the app runs.")
    assert reminders_badge(user).text == "1"
    switches = sorted(user.find(kind=ui.switch).elements, key=lambda e: e.id)
    assert [sw.value for sw in switches[-5:]] == [True] * 5
    put(user, switches[-5], False)  # balances
    await settle()
    assert ctx.store.settings.reminders_off == [rem_svc.BALANCES]
    assert not reminders_badge(user).visible
    await user.open("/reminders")
    await user.should_see("Nothing to do right now.")
    await user.open("/settings")
    switches = sorted(user.find(kind=ui.switch).elements, key=lambda e: e.id)
    assert switches[-5].value is False
    put(user, switches[-5], True)
    await settle()
    assert ctx.store.settings.reminders_off == [] and reminders_badge(user).text == "1"
