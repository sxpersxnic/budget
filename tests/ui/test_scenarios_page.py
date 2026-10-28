"""What-if scenarios in the interface (F15): the Scenarios page (list, sheets, show on the Overview, adopt, delete,
undo) and the scenario lines of the Overview's wealth chart."""

import io

import pandas as pd
from nicegui import ui
from nicegui.testing import User
from notifications import notified
from test_overview_page import card, downloaded, menu_item, open_overview, settle, traces, values
from test_payees_page import click, icon_buttons, newest, put

from budget_app.domain import months
from budget_app.domain.models import BUDGET_INCOME
from budget_app.services import scenarios as scen_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.scenarios import ChangeInput
from budget_app.ui import layout
from budget_app.ui.layout import fmt

CM = months.current_month()


def in_months(n: int) -> str:
    return months.add_months(CM, n)


def scenario(ctx, name: str = "80 % work", **kw):
    return scen_svc.add(ctx, name, **kw)


def cut(ctx, s, demo, amount: float = 4960, start: str | None = None, **kw):
    base = dict(
        type=BUDGET_INCOME, category=demo["salary"].id, amount=amount, currency="CHF", start_month=start or in_months(2)
    )
    return scen_svc.add_change(ctx, s.id, ChangeInput(**(base | kw)))


def texts(user: User) -> list[str]:
    return [lb.text for lb in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


# ------------------------------------------------------------------ the page
async def test_the_page_without_scenarios(user: User, ctx) -> None:
    await user.open("/scenarios")
    await user.should_see("No scenarios yet.")
    paths = [p for p, _, _ in layout.NAV]
    assert ("/scenarios", "Scenarios", "o_alt_route") in layout.NAV
    assert paths.index("/scenarios") == paths.index("/goals") + 1


async def test_scenarios_with_their_changes_and_outcome(user: User, ctx, demo) -> None:
    s = scenario(ctx, comment="Four days a week")
    cut(ctx, s, demo)
    scen_svc.add_change(
        ctx,
        s.id,
        ChangeInput(category="07.1.1", amount=18500, currency="CHF", start_month=in_months(4), end_month=in_months(4)),
    )
    scen_svc.add_change(
        ctx,
        s.id,
        ChangeInput(
            category="01.1", amount=0, currency="CHF", start_month=in_months(1), end_month=in_months(3), recur_months=2
        ),
    )
    empty = scenario(ctx, "Nothing yet", today=None)
    await user.open("/scenarios")
    await user.should_see("80 % work")
    await user.should_see("Four days a week")
    await user.should_see("Salary")
    await user.should_see(f"4'960.00 CHF every month · from {months.label(in_months(2))} to the end of the plan")
    await user.should_see(f"18'500.00 CHF · {months.label(in_months(4))}")
    await user.should_see(f"No budget · {months.label(in_months(1))} – {months.label(in_months(3))}")
    await user.should_see("No changes yet: the scenario is the plan.")
    out = scen_svc.outcome(ctx, s.id, "CHF")
    horizon = months.label(ctx.horizon())
    line = next(lb for lb in texts(user) if lb.startswith(f"Projected for {horizon}: {fmt(out.scenario, 'CHF')}"))
    assert line.endswith(f"· −{fmt(-out.delta, 'CHF')} against the plan")
    assert out.delta < 0
    plan = wealth_svc.projected_wealth(ctx, ctx.horizon(), "CHF")
    await user.should_see(f"Projected for {horizon}: {fmt(plan, 'CHF')} · as in the plan")
    order = [x for x in texts(user) if x in {"80 % work", "Nothing yet"}]
    assert order == ["80 % work", "Nothing yet"]
    assert len([b for b in user.find(kind=ui.button).elements if b.text == "Adopt into Plan…"]) == 1  # not for `empty`
    assert ctx.store.scenarios.get(empty.id) is not None


async def test_add_a_scenario_and_a_change_through_the_sheets(user: User, ctx, demo) -> None:
    await user.open("/scenarios")
    click(user, newest(user, ui.button, text="New Scenario"))
    click(user, newest(user, ui.button, text="Add Scenario"))
    assert notified(user, "Name is required")
    put(user, newest(user, ui.input, "Name"), "80 % work")
    put(user, newest(user, ui.textarea, "Note"), "Four days")
    click(user, newest(user, ui.button, text="Add Scenario"))
    assert notified(user, "Saved “80 % work”")
    [s] = ctx.store.scenarios.all()
    assert (s.name, s.comment, s.shown) == ("80 % work", "Four days", True)
    assert undo_svc.latest(ctx).label == "Add a scenario"
    await user.should_see("No changes yet: the scenario is the plan.")  # the list refreshes on the event loop

    click(user, newest(user, ui.button, text="Add Change…"))
    await user.should_see("In “80 % work”")
    click(user, newest(user, ui.button, text="Add Change"))
    assert notified(user, "Enter an amount; 0 takes the budget out of these months")
    put(user, newest(user, ui.number, "Amount"), 4960)
    click(user, newest(user, ui.button, text="Add Change"))
    assert notified(user, "Select an expenditure category")
    put(user, newest(user, ui.toggle), BUDGET_INCOME)
    put(user, newest(user, ui.select, "Income category"), demo["salary"].id)
    await user.should_see(f"The plan has 6'200.00 CHF for {months.label(CM)}")
    put(user, newest(user, ui.select, "From"), in_months(2))
    n = len(months.month_range(in_months(2), ctx.horizon()))
    await user.should_see(f"{n} months · The plan has 6'200.00 CHF for {months.label(in_months(2))}")
    click(user, newest(user, ui.button, text="Add Change"))
    assert notified(user, "Saved the change in “80 % work”")
    [c] = ctx.store.scenario_changes.all()
    assert (c.type, c.category, c.amount, c.start_month, c.end_month, c.recur_months) == (
        BUDGET_INCOME,
        demo["salary"].id,
        4960,
        in_months(2),
        "",
        1,
    )
    await user.should_see(f"−{fmt(1240 * n, 'CHF')} against the plan")

    click(user, newest(user, ui.button, text="Add Change…"))
    put(user, newest(user, ui.number, "Amount"), 500)
    put(user, newest(user, ui.select, "Category"), "09.1")
    put(user, newest(user, ui.select, "Until"), in_months(1))
    await user.should_see(f"2 months · The plan has no budget for this category in {months.label(CM)}")
    put(user, newest(user, ui.select, "From"), in_months(3))  # Until falls before it: back to the end of the plan
    assert newest(user, ui.select, "Until").value == "forever"
    click(user, newest(user, ui.button, text="Add Change"))
    assert notified(user, "Saved the change in “80 % work”")
    assert len(ctx.store.scenario_changes.all()) == 2


async def test_edit_and_delete_changes_and_scenarios(user: User, ctx, demo) -> None:
    s = scenario(ctx)
    c = cut(ctx, s, demo)
    await user.open("/scenarios")
    click(user, icon_buttons(user, "o_edit")[1])  # the change's pencil; the scenario's comes first
    await user.should_see("Edit Change")
    assert newest(user, ui.number, "Amount").value == 4960
    assert newest(user, ui.select, "Income category").value == demo["salary"].id
    put(user, newest(user, ui.number, "Amount"), 3100)
    put(user, newest(user, ui.select, "Until"), in_months(3))
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Saved the change in “80 % work”")
    edited = ctx.store.scenario_changes.get(c.id)
    assert (edited.amount, edited.end_month) == (3100, in_months(3))
    await user.should_see(f"3'100.00 CHF every month · {months.label(in_months(2))} – {months.label(in_months(3))}")

    click(user, icon_buttons(user, "o_edit")[0])
    await user.should_see("Edit Scenario")
    put(user, newest(user, ui.input, "Name"), "Part time")
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Saved “Part time”")
    assert ctx.store.scenarios.get(s.id).name == "Part time"
    await user.should_see("Part time")
    await settle()  # the list rebuilds on the event loop; the switch below must be the rebuilt one

    put(user, newest(user, ui.switch, text="On the Overview"), False)
    assert notified(user, "“Part time” is hidden on the Overview")
    assert not ctx.store.scenarios.get(s.id).shown
    await settle()
    put(user, newest(user, ui.switch, text="On the Overview"), True)
    assert notified(user, "“Part time” is shown on the Overview")
    await settle()

    click(user, icon_buttons(user, "o_delete")[1])  # the change
    assert notified(user, "Removed the change for Salary")
    assert ctx.store.scenario_changes.all() == []
    await user.should_see("No changes yet: the scenario is the plan.")

    cut(ctx, s, demo)
    await settle()
    click(user, icon_buttons(user, "o_delete")[0])  # the scenario
    await user.should_see("Delete “Part time”?")
    await user.should_see("Its 1 change goes with it. The plan doesn’t change.")
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "Deleted Part time")
    assert ctx.store.scenarios.all() == [] and ctx.store.scenario_changes.all() == []
    assert undo_svc.latest(ctx).label == "Delete a scenario"
    await user.should_see("No scenarios yet.")


async def test_a_scenario_gone_meanwhile_is_reported(user: User, ctx, demo) -> None:
    s = scenario(ctx)
    await user.open("/scenarios")
    ctx.store.scenarios.delete([s.id])
    put(user, newest(user, ui.switch, text="On the Overview"), False)
    assert notified(user, "This scenario no longer exists")
    await user.should_see("No scenarios yet.")


async def test_adopt_into_plan(user: User, ctx, demo) -> None:
    s = scenario(ctx)
    cut(ctx, s, demo)
    scen_svc.add_change(
        ctx,
        s.id,
        ChangeInput(category="07.1.1", amount=18500, currency="CHF", start_month=in_months(4), end_month=in_months(4)),
    )
    scenario_wealth = scen_svc.projected_wealth(ctx, s.id, ctx.horizon(), "CHF")
    before = len(ctx.store.budget.all())
    await user.open("/scenarios")
    click(user, newest(user, ui.button, text="Adopt into Plan…"))
    await user.should_see("Adopt “80 % work” into the plan?")
    await user.should_see("Its 2 changes become budget entries")
    click(user, newest(user, ui.button, text="Adopt"))
    n = len(months.month_range(in_months(2), ctx.horizon()))
    assert notified(user, f"Adopted “80 % work”: {n + 1} budget entries")
    assert len(ctx.store.budget.all()) == before - n + n + 1  # the salary months replaced, the car added
    assert ctx.store.scenarios.get(s.id) is None
    assert wealth_svc.projected_wealth(ctx, ctx.horizon(), "CHF") == scenario_wealth
    assert undo_svc.latest(ctx).label == "Adopt a scenario into the plan"
    await user.should_see("No scenarios yet.")

    other = scenario(ctx, "Later")
    scen_svc.add_change(ctx, other.id, ChangeInput(category="09.1", amount=100, currency="CHF", start_month=CM))
    await user.open("/scenarios")
    ctx.store.settings.budget_horizon = months.add_months(CM, -1)  # the plan ends before the change
    click(user, newest(user, ui.button, text="Adopt into Plan…"))
    click(user, newest(user, ui.button, text="Adopt"))
    assert user.notify.contains("The plan ends in")
    assert ctx.store.scenarios.get(other.id) is not None


async def test_a_change_elsewhere_refreshes_the_page(user: User, ctx) -> None:
    await user.open("/scenarios")
    scenario(ctx)
    with user:
        layout.data_changed()
    await user.should_see("80 % work")


# ------------------------------------------------------------------ the Overview
async def test_the_overview_draws_the_shown_scenarios(user: User, ctx, demo) -> None:
    s = scenario(ctx)
    cut(ctx, s, demo)
    hidden = scenario(ctx, "Hidden")
    cut(ctx, hidden, demo, amount=100)
    scen_svc.set_shown(ctx, hidden.id, False)
    await open_overview(user)
    wealth = traces(user, card(user, "Wealth Over Time"))
    assert [tr["name"] for tr in wealth] == ["Recorded", "Projected", "80 % work"]
    plan_line, scen_line = wealth[1], wealth[2]
    assert scen_line["x"] == plan_line["x"]  # starts at the last recorded value, like the projection
    gap = [p - s for p, s in zip(values(plan_line["y"]), values(scen_line["y"]), strict=True)]
    assert gap[:3] == [0, 0, 0]  # the recorded value, this month and next are as in the plan
    assert gap[-1] == scen_svc.outcome(ctx, s.id, "CHF").delta * -1
    assert gap == sorted(gap) and scen_line["line"]["dash"] == "dash"

    await menu_item(user, "Export Data as CSV…", card(user, "Wealth Over Time"))
    data = pd.read_csv(io.BytesIO((await downloaded(user)).content))
    assert list(data.columns) == ["month", "wealth", "type", "scenario: 80 % work"]
    assert data[data["type"] == "actual"]["scenario: 80 % work"].isna().all()
    last = data.iloc[-1]
    assert round(last["wealth"] - last["scenario: 80 % work"], 2) == round(gap[-1], 2)

    scen_svc.set_shown(ctx, s.id, False)
    currency = next(x for x in user.find(kind=ui.select).elements if x.props.get("label") == "Currency")
    put(user, currency, "EUR")
    assert [tr["name"] for tr in traces(user, card(user, "Wealth Over Time"))] == ["Recorded", "Projected"]
