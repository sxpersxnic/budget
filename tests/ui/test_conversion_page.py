"""Converting an account with history into a securities account (F21, decision 13) in the interface: the account
sheet opens the conversion sheet, the opening holdings appear on the Investments page, and the pages elsewhere
treat the account by month."""

import asyncio
from datetime import timedelta

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_REGULAR, ASSET_CASH, ASSET_SECURITIES, TX_TRANSFER
from budget_app.services import accounts as acc_svc
from budget_app.services import investments as inv_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.investments import OpeningHolding
from budget_app.services.transactions import TransactionInput
from budget_app.ui.pages import investments as page
from budget_app.ui.pages import settings
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

CONVERSION = months.previous_month()  # a complete month, so the Budget page reconciles it
BEFORE = months.add_months(CONVERSION, -1)
EARLIER = months.add_months(CONVERSION, -2)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


def top_sheet(user: User) -> ui.dialog:
    with user:
        dialogs = [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]
    assert dialogs, "expected an open sheet"
    return max(dialogs, key=lambda d: d.id)


def fields(user: User, kind: type, label: str) -> list:
    found = [e for e in top_sheet(user).descendants() if isinstance(e, kind) and e.props.get("label") == label]
    assert found, f"no {kind.__name__} {label!r} in the sheet"
    return sorted(found, key=lambda e: e.id)


def sheet_button(user: User, text: str) -> ui.button:
    found = [e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.text == text]
    assert found, f"no button {text!r} in the sheet"
    return found[0]


def sheet_texts(user: User) -> str:
    return " | ".join(e.text for e in top_sheet(user).descendants() if isinstance(e, ui.label))


async def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()
    await settle()


async def put(user: User, element, value) -> None:
    with user:
        element.value = value
    await settle()


@pytest.fixture
def fund(ctx, setup):
    """A CHF fund with a transfer two months ago and balances recorded for the two months before the conversion."""
    bank, fund = setup["chf"], acc_svc.add_account(ctx, "Fund", "CHF")
    old = tx_svc.save(
        ctx,
        TransactionInput(
            date=months.first_day(EARLIER) + timedelta(days=4),
            type=TX_TRANSFER,
            amount=500,
            currency="CHF",
            from_account=bank.id,
            to_account=fund.id,
        ),
    )
    for m in (EARLIER, BEFORE):
        wealth_svc.upsert_entry(ctx, bank.id, m, 1500)
        wealth_svc.upsert_entry(ctx, fund.id, m, 10000)
    return {"bank": bank, "fund": fund, "old": old, **setup}


async def open_account_sheet(user: User, ctx, acc) -> None:
    await user.open("/settings")
    with user:
        settings._account_sheet(ctx, ACCOUNT_REGULAR, lambda: None, ctx.store.accounts.get(acc.id))
    await settle()


async def test_choosing_securities_for_an_account_with_history_opens_the_conversion(user: User, ctx, fund) -> None:
    acc = fund["fund"]
    await open_account_sheet(user, ctx, acc)
    [asset_class] = fields(user, ui.select, "Asset class")
    await put(user, asset_class, ASSET_SECURITIES)
    assert "Convert to Securities" in sheet_texts(user)
    assert "Fund has transactions or recorded balances" in sheet_texts(user)
    assert asset_class.value == ASSET_CASH  # until the conversion is saved
    texts = sheet_texts(user)
    assert f"Last recorded balance: 10'000.00 CHF at the end of {months.label(BEFORE)}." in texts
    convert = sheet_button(user, "Convert")
    assert not convert.enabled
    [month] = fields(user, ui.select, "Conversion month")
    assert month.value == months.current_month()
    await put(user, month, CONVERSION)
    # two holdings, a third row added and removed again
    await put(user, fields(user, ui.input, "Name")[0], "World ETF")
    await put(user, fields(user, ui.input, "ISIN or symbol")[0], "VWRL")
    await put(user, fields(user, ui.number, "Quantity")[0], 80)
    await put(user, fields(user, ui.number, "Price")[0], 100)
    await click(user, sheet_button(user, "Add Holding"))
    await put(user, fields(user, ui.input, "Name")[1], "world etf")
    await put(user, fields(user, ui.number, "Quantity")[1], 1)
    await put(user, fields(user, ui.number, "Price")[1], 1)
    assert "Holding 2: “world etf” is listed twice" in sheet_texts(user)
    await put(user, fields(user, ui.input, "Name")[1], "Gold")
    await put(user, fields(user, ui.number, "Quantity")[1], 10)
    await put(user, fields(user, ui.number, "Price")[1], 205)
    await click(user, sheet_button(user, "Add Holding"))
    removes = sorted(
        (e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_delete"),
        key=lambda e: e.id,
    )
    await click(user, removes[-1])
    assert len(fields(user, ui.input, "Name")) == 2
    texts = sheet_texts(user)
    assert "Opening value 10'050.00 CHF, a difference of +50.00 CHF to the last balance" in texts
    assert "Capital gains on investment" in texts
    await click(user, sheet_button(user, "Convert"))
    saved = ctx.store.accounts.get(acc.id)
    assert (saved.asset_class, saved.invest_from) == (ASSET_SECURITIES, CONVERSION)
    assert notified(user, f"Fund is valued from its holdings from {months.label(CONVERSION)} on")
    with user:
        assert not [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]  # both closed
    await open_account_sheet(user, ctx, acc)  # the conversion saved the class; the account sheet tells
    assert fields(user, ui.select, "Asset class")[0].value == ASSET_SECURITIES
    assert f"Valued from its holdings from {months.label(CONVERSION)} on" in sheet_texts(user)
    assert {h.name for h in inv_svc.holdings(ctx, acc.id)} == {"World ETF", "Gold"}
    rep = reports.month_report(ctx, CONVERSION, "CHF")
    row = next(r for r in rep.accounts if r.account.id == acc.id)
    assert (row.opening, row.amount, row.unexplained) == (10000, 10050, 0)


async def test_the_conversion_sheet_explains_what_is_in_the_way(user: User, ctx, fund) -> None:
    acc = fund["fund"]
    wealth_svc.upsert_entry(ctx, acc.id, CONVERSION, 10100)
    await user.open("/settings")
    with user:
        page.open_conversion_sheet(ctx, acc.id, ASSET_SECURITIES)
        page.open_conversion_sheet(ctx, "gone", ASSET_SECURITIES)  # nothing to convert
    assert not sheet_button(user, "Convert").enabled
    assert "Add at least one holding" not in sheet_texts(user)  # nothing typed yet: no complaint
    [month] = fields(user, ui.select, "Conversion month")
    await put(user, month, EARLIER)
    assert "1 transaction on this account is dated in" in sheet_texts(user)
    await put(user, month, CONVERSION)
    await put(user, fields(user, ui.input, "Name")[0], "World ETF")
    await put(user, fields(user, ui.number, "Quantity")[0], 1)
    await put(user, fields(user, ui.number, "Price")[0], 100)
    assert f"The balance recorded for {months.label(CONVERSION)} is replaced by the value of the holdings." in (
        sheet_texts(user)
    )
    removes = [
        e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_delete"
    ]
    await click(user, removes[0])  # the last row: an empty one takes its place
    assert fields(user, ui.input, "Name")[0].value == ""
    await put(user, fields(user, ui.input, "Name")[0], "World ETF")
    await put(user, fields(user, ui.number, "Quantity")[0], 1)
    await put(user, fields(user, ui.number, "Price")[0], 100)
    inv_svc.convert_account(ctx, acc.id, ASSET_SECURITIES, CONVERSION, [OpeningHolding("X", "", 1, 1)])  # meanwhile
    await click(user, sheet_button(user, "Convert"))
    assert user.notify.contains("valued from its holdings already")


async def test_the_opening_holding_on_the_investments_page(user: User, ctx, fund) -> None:
    acc = fund["fund"]
    inv_svc.convert_account(ctx, acc.id, ASSET_SECURITIES, CONVERSION, [OpeningHolding("World ETF", "VWRL", 80, 125)])
    await user.open("/investments")
    await user.should_see("10'000.00 CHF")
    rows = [e for e in user.find(kind=ui.element).elements if "cursor-pointer" in e.classes]
    await click(user, rows[0])
    texts = sheet_texts(user)
    assert "Opening holding 80 × 125.00" in texts and "Opening value" in texts
    edit = [e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_edit"]
    await click(user, edit[0])
    assert "Edit Opening Holding" in sheet_texts(user)
    assert not fields(user, ui.select, "Holding")[0].enabled and not fields(user, ui.input, "Date")[0].enabled
    assert not fields(user, ui.select, "Cash account")[0].visible
    await put(user, fields(user, ui.number, "Quantity")[0], 81)
    assert "Worth 10'125.00 CHF on the day of the conversion" in sheet_texts(user)
    await click(user, sheet_button(user, "Save Changes"))
    [row] = [tx for tx in ctx.store.transactions.all() if tx.invest_id == acc.id]
    assert row.amount == 125 and row.to_account == acc.id
    # the conversion's revaluation opens an explanation in the transaction editor
    with user:
        open_transaction_sheet(ctx, editing=row)
    assert user.notify.contains("the difference between its last recorded balance and the value of its opening")


async def test_months_before_the_conversion_stay_ordinary(user: User, ctx, fund) -> None:
    acc = fund["fund"]
    inv_svc.convert_account(ctx, acc.id, ASSET_SECURITIES, CONVERSION, [OpeningHolding("World ETF", "", 80, 125)])
    # an old transaction keeps its account in the editor
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=fund["old"])
    to = [e for e in top_sheet(user).descendants() if isinstance(e, ui.select) and e.props.get("label") == "To"]
    assert to and acc.id in to[0].options and to[0].value == acc.id
    # the Budget page: recorded before the conversion, valued from holdings from it on
    await user.open("/budget")
    cm = months.current_month()
    [stepper] = [s for s in user.find(kind=ui.select).elements if cm in (s.options or {}) and not s.props.get("label")]
    await put(user, stepper, CONVERSION)
    await user.should_see("Valued from holdings")
    await put(user, stepper, BEFORE)
    await user.should_not_see("Valued from holdings")
    # converting back is refused while holdings exist, and says why
    await open_account_sheet(user, ctx, acc)
    await put(user, fields(user, ui.select, "Asset class")[0], ASSET_CASH)
    await click(user, sheet_button(user, "Save"))
    assert user.notify.contains("still has holdings, with their trades and prices")
