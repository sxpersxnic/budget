"""Recurring's *Found in Your Transactions* (F17, formerly the Subscriptions page): the detected list with its two-fact
captions, the details in a tooltip and the totals, Make Recurring through F05's sheet, Ignore and Watch Again with
undo, the running series' yearly cost in *Your Series*, the empty states that teach, and `/subscriptions` leading
here."""

import asyncio
from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_recurring_page import in_dialog

from budget_app import i18n
from budget_app.domain import months
from budget_app.services import recurring as rec_svc
from budget_app.services import subscriptions as sub_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.pages import recurring as page


def day(months_back: int, d: int) -> date:
    m = months.add_months(months.current_month(), -months_back)
    return date(int(m[:4]), int(m[5:]), d)


def spend(ctx, setup, when: date, amount: float, payee: str, **kw):
    base = dict(
        date=when, type="expenditure", amount=amount, currency="CHF", category="08.3", from_account=setup["chf"].id
    )
    return tx_svc.save(ctx, TransactionInput(**(base | {"payee": payee} | kw)))


def charges(ctx, setup):
    """Swisscom monthly on the 10th for six months, ZVV on the 2nd for four; the last ones last month."""
    for back in range(6, 0, -1):
        spend(ctx, setup, day(back, 10), 49.9, "Swisscom", comment="Mobile")
    for back in range(4, 0, -1):
        spend(ctx, setup, day(back, 2), 85, "ZVV", category="07.3")


def labels(user: User) -> list[str]:
    return [lb.text for lb in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


def buttons(user: User, text: str) -> list[ui.button]:
    return sorted((b for b in user.find(kind=ui.button).elements if b.text == text), key=lambda b: b.id)


def icon_buttons(user: User, icon: str) -> list[ui.button]:
    return sorted((b for b in user.find(kind=ui.button).elements if b.props.get("icon") == icon), key=lambda b: b.id)


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


async def test_the_empty_page_teaches_and_subscriptions_leads_here(user: User, ctx) -> None:
    assert "/subscriptions" not in layout.TAB_PARENT  # no page of its own any more
    await user.open("/recurring")
    with user:
        assert user.client.page.path == "/recurring"
    await user.should_see("Found in Your Transactions")
    await user.should_see("shows here once it was charged 3 times (2 times for half-yearly and yearly ones)")
    await user.should_see("Rent, premiums, a salary: add what repeats with New Recurring Transaction")
    shown = " ".join(labels(user))
    assert "Ignored" not in shown and "Due" not in labels(user)  # nothing can be due without a series
    assert "spending about" not in shown


async def test_detected_subscriptions_with_their_captions(user: User, ctx, setup) -> None:
    charges(ctx, setup)
    await user.open("/recurring")
    await user.should_see("Found in Your Transactions")
    shown = labels(user)
    assert shown.index("ZVV") < shown.index("Swisscom")  # the dearer year first
    assert "2 subscriptions · about 1'618.80 CHF a year" in shown
    assert "1'020.00 CHF a year" in shown and "598.80 CHF a year" in shown
    [sub] = [s for s in sub_svc.detect(ctx, "CHF") if s.name == "Swisscom"]
    assert page.caption(sub) in shown
    assert page.caption(sub) == f"49.90 CHF · Every month · next around {i18n.date_label(day(0, 10))}"  # two facts
    assert page.details(sub).startswith("6 charges since ") and "last on" in page.details(sub)
    tips = [e for e in user.find(kind=ui.tooltip).elements if e.text == page.details(sub)]
    assert tips  # the rest in the row's tooltip


async def test_make_recurring_opens_the_prefilled_sheet_and_the_row_leaves(user: User, ctx, setup) -> None:
    charges(ctx, setup)
    await user.open("/recurring")
    await user.should_see("Swisscom")
    swisscom = buttons(user, "Make Recurring…")[1]  # the second row
    click(user, swisscom)
    await user.should_see("New Recurring Transaction")
    [start] = in_dialog(user, ui.input, "First Date")
    assert start.value == day(1, 10).isoformat()
    [amount] = in_dialog(user, ui.number, "Amount")
    assert amount.value == 49.9
    [payee] = in_dialog(user, ui.input, "Payee")
    assert payee.value == "Swisscom"
    click(user, in_dialog(user, ui.button, text="Add Recurring Transaction")[0])
    await settle()
    [series] = ctx.store.recurring.all()
    assert series.payee == "Swisscom" and series.next_due == day(0, 10)
    assert notified(user, "Recurring transaction added")
    shown = labels(user)
    assert "1 subscription · about 1'020.00 CHF a year" in shown
    assert "Swisscom · 08.3 Information and communication services" in shown  # now under Your Series
    assert "spending about 598.80 CHF a year" in shown


async def test_ignore_and_watch_again(user: User, ctx, setup) -> None:
    charges(ctx, setup)
    await user.open("/recurring")
    await user.should_see("ZVV")
    ignore = icon_buttons(user, "o_visibility_off")[0]  # ZVV, the first row
    assert ignore.props["aria-label"] == "Ignore"  # an icon-only button says what it does
    click(user, ignore)
    await settle()
    assert notified(user, "Ignored ZVV")
    await user.should_see("Ignored · 1 subscription")
    assert "1 subscription · about 598.80 CHF a year" in labels(user)
    assert [i.name for i in sub_svc.ignored(ctx)] == ["ZVV"]
    click(user, buttons(user, "Watch Again")[0])
    await settle()
    assert notified(user, "Watching ZVV again")
    assert sub_svc.ignored(ctx) == []
    assert "2 subscriptions · about 1'618.80 CHF a year" in labels(user)


async def test_a_stale_watch_again_is_reported(user: User, ctx, setup) -> None:
    charges(ctx, setup)
    await user.open("/recurring")
    await user.should_see("ZVV")
    click(user, icon_buttons(user, "o_visibility_off")[0])
    await settle()
    [item] = sub_svc.ignored(ctx)
    ctx.store.subscription_ignores.delete([item.id])  # gone meanwhile (an undo elsewhere)
    click(user, buttons(user, "Watch Again")[0])
    await user.should_see("This subscription is not ignored")


async def test_a_charge_deleted_meanwhile_is_reported(user: User, ctx, setup) -> None:
    charges(ctx, setup)
    await user.open("/recurring")
    await user.should_see("ZVV")
    last = max((tx for tx in ctx.store.transactions.all() if tx.payee == "ZVV"), key=lambda tx: tx.date)
    ctx.store.transactions.delete([last.id])
    click(user, buttons(user, "Make Recurring…")[0])
    await user.should_see("Transaction not found")


async def test_the_running_series_and_their_yearly_cost(user: User, ctx, setup) -> None:
    rec_svc.save(
        ctx,
        RecurringInput(
            amount=1900, currency="CHF", category="04.1", from_account=setup["chf"].id, start=day(0, 1), recur_months=1
        ),
    )
    rec_svc.save(
        ctx,
        RecurringInput(
            amount=300, currency="CHF", category="12.1", from_account=setup["chf"].id, start=day(0, 1), recur_months=3
        ),
    )
    await user.open("/recurring")
    await user.should_see("Your Series")
    shown = labels(user)
    assert "spending about 24'000.00 CHF a year" in shown  # 22'800 + 1'200
    assert any(text.startswith("300.00 CHF · Every quarter") for text in shown)
