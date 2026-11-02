"""The Wealth page: the net worth as its headline, each account's balance as a value with its month said once, and
recording a balance from the header or from an account's row (moved here from the Budget page)."""

import asyncio

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_settings_page import amounts, dialog_button, dialog_field, row_texts, set_value, top_dialog

from budget_app import i18n
from budget_app.domain import months
from budget_app.services import wealth as wealth_svc
from budget_app.ui.layout import fmt

PREV = months.previous_month()  # the month the page shows: the last one that is over


def balance_of(user: User, name: str) -> ui.element:
    """The clickable balance in the row of the account `name`."""
    with user:
        label = next(e for e in user.client.elements.values() if isinstance(e, ui.label) and e.text == name)
    row = label
    while "group-row" not in row.classes:
        row = row.parent_slot.parent
    [value] = [e for e in row.descendants() if "balance-value" in e.classes]
    return value


async def test_the_net_worth_heads_the_page_and_the_month_is_said_once(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["chf"].id, PREV, 1000)
    wealth_svc.upsert_entry(ctx, setup["eur"].id, months.add_months(PREV, -1), 100)  # not recorded since
    await user.open("/accounts")
    await user.should_see("Net worth")
    title = [lb for lb in user.find(kind=ui.label).elements if "large-title" in lb.classes]
    assert [lb.text for lb in title] == ["Wealth"]  # as the sidebar says, not "Accounts"
    year, mon = months.parse(PREV)
    await user.should_see(f"End of {i18n.month_label(year, mon, long=True)}")  # the month once, under the net worth
    net = wealth_svc.wealth(ctx, PREV, "CHF")
    assert amounts(user)[0] == fmt(net, "CHF") and net > 1000  # the euros count too
    assert await row_texts(user, "Bank") == ["Bank", "1'000.00 CHF"]  # no date on the row: it's the page's month
    older = f"End of {months.label(months.add_months(PREV, -1))}"
    assert await row_texts(user, "Euro account") == ["Euro account", "100.00 EUR", older]  # an older one says so


async def test_without_balances_there_is_no_net_worth_yet(user: User, ctx, setup) -> None:
    await user.open("/accounts")
    await user.should_see("Accounts")
    await user.should_not_see("Net worth")
    assert await row_texts(user, "Bank") == ["Bank", "Record Balance…"]


async def test_record_a_balance_from_the_header_or_the_row(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["chf"].id, PREV, 1000)
    await user.open("/accounts")
    await user.should_see("Net worth")
    [header] = [
        b for b in user.find(kind=ui.button).elements if b.text == "Record Balance…" and "link-btn" not in b.classes
    ]
    assert header.props.get("flat") and not header.props.get("unelevated")  # quiet, beside New Transaction
    UserInteraction(user, {header}, None).click()
    await asyncio.sleep(0.05)
    assert dialog_field(user, ui.select, "Account").value is None  # any account
    assert dialog_field(user, ui.select, "Month").value == PREV
    dialog_button(user, "Cancel").click()

    UserInteraction(user, {balance_of(user, "Bank")}, None).trigger("click")  # a click on the balance records it
    await asyncio.sleep(0.05)
    assert "Record Balance" in [e.text for e in top_dialog(user).descendants() if isinstance(e, ui.label)]
    assert dialog_field(user, ui.select, "Account").value == setup["chf"].id
    assert dialog_field(user, ui.select, "Month").value == PREV
    set_value(user, dialog_field(user, ui.number, "Balance"), 1500)
    dialog_button(user, "Save").click()
    assert notified(user, "Balance recorded")
    assert wealth_svc.entry_for(ctx, setup["chf"].id, PREV).amount == 1500
    for _ in range(40):  # the page refreshes: the row and the net worth
        if "1'500.00 CHF" in amounts(user):
            break
        await asyncio.sleep(0.05)
    assert amounts(user).count("1'500.00 CHF") == 2

    # an account without a balance offers to record one in the row
    [link] = [b for b in user.find(kind=ui.button).elements if b.text == "Record Balance…" and "link-btn" in b.classes]
    UserInteraction(user, {link}, None).click()
    await asyncio.sleep(0.05)
    assert dialog_field(user, ui.select, "Account").value == setup["eur"].id


async def test_a_balance_of_this_month_moves_the_page_to_it(user: User, ctx, setup) -> None:
    this = months.current_month()
    wealth_svc.upsert_entry(ctx, setup["chf"].id, this, 2000)  # recorded early, before the month is over
    wealth_svc.upsert_entry(ctx, setup["eur"].id, PREV, 50)
    await user.open("/accounts")
    year, mon = months.parse(this)
    await user.should_see(f"End of {i18n.month_label(year, mon, long=True)}")
    assert await row_texts(user, "Bank") == ["Bank", "2'000.00 CHF"]
    assert await row_texts(user, "Euro account") == ["Euro account", "50.00 EUR", f"End of {months.label(PREV)}"]
