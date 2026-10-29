"""The Overview's Due list (F31): one row per thing to do with its action (record a balance, book a recurring
transaction or a loan payment, pay a bill, open a goal), the sidebar badge counting exactly those rows, the quiet line
when nothing is due, `/reminders` leading here, and the switches in Settings."""

from datetime import date, timedelta

from nicegui import ui
from nicegui.testing import User
from notifications import notified
from test_overview_page import click, settle
from test_payees_page import put
from test_recurring_page import in_dialog

from budget_app.domain import months
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import BILL_DISMISSED, Bill
from budget_app.services import goals as goal_svc
from budget_app.services import loans as loan_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import reminders as rem_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.goals import GoalInput
from budget_app.services.loans import LoanInput
from budget_app.services.recurring import RecurringInput
from budget_app.ui import layout

TODAY = date.today()


def rent_title() -> str:
    return f"Landlord · {get_coicop().label('04.1')}"


def due_badge(user: User) -> ui.badge:
    """The sidebar's one badge, beside Overview."""
    return layout._due_badges[user.client]["/"]


def rows(user: User) -> list[ui.element]:
    return sorted((e for e in user.find(kind=ui.element).elements if "due-row" in e.classes), key=lambda e: e.id)


def links(user: User) -> list[str]:
    found = [e for e in user.find(kind=ui.link).elements if "due-text" in e.classes]
    return [e.props.get("href", "") for e in sorted(found, key=lambda e: e.id)]


def action(user: User, kind: str) -> ui.button:
    [button] = user.find(marker=f"due-{kind}").elements
    return button


def everything(ctx, setup) -> dict:
    """A balance to record, a rent due twice, an overdue bill, a goal behind its plan and a loan payment due."""
    prev = months.previous_month()
    wealth_svc.upsert_entry(ctx, setup["chf"].id, months.add_months(prev, -1), 1200)
    rent = rec_svc.save(
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
    bill = ctx.store.bills.add(
        Bill("Swisscom", "CH9300762011623852957", "CHF", 89.9, due=(TODAY - timedelta(days=2)).isoformat())
    )
    goal = goal_svc.add(
        ctx, GoalInput(name="Holiday", currency="CHF", target=1000, target_date=TODAY + timedelta(days=200))
    )
    g = ctx.store.goals.get(goal.id)
    g.start = TODAY - timedelta(days=200)
    ctx.store.goals.update(g)
    loan = loan_svc.add(
        ctx,
        LoanInput(
            name="Car",
            currency="CHF",
            principal=5_000,
            start=TODAY - timedelta(days=60),
            rate=0,
            recur_months=1,
            first_due=TODAY,
            amortization=500,
            cash_account=setup["chf"].id,
        ),
    )
    return {"prev": prev, "rent": rent, "bill": bill, "goal": goal, "loan": loan}


async def test_nothing_due_is_one_quiet_line(user: User, ctx) -> None:
    ctx.store.settings.onboarding_done = True
    await user.open("/")
    await user.should_see("Nothing due. Balances to record, bookings and bills show here when they need you.")
    assert layout.BADGES["/"] == "Things to do"
    assert not due_badge(user).visible and not rows(user)


async def test_the_due_list_with_its_actions_and_the_badge(user: User, ctx, setup) -> None:
    found = everything(ctx, setup)
    prev = found["prev"]
    await user.open("/")
    await user.should_see("5 things to do")
    await user.should_see(rent_title())
    await user.should_see(f"No balance for {months.label(prev)} · last 1'200.00 CHF")
    await user.should_see("Swisscom")
    await user.should_see("89.90 CHF")
    await user.should_see("Behind: ")
    await user.should_see("Car")
    # what is overdue first (the bill, the rent due twice), then the rest in the order of the kinds
    assert links(user) == ["/recurring", "/bills", f"/budget?month={prev}", "/loans", "/goals"]
    assert due_badge(user).text == "5" and due_badge(user).visible and len(rows(user)) == 5
    late = [e for e in user.find(kind=ui.label).elements if "text-neg" in e.classes and "n-sub" in e.classes]
    assert sorted(e.text for e in late)[0].startswith("2 occurrences due since ")

    await click(user, action(user, rem_svc.RECURRING))  # books the earliest at the series' amount
    assert notified(user, f"Booked {rent_title()}")
    [tx] = ctx.store.transactions.all()
    assert (tx.amount, tx.recurring_id) == (1900, found["rent"].id)
    await user.should_see("Due since ")
    assert due_badge(user).text == "5"  # the rent is still due once

    await click(user, action(user, rem_svc.LOANS))
    assert notified(user, "Booked Car")
    assert loan_svc.due(ctx, TODAY) == []
    assert due_badge(user).text == "4" and len(rows(user)) == 4

    await click(user, action(user, rem_svc.BILLS))
    await user.should_see("Pay Bill")
    [amount] = in_dialog(user, ui.number, "Amount")
    assert amount.value == 89.9
    await click(user, in_dialog(user, ui.button, text="Cancel")[0])

    await click(user, action(user, rem_svc.BALANCES))
    await user.should_see("Record Balance")
    [balance] = in_dialog(user, ui.number, "Balance")
    put(user, balance, 1300)
    await click(user, in_dialog(user, ui.button, text="Save")[0])
    assert wealth_svc.entry_for(ctx, setup["chf"].id, prev).amount == 1300
    assert due_badge(user).text == "3" and len(rows(user)) == 3

    await click(user, action(user, rem_svc.GOALS))
    await user.should_see("New Goal")  # the Goals page, in the window's shell (NEW-SPA)
    assert user.client.sub_pages_router.current_path == "/goals"


async def test_a_stale_row_is_reported(user: User, ctx, setup) -> None:
    found = everything(ctx, setup)
    await user.open("/")
    await user.should_see("Swisscom")
    bill = ctx.store.bills.get(found["bill"].id)
    bill.status = BILL_DISMISSED  # dealt with meanwhile, elsewhere
    ctx.store.bills.update(bill)
    await click(user, action(user, rem_svc.BILLS))
    assert notified(user, "This bill is no longer in the list")
    await user.should_not_see("Swisscom")
    rec_svc.skip(ctx, found["rent"].id, found["rent"].next_due)
    await click(user, action(user, rem_svc.RECURRING))
    assert notified(user, "This occurrence has already been booked or skipped")


async def test_a_long_list_shows_the_rest_on_request(user: User, ctx, setup) -> None:
    for k in range(8):
        ctx.store.bills.add(Bill(f"Creditor {k}", "CH9300762011623852957", "CHF", 10 + k, due=TODAY.isoformat()))
    await user.open("/")
    await user.should_see("8 things to do")
    assert len(rows(user)) == 6
    [more] = [b for b in user.find(kind=ui.button).elements if b.text == "Show 2 more"]
    await click(user, more)
    assert len(rows(user)) == 8 and due_badge(user).text == "8"


async def test_reminders_leads_to_the_overview(user: User, ctx, setup) -> None:
    everything(ctx, setup)
    await user.open("/reminders")
    await user.should_see("5 things to do")
    with user:
        assert user.client.page.path == "/"


async def test_settings_switches_turn_kinds_off(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["chf"].id, months.add_months(months.previous_month(), -1), 50)
    await user.open("/settings")
    await user.should_see("Balances to record")
    await user.should_see("Accounts without a month-end balance for last month.")
    await user.should_see("Loan payments due")
    await user.should_see("Shown in the Overview's Due list and counted in the sidebar while the app runs.")
    assert due_badge(user).text == "1"
    switches = [user.find(marker=f"reminder-{kind}").elements.pop() for kind in rem_svc.KINDS]
    assert [sw.value for sw in switches] == [True] * 5
    put(user, switches[0], False)  # balances
    await settle()
    assert ctx.store.settings.reminders_off == [rem_svc.BALANCES]
    assert not due_badge(user).visible
    await user.open("/")
    await user.should_see("Nothing due.")
    for kind in rem_svc.KINDS:
        rem_svc.set_enabled(ctx, kind, False)
    await user.open("/")
    await user.should_see("Wealth")
    await user.should_not_see("Nothing due.")  # every kind off: no Due list at all
    for kind in rem_svc.KINDS:
        rem_svc.set_enabled(ctx, kind, True)
    await user.open("/settings")
    balances = user.find(marker=f"reminder-{rem_svc.BALANCES}").elements.pop()
    assert balances.value is True
    put(user, balances, False)
    await settle()
    put(user, balances, True)
    await settle()
    assert ctx.store.settings.reminders_off == [] and due_badge(user).text == "1"
