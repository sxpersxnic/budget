"""The Shared Expenses page (F23): people with their open items, the settle-up sheet, the summary sheet (copy, PDF,
text) and the settled list."""

from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_budget_page import find_all
from test_investments_page import field, settle, sheet_button, sheet_texts, top_sheet
from test_overview_page import downloaded
from test_payees_page import click, icon_buttons, newest, put
from test_shared import advance, repay

from budget_app import i18n
from budget_app.services import shared as shared_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.ui import layout
from budget_app.ui.pages import shared as page

TODAY = date.today()
D = date


def label(d: date) -> str:
    return i18n.date_label(d)


def texts(user: User) -> list[str]:
    return [lb.text for lb in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


@pytest.fixture
def debts(ctx, setup):
    """Anna owes 40 CHF and 45 EUR, Ben 50 CHF; Carla settled 80 CHF this year."""
    this_year = D(TODAY.year, 1, 15)
    dinner = tx_svc.save(ctx, advance(setup, this_year, 120, 60, "Anna", payee="Restaurant X"))
    tx_svc.save(ctx, repay(setup, dinner, D(TODAY.year, 2, 1), 20))
    trip = tx_svc.save(
        ctx,
        advance(
            setup, D(TODAY.year, 3, 1), 90, 45, "Anna", currency="EUR", from_account=setup["eur"].id, category="07.3"
        ),
    )
    ben = tx_svc.save(ctx, advance(setup, D(TODAY.year, 3, 5), 50, 50, "Ben"))
    old = tx_svc.save(ctx, advance(setup, D(TODAY.year, 1, 2), 80, 80, "Carla"))
    tx_svc.save(ctx, repay(setup, old, D(TODAY.year, 1, 9), 80))
    return {"dinner": dinner, "trip": trip, "ben": ben, "old": old, **setup}


# ------------------------------------------------------------------ the page
async def test_the_page_without_debts(user: User, ctx) -> None:
    await user.open("/shared")
    await user.should_see("Nobody owes you anything.")
    await user.should_see("0 people owe you")
    tabs = [p for p, _ in layout.SECTIONS["/accounts"]]  # a tab of Wealth, after Loans
    assert tabs.index("/shared") == tabs.index("/loans") + 1


async def test_people_with_their_items(user: User, ctx, debts) -> None:
    await user.open("/shared")
    await user.should_see("2 people owe you")
    await user.should_see(f"40.00 CHF + 45.00 EUR open · 2 items · oldest {label(D(TODAY.year, 1, 15))}")
    await user.should_see(f"settled in {TODAY.year}: 20.00 CHF")
    await user.should_see(f"50.00 CHF open · 1 item · oldest {label(D(TODAY.year, 3, 5))}")
    await user.should_see(f"nothing settled in {TODAY.year}")
    names = [x for x in texts(user) if x in {"Anna", "Ben", "Carla"}]
    assert names == ["Anna", "Ben", "Carla"]  # by the oldest open item; settled people last
    await user.should_see("Settled · 1 person")
    item = f"{label(D(TODAY.year, 1, 15))} · Restaurant X · 40.00 of 60.00 CHF open"
    [row] = [lb for lb in find_all(user, ui.label, hidden=True) if lb.text == item]
    items = row.parent_slot.parent.parent_slot.parent
    assert not items.visible  # the items are folded
    toggle = icon_buttons(user, "expand_more")[0]
    assert toggle.props["aria-label"] == "Open items"
    assert icon_buttons(user, "o_description")[0].props["aria-label"] == "Summary…"
    click(user, toggle)
    await settle()
    assert items.visible and toggle.props["icon"] == "expand_less"
    UserInteraction(user, {row.parent_slot.parent}, None).trigger("click")  # a click opens the transaction
    await settle()
    assert field(user, ui.number, "Amount").value == 120
    click(user, toggle)
    await settle()
    assert not items.visible and toggle.props["icon"] == "expand_more"


# ------------------------------------------------------------------ settling up
async def test_settle_up_in_the_sheet(user: User, ctx, debts) -> None:
    await user.open("/shared")
    click(user, newest(user, ui.button, text="Settle Up…"))  # Ben's row is the last one
    await settle()
    assert "Settle Up with Ben" in sheet_texts(user)
    assert field(user, ui.number, "Amount Received").value == 50
    assert field(user, ui.select, "To Account").value == debts["chf"].id  # the only CHF account
    assert f"Settles 1 item: {label(D(TODAY.year, 3, 5))} · 50.00 CHF" in sheet_texts(user)
    put(user, field(user, ui.number, "Amount Received"), 60)
    await settle()
    assert "Ben owes 50.00 CHF, not more" in sheet_texts(user)
    assert not sheet_button(user, "Record Repayment").enabled
    put(user, field(user, ui.number, "Amount Received"), 30)
    put(user, field(user, ui.input, "Date"), D(TODAY.year, 4, 1).isoformat())
    put(user, field(user, ui.textarea, "Note"), "cash")
    await settle()
    click(user, sheet_button(user, "Record Repayment"))
    await settle()
    assert notified(user, "Recorded 30.00 CHF from Ben")
    [repayment] = [tx for tx in ctx.store.transactions.all() if tx.settles_id == debts["ben"].id]
    assert (repayment.amount, repayment.date, repayment.comment, repayment.payee) == (
        30,
        D(TODAY.year, 4, 1),
        "cash",
        "Ben",
    )
    assert undo_svc.latest(ctx).label == "Settle up"
    await user.should_see(f"20.00 CHF open · 1 item · oldest {label(D(TODAY.year, 3, 5))}")


async def test_settle_up_with_two_currencies(user: User, ctx, debts) -> None:
    await user.open("/shared")
    with user:
        page.open_settle_sheet(ctx, "anna")
    toggle = next(e for e in top_sheet(user).descendants() if isinstance(e, ui.toggle))
    assert toggle.visible and list(toggle.options) == ["CHF", "EUR"]
    assert field(user, ui.number, "Amount Received").value == 40
    put(user, toggle, "EUR")
    await settle()
    assert field(user, ui.number, "Amount Received").value == 45
    assert field(user, ui.select, "To Account").value == debts["eur"].id
    assert f"Settles 1 item: {label(D(TODAY.year, 3, 1))} · 45.00 EUR" in sheet_texts(user)
    click(user, sheet_button(user, "Record Repayment"))
    await settle()
    assert notified(user, "Recorded 45.00 EUR from Anna")
    assert shared_svc.person(ctx, "anna").open == {"CHF": 40}


async def test_settle_up_errors(user: User, ctx, debts) -> None:
    await user.open("/shared")
    with user:
        page.open_settle_sheet(ctx, "zoe")
    assert notified(user, "Nobody of that name owes anything")
    with user:
        page.open_settle_sheet(ctx, "carla")
    assert notified(user, "Carla owes nothing")
    with user:
        page.open_settle_sheet(ctx, "ben")
    tx_svc.delete(ctx, [debts["ben"].id])  # gone in the meantime
    click(user, sheet_button(user, "Record Repayment"))
    await settle()
    assert notified(user, "Nobody of that name owes anything")


# ------------------------------------------------------------------ the summary
async def test_the_summary_sheet(user: User, ctx, debts) -> None:
    await user.open("/shared")
    click(user, icon_buttons(user, "o_description")[0])  # Anna's
    await settle()
    assert "Summary for Anna" in sheet_texts(user)
    text = next(e for e in top_sheet(user).descendants() if isinstance(e, ui.textarea)).value
    assert text.startswith(
        f"Open items for Anna · {label(TODAY)}\n\n{label(D(TODAY.year, 1, 15))} · Restaurant X · 40.00 CHF of 60.00 CHF"
    )
    assert "Total open: 40.00 CHF\nTotal open: 45.00 EUR\n" in text
    click(user, sheet_button(user, "Copy"))
    await settle()
    assert notified(user, "Copied")
    click(user, sheet_button(user, "Save as PDF…"))
    pdf = await downloaded(user)
    assert pdf.content.startswith(b"%PDF")
    click(user, sheet_button(user, "Save as Text…"))
    txt = await downloaded(user, 2)
    assert txt.content.decode("utf-8") == text
    with user:
        page.open_summary_sheet(ctx, "zoe")
    assert notified(user, "Nobody of that name owes anything")
    await user.open("/shared")
    click(user, icon_buttons(user, "o_description")[-1])  # Carla's, in the settled list
    await settle()
    assert "Summary for Carla" in sheet_texts(user)
    assert "Nothing open." in next(e for e in top_sheet(user).descendants() if isinstance(e, ui.textarea)).value


async def test_a_change_elsewhere_refreshes_the_page(user: User, ctx, setup) -> None:
    await user.open("/shared")
    tx_svc.save(ctx, advance(setup, D(TODAY.year, 3, 5), 50, 50, "Ben"))
    with user:
        layout.data_changed()
    await user.should_see("1 person owes you")
