"""Savings goals in the interface (F14): the Goals page (list, sheet, archive, restore, delete, undo), the Overview's
Goals card and the free-wealth line of the wealth chart."""

import io
from datetime import date, timedelta

import pandas as pd
from nicegui import ui
from nicegui.testing import User
from notifications import notified
from test_overview_page import card, downloaded, menu_item, open_overview, traces, values
from test_payees_page import click, icon_buttons, newest, put

from budget_app import i18n
from budget_app.domain import months
from budget_app.domain.models import GOAL_MONTHS
from budget_app.services import goals as goal_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.goals import GoalInput
from budget_app.ui import layout
from budget_app.ui.layout import fmt

TODAY = date.today()


def label(d: date) -> str:
    return i18n.date_label(d)


def in_months(n: int) -> date:
    """The last day of the month `n` months from now (whole months away, whatever today's day)."""
    return months.last_day(months.add_months(months.current_month(), n))


def goal(ctx, name: str, **kw):
    base = dict(name=name, currency="CHF", target=4000, target_date=in_months(8))
    return goal_svc.add(ctx, GoalInput(**(base | kw)))


def _change(ctx, goal_id: str, **values) -> None:
    g = ctx.store.goals.get(goal_id)
    for k, v in values.items():
        setattr(g, k, v)
    ctx.store.goals.update(g)


def shown(user: User, label: str, sheet_element) -> bool:
    """Whether the open sheet (the one `sheet_element` is in) shows a number field `label`; `find` sees only what is
    visible, and the sheets closed before stay in the simulation."""
    return any(n.id > sheet_element.id and n.props.get("label") == label for n in user.find(kind=ui.number).elements)


def labels(user: User) -> list[ui.label]:
    return sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)


def texts(user: User) -> list[str]:
    return [lb.text for lb in labels(user)]


# ------------------------------------------------------------------ the page
async def test_the_page_without_goals(user: User, ctx) -> None:
    await user.open("/goals")
    await user.should_see("No goals yet.")
    await user.should_see("0 goals")
    tabs = [p for p, _ in layout.SECTIONS["/budget-entries"]]  # a tab of Plan, after the budget plan
    assert tabs.index("/goals") == tabs.index("/budget-entries") + 1
    await open_overview(user)
    await user.should_not_see("All Goals")


async def test_goals_with_their_progress(user: User, ctx, demo) -> None:
    holiday = goal(ctx, "Holiday", saved=1000)
    goal(ctx, "Car", target=20_000, target_date=None, saved=5000)
    goal(ctx, "Bike", target=800, saved=900, target_date=in_months(2))
    late = goal(ctx, "Late", target=1000, saved=100, target_date=in_months(1))
    _change(ctx, late.id, target_date=(TODAY - timedelta(days=3)).isoformat())  # its date has passed since
    goal(ctx, "Fund", kind=GOAL_MONTHS, months=3, target_date=None, account_id=demo["chf"].id, reserve=True)
    behind = goal(ctx, "Behind", target=1200, saved=0)
    _change(ctx, behind.id, start=TODAY - timedelta(days=200))  # nothing saved in 200 days
    await user.open("/goals")
    await user.should_see("6 goals")
    needed = goal_svc.status(ctx, holiday).progress.needed
    await user.should_see("1'000.00 of 4'000.00 CHF · 25 %")
    await user.should_see(f"{fmt(needed, 'CHF')} a month until {label(in_months(8))} · on track")
    await user.should_see("15'000.00 CHF to go · no target date")
    await user.should_see("Reached")
    await user.should_see("900.00 of 800.00 CHF · 112 %")
    overdue = next(lb for lb in labels(user) if lb.text.startswith("Overdue since"))
    assert overdue.text == f"Overdue since {label(TODAY - timedelta(days=3))} · 900.00 CHF to go"
    assert "text-neg" in overdue.classes
    behind_line = next(lb for lb in labels(user) if lb.text.endswith(" · behind"))
    assert "text-neg" in behind_line.classes
    reached = next(lb for lb in labels(user) if lb.text == "Reached")
    assert "text-pos" in reached.classes
    meters = [e for e in user.find(kind=ui.element).elements if "meter" in e.classes]
    assert sum("done" in m.classes for m in meters) == 1
    average = goal_svc.monthly_spending(ctx, "CHF")
    await user.should_see(f"Balance of Bank (CHF) · 3 months of spending at {fmt(average, 'CHF')}")
    await user.should_see(" · reserved in the projection")
    await user.should_see("Saved by hand")
    order = [n for n in texts(user) if n in {"Holiday", "Car", "Bike", "Late", "Fund", "Behind"}]
    assert order == ["Late", "Bike", "Behind", "Holiday", "Car", "Fund"]  # by target date, none last


async def test_an_emergency_fund_without_spending(user: User, ctx) -> None:
    goal(ctx, "Fund", kind=GOAL_MONTHS, months=3, saved=50)
    await user.open("/goals")
    await user.should_see("No spending recorded in the last 12 months yet")
    await user.should_see("Saved by hand · 3 months of spending at 0.00 CHF")
    await user.should_see("50.00 CHF")


async def test_add_a_goal_through_the_sheet(user: User, ctx, demo) -> None:
    await user.open("/goals")
    click(user, newest(user, ui.button, text="New Goal"))
    await user.should_see("New Goal")
    click(user, newest(user, ui.button, text="Add Goal"))
    assert notified(user, "Name is required")
    put(user, newest(user, ui.input, "Name"), "Holiday")
    click(user, newest(user, ui.button, text="Add Goal"))
    assert notified(user, "Enter a target amount above 0")
    put(user, newest(user, ui.number, "Target"), 4000)
    put(user, newest(user, ui.input, "Target Date"), in_months(8).isoformat())
    saved = newest(user, ui.number, "Saved So Far")
    put(user, saved, 1000)
    put(user, newest(user, ui.switch, text="Reserve in the projection"), True)
    click(user, newest(user, ui.button, text="Add Goal"))
    assert notified(user, "Saved “Holiday”")
    [g] = ctx.store.goals.all()
    assert (g.target, g.saved, g.reserve, g.target_date, g.start_amount) == (
        4000,
        1000,
        True,
        in_months(8).isoformat(),
        1000,
    )
    assert undo_svc.latest(ctx).label == "Add a savings goal"
    await user.should_see("1 goal")

    click(user, newest(user, ui.button, text="New Goal"))
    put(user, newest(user, ui.input, "Name"), "Fund")
    kind = newest(user, ui.toggle)
    put(user, kind, GOAL_MONTHS)
    assert not shown(user, "Target", kind) and shown(user, "Months", kind)
    average = goal_svc.monthly_spending(ctx, "CHF")
    await user.should_see(
        f"{fmt(3 * average, 'CHF')}: 3 × the average spending of the last 12 complete months "
        f"({fmt(average, 'CHF')} a month)"
    )
    put(user, newest(user, ui.number, "Months"), 6)
    await user.should_see(f"{fmt(6 * average, 'CHF')}: 6 × the average")
    account = newest(user, ui.select, "Linked Account")
    assert account.options[""] == "None – I enter what I have saved"
    put(user, account, demo["chf"].id)
    assert not shown(user, "Saved So Far", kind)
    click(user, newest(user, ui.button, text="Add Goal"))
    assert notified(user, "Saved “Fund”")
    fund = next(g for g in ctx.store.goals.all() if g.name == "Fund")
    assert (fund.kind, fund.months, fund.account_id, fund.saved) == (GOAL_MONTHS, 6, demo["chf"].id, 0)


async def test_edit_a_goal(user: User, ctx) -> None:
    g = goal(ctx, "Holiday", saved=1000)
    await user.open("/goals")
    click(user, icon_buttons(user, "o_edit")[0])
    await user.should_see("Edit Goal")
    assert newest(user, ui.number, "Target").value == 4000
    assert newest(user, ui.input, "Target Date").value == in_months(8).isoformat()
    put(user, newest(user, ui.input, "Name"), "Summer")
    put(user, newest(user, ui.input, "Target Date"), "")
    put(user, newest(user, ui.number, "Saved So Far"), 1500)
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Saved “Summer”")
    edited = ctx.store.goals.get(g.id)
    assert (edited.name, edited.target_date, edited.saved) == ("Summer", "", 1500)
    await user.should_see("2'500.00 CHF to go · no target date")
    assert undo_svc.latest(ctx).label == "Change a savings goal"

    goal(ctx, "Car")
    click(user, icon_buttons(user, "o_edit")[0])
    put(user, newest(user, ui.input, "Name"), "car")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "A goal called “Car” already exists")


async def test_archive_restore_and_delete(user: User, ctx) -> None:
    g = goal(ctx, "Holiday")
    await user.open("/goals")
    click(user, icon_buttons(user, "o_archive")[0])
    assert notified(user, "Archived “Holiday”")
    assert ctx.store.goals.get(g.id).archived
    await user.should_see("0 goals")
    await user.should_see("Archived · 1 goal")
    click(user, newest(user, ui.button, text="Restore"))
    assert notified(user, "Restored “Holiday”")
    assert not ctx.store.goals.get(g.id).archived
    await user.should_see("1 goal")

    click(user, icon_buttons(user, "o_delete")[-1])
    await user.should_see("Delete “Holiday”?")
    await user.should_see("Archive it instead to keep it out of the way. Accounts and transactions don’t change.")
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "Deleted Holiday")
    assert ctx.store.goals.get(g.id) is None
    assert undo_svc.latest(ctx).label == "Delete a savings goal"

    other = goal(ctx, "Car")
    await user.open("/goals")
    ctx.store.goals.delete([other.id])  # gone in the meantime
    click(user, icon_buttons(user, "o_archive")[0])
    assert notified(user, "This goal no longer exists")
    await user.should_see("0 goals")


async def test_a_change_elsewhere_refreshes_the_page(user: User, ctx) -> None:
    await user.open("/goals")
    goal(ctx, "Holiday")
    with user:
        layout.data_changed()
    await user.should_see("1 goal")


# ------------------------------------------------------------------ the Overview
async def test_the_overview_card_and_the_free_wealth_line(user: User, ctx, demo) -> None:
    goal(ctx, "Holiday", saved=1000, reserve=True)
    goal(ctx, "Car", target=20_000, target_date=None, saved=5000)
    goal(ctx, "Bike", target=800, target_date=in_months(2))
    goal(ctx, "Later", target=100, target_date=in_months(20))
    await open_overview(user)
    await user.should_see("All Goals")
    shown = [n for n in texts(user) if n in {"Holiday", "Car", "Bike", "Later"}]
    assert shown == ["Bike", "Holiday", "Later"]  # the first three by target date
    horizon = ctx.horizon()
    reserved = goal_svc.reserve_at(ctx, horizon, "CHF")
    assert reserved == 4000  # in full by the horizon, 12 months away
    free = wealth_svc.projected_wealth(ctx, horizon, "CHF") - reserved
    await user.should_see(
        f"Projected for {months.label(horizon)}: {fmt(free, 'CHF')} free after 4'000.00 CHF reserved for goals"
    )
    wealth = traces(user, card(user, "Wealth Over Time"))
    assert [tr["name"] for tr in wealth] == ["Recorded", "Projected", "Free after goals"]
    plan_line, free_line = wealth[1], wealth[2]
    assert free_line["x"] == plan_line["x"]
    gap = [p - f for p, f in zip(values(plan_line["y"]), values(free_line["y"]), strict=True)]
    assert gap[-1] == 4000 and gap[0] == 1000  # what the goal holds now, then the planned savings
    assert gap == sorted(gap)


async def test_the_card_follows_the_currency_and_hides_without_reserve(user: User, ctx, demo) -> None:
    goal(ctx, "Holiday", saved=1000)
    await open_overview(user)
    await user.should_see("Holiday")
    await user.should_not_see("reserved for goals")
    assert [tr["name"] for tr in traces(user, card(user, "Wealth Over Time"))] == ["Recorded", "Projected"]
    g = ctx.store.goals.all()[0]
    g.reserve = True
    ctx.store.goals.update(g)
    currency = next(s for s in user.find(kind=ui.select).elements if s.props.get("label") == "Currency")
    put(user, currency, "EUR")
    await user.should_see("reserved for goals")
    reserved = goal_svc.reserve_at(ctx, ctx.horizon(), "EUR")
    await user.should_see(f"after {fmt(reserved, 'EUR')} reserved for goals")
    await menu_item(user, "Export Data as CSV…", card(user, "Wealth Over Time"))
    data = pd.read_csv(io.BytesIO((await downloaded(user)).content))
    assert list(data.columns) == ["month", "wealth", "type", "free_after_goals"]
    assert data[data["type"] == "actual"]["free_after_goals"].isna().all()
    last = data.iloc[-1]
    assert round(last["wealth"] - last["free_after_goals"], 2) == round(reserved, 2)
