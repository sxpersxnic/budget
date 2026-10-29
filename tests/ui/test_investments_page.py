"""The Investments page (F21): holdings, the trade, price, paste and holding sheets, a holding's history, and the
places elsewhere that know about securities accounts (Settings, Overview, Budget, the transaction editor)."""

import asyncio
from datetime import date, timedelta

import pytest
from nicegui import ui
from nicegui.element_filter import ElementFilter
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app.domain import months
from budget_app.domain.models import ASSET_PILLAR3A, ASSET_SECURITIES, TRADE_BUY, TX_INCOME
from budget_app.services import accounts as acc_svc
from budget_app.services import investments as inv_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.investments import TradeInput
from budget_app.ui.pages import investments as page
from budget_app.ui.widgets.bulk_edit import open_bulk_edit_sheet
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

TODAY = date.today()
LAST_MONTH = months.last_day(months.previous_month())  # a buy dated there is in a complete month
EARLIER = LAST_MONTH - timedelta(days=10)


# ------------------------------------------------------------------ helpers
async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


def top_sheet(user: User) -> ui.dialog:
    with user:
        dialogs = [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]
    assert dialogs, "expected an open sheet"
    return max(dialogs, key=lambda d: d.id)


def field(user: User, kind: type, label: str):
    found = [e for e in top_sheet(user).descendants() if isinstance(e, kind) and e.props.get("label") == label]
    assert found, f"no {kind.__name__} {label!r} in the sheet"
    return found[0]


def sheet_button(user: User, text: str) -> ui.button:
    found = [e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.text == text]
    assert found, f"no button {text!r} in the sheet"
    return found[0]


def sheet_texts(user: User) -> str:
    return " | ".join(e.text for e in top_sheet(user).descendants() if isinstance(e, ui.label))


def page_button(user: User, text: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.text == text]
    assert found, f"no button {text!r}"
    return max(found, key=lambda b: b.id)


async def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()
    await settle()


async def put(user: User, element, value) -> None:
    with user:
        element.value = value
    await settle()


@pytest.fixture
def depot(ctx, setup):
    acc = acc_svc.add_account(ctx, "Depot", "CHF", asset_class=ASSET_SECURITIES)
    nestle = inv_svc.add_holding(ctx, acc.id, "Nestlé", "NESN")
    return {"acc": acc, "nestle": nestle, "bank": setup["chf"], **setup}


@pytest.fixture
def bought(ctx, depot):
    trade = inv_svc.save_trade(
        ctx, TradeInput(TRADE_BUY, depot["nestle"].id, EARLIER, 10, 95.5, 9.9, cash_account=depot["bank"].id)
    )
    return {**depot, "trade": trade}


# ------------------------------------------------------------------ the page
async def test_without_investment_accounts(user: User, ctx, setup) -> None:
    await user.open("/investments")
    await user.should_see("Give an account the asset class Securities or Crypto in Settings")
    assert not [b for b in user.find(kind=ui.button).elements if b.text == "Buy"]
    await click(user, page_button(user, "Settings…"))
    await user.should_see("Base Currency", retries=20)


async def test_add_a_holding(user: User, ctx, depot) -> None:
    inv_svc.delete_holding(ctx, depot["nestle"].id)
    await user.open("/investments")
    await user.should_see("Depot · CHF · Securities")
    await user.should_not_see("Paste Prices…")  # nothing to trade yet
    await click(user, page_button(user, "Add Holding…"))
    await put(user, field(user, ui.input, "Name"), "Roche")
    await put(user, field(user, ui.input, "ISIN or symbol"), "CH0012032048")
    await click(user, sheet_button(user, "Add"))
    [roche] = inv_svc.holdings(ctx)
    assert (roche.name, roche.symbol, roche.account_id) == ("Roche", "CH0012032048", depot["acc"].id)
    assert notified(user, "Added Roche")
    await user.should_see("No price yet")
    await user.should_see("Paste Prices…")


async def test_holdings_show_value_and_gain(user: User, ctx, bought) -> None:
    inv_svc.save_price(ctx, bought["nestle"].id, LAST_MONTH, 100)
    await user.open("/investments")
    await user.should_see("Nestlé")
    await user.should_see("1'000.00 CHF")  # 10 × 100
    await user.should_see("+35.10 CHF · +3.68 %")  # 1000 − 955 − 9.90 fee
    await user.should_see("Money put in")
    await user.should_see("Gain = value − money put in")


async def test_buy_in_the_sheet(user: User, ctx, depot) -> None:
    await user.open("/investments")
    await click(user, page_button(user, "Buy"))
    save = sheet_button(user, "Save Trade")
    assert not save.enabled
    assert field(user, ui.select, "Holding").value == depot["nestle"].id  # the only holding
    assert field(user, ui.select, "Cash account").value == depot["bank"].id  # the only CHF cash account
    await put(user, field(user, ui.number, "Quantity"), 12)
    await put(user, field(user, ui.number, "Price"), 50.25)
    await put(user, field(user, ui.number, "Fee"), 4.5)
    assert "Moves 603.00 CHF from Bank to Depot" in sheet_texts(user)
    assert "the fee of 4.50 CHF is spending from Bank" in sheet_texts(user)
    await click(user, save)
    [trade] = ctx.store.trades.all()
    assert (trade.quantity, trade.price, trade.fee, trade.date) == (12, 50.25, 4.5, TODAY)
    assert notified(user, "Trade saved")
    await user.should_see("603.00 CHF")


async def test_selling_more_than_held_is_explained(user: User, ctx, bought) -> None:
    await user.open("/investments")
    await click(user, page_button(user, "Sell"))
    await put(user, field(user, ui.number, "Quantity"), 11)
    await put(user, field(user, ui.number, "Price"), 99)
    assert "Only 10 held on" in sheet_texts(user)
    assert not sheet_button(user, "Save Trade").enabled
    await put(user, field(user, ui.number, "Quantity"), 4)
    await click(user, sheet_button(user, "Save Trade"))
    assert inv_svc.performance(ctx, bought["nestle"].id).quantity == 6


async def test_a_dividend_in_the_sheet(user: User, ctx, bought) -> None:
    await user.open("/investments")
    await user.should_not_see("Reclaimable withholding tax")
    await click(user, page_button(user, "Dividend"))
    gross, tax = field(user, ui.number, "Gross dividend"), field(user, ui.number, "Withholding tax")
    assert gross.visible and tax.visible
    await put(user, gross, 28.6)
    assert tax.value == 10.01  # 35 % Swiss withholding tax in a CHF account
    assert "Net 18.59 CHF to Bank · income 28.60 CHF in Dividends, 10.01 CHF under Taxes" in sheet_texts(user)
    await put(user, gross, 100)
    assert tax.value == 35  # follows the gross amount until typed in
    await put(user, tax, 30)
    await put(user, gross, 120)
    assert tax.value == 30
    assert "Net 90.00 CHF to Bank" in sheet_texts(user)
    await put(user, tax, 130)
    assert "can’t be more than the dividend" in sheet_texts(user)
    await put(user, tax, 30)
    await click(user, sheet_button(user, "Save Trade"))
    [income] = [tx for tx in ctx.store.transactions.all() if tx.type == TX_INCOME]
    assert income.amount == 120 and ctx.store.income_categories.get(income.category).name == "Dividends"
    [withheld] = [tx for tx in ctx.store.transactions.all() if tx.category == "TAX"]
    assert withheld.amount == 30 and withheld.invest_id == income.invest_id
    await user.should_see(f"Reclaimable withholding tax {TODAY.year}")
    await user.should_see("30.00 CHF")
    await user.should_see("Claim it back with your tax return")
    # the holding sheet: net dividends and the tax apart, and the tax in the dividend's history line
    rows = [e for e in user.find(kind=ui.element).elements if "cursor-pointer" in e.classes]
    await click(user, rows[0])
    texts = sheet_texts(user)
    for text in (
        "Net dividends",
        "90.00 CHF",
        "Withholding tax",
        "withholding tax 30.00 CHF, reclaimable",
        "net 90.00",
    ):
        assert text in texts, text
    edit = [e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_edit"]
    await click(user, edit[0])  # newest first: the dividend
    assert (field(user, ui.number, "Gross dividend").value, field(user, ui.number, "Withholding tax").value) == (
        120,
        30,
    )
    await put(user, field(user, ui.number, "Gross dividend"), 110)
    assert field(user, ui.number, "Withholding tax").value == 30  # an edit keeps the tax as it is
    await put(user, field(user, ui.number, "Withholding tax"), 0)
    assert "Net 110.00 CHF to Bank · income in Dividends" in sheet_texts(user)
    await click(user, sheet_button(user, "Save Changes"))
    assert not [tx for tx in ctx.store.transactions.all() if tx.category == "TAX"]


async def test_a_foreign_dividend_has_no_tax_prefilled(user: User, ctx, setup) -> None:
    usd = acc_svc.add_account(ctx, "USD cash", "USD")
    broker = acc_svc.add_account(ctx, "Broker", "USD", asset_class=ASSET_SECURITIES)
    apple = inv_svc.add_holding(ctx, broker.id, "Apple")
    inv_svc.save_trade(ctx, TradeInput(TRADE_BUY, apple.id, EARLIER, 2, 200, cash_account=usd.id))
    await user.open("/investments")
    await click(user, page_button(user, "Dividend"))
    await put(user, field(user, ui.number, "Gross dividend"), 10)
    assert field(user, ui.number, "Withholding tax").value == 0
    await put(user, field(user, ui.number, "Withholding tax"), 1.5)
    await click(user, sheet_button(user, "Save Trade"))
    await user.should_not_see("Reclaimable withholding tax")  # not Swiss tax
    with user:
        page.open_holding_sheet(ctx, apple.id)
    assert "foreign withholding tax 1.50 USD" in sheet_texts(user)


async def test_update_a_price(user: User, ctx, bought) -> None:
    await user.open("/investments")
    await click(user, page_button(user, "Update Price"))
    await put(user, field(user, ui.number, "Price"), 97)
    assert "10 held: worth 970.00 CHF, a change of +15.00 CHF." in sheet_texts(user)
    await click(user, sheet_button(user, "Save Price"))
    assert notified(user, "Price saved")
    assert inv_svc.account_value(ctx, bought["acc"].id) == 970
    await click(user, page_button(user, "Update Price"))
    await put(user, field(user, ui.input, "Date"), (EARLIER - timedelta(days=1)).isoformat())
    await put(user, field(user, ui.number, "Price"), 90)
    assert "Nothing is held on that day" in sheet_texts(user)
    await put(user, field(user, ui.input, "Date"), TODAY.isoformat())
    assert "Replaces the price of 97.00 recorded for that day." in sheet_texts(user)
    await put(user, field(user, ui.number, "Price"), None)
    assert "Enter the price" in sheet_texts(user)


async def test_paste_a_price_list(user: User, ctx, bought) -> None:
    await user.open("/investments")
    await click(user, page_button(user, "Paste Prices…"))
    assert not sheet_button(user, "Save Prices").enabled
    day = LAST_MONTH.strftime("%d.%m.%Y")
    text = f"Datum\tTitel\tKurs\n{day}\tNESN\t101,20\n{day}\tUnknown\t3\n{TODAY:%d.%m.%Y}\tNestlé\t102"
    await put(user, field(user, ui.textarea, None), text)
    texts = sheet_texts(user)
    assert "Line 3: No holding “Unknown”" in texts and "1 line is left out" in texts
    assert "new" in texts
    await click(user, sheet_button(user, "Save 2 Prices"))
    assert notified(user, "Saved 2 prices")
    assert inv_svc.account_value(ctx, bought["acc"].id) == 1020


async def test_the_holding_history(user: User, ctx, bought) -> None:
    price = inv_svc.save_price(ctx, bought["nestle"].id, LAST_MONTH, 100)
    await user.open("/investments")
    rows = [e for e in user.find(kind=ui.element).elements if "cursor-pointer" in e.classes]
    await click(user, rows[0])
    texts = sheet_texts(user)
    for text in ("History", "Price 100.00 CHF", "Buy 10 × 95.50", "fee 9.90 CHF", "Realized", "Unrealized"):
        assert text in texts, text
    # edit the price from the history
    edit = [e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_edit"]
    await click(user, edit[0])  # newest first: the price
    await put(user, field(user, ui.number, "Price"), 110)
    await click(user, sheet_button(user, "Save Price"))
    assert ctx.store.prices.get(price.id).price == 110
    assert "Price 110.00 CHF" in sheet_texts(user)  # the history sheet followed
    # delete the price
    delete = [
        e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_delete"
    ]
    await click(user, delete[0])
    await click(user, sheet_button(user, "Delete"))
    assert ctx.store.prices.all() == []
    assert notified(user, "Price deleted")
    # rename, then delete the holding with its trade
    await click(user, sheet_button(user, "Edit Holding…"))
    await put(user, field(user, ui.input, "Name"), "Nestlé N")
    assert not field(user, ui.select, "Account").enabled  # it has a trade
    await click(user, sheet_button(user, "Save"))
    assert ctx.store.holdings.get(bought["nestle"].id).name == "Nestlé N"
    await click(user, sheet_button(user, "Delete Holding…"))
    assert "Its 1 trade or price and the transactions it booked are deleted too." in sheet_texts(user)
    await click(user, sheet_button(user, "Delete"))
    assert ctx.store.holdings.all() == [] and ctx.store.trades.all() == []
    assert not [tx for tx in ctx.store.transactions.all() if tx.invest_id]


async def test_trades_from_the_history(user: User, ctx, bought) -> None:
    sale = inv_svc.save_trade(
        ctx, TradeInput("sell", bought["nestle"].id, LAST_MONTH, 4, 99, cash_account=bought["bank"].id)
    )
    await user.open("/investments")
    rows = [e for e in user.find(kind=ui.element).elements if "cursor-pointer" in e.classes]
    await click(user, rows[0])
    await click(user, sheet_button(user, "Update Price"))
    await put(user, field(user, ui.number, "Price"), 98)
    await click(user, sheet_button(user, "Cancel"))
    buttons = [e for e in top_sheet(user).descendants() if isinstance(e, ui.button)]
    deletes = [b for b in buttons if b.props.get("icon") == "o_delete"]
    await click(user, deletes[-1])  # oldest: the buy, which the sale needs
    await click(user, sheet_button(user, "Delete"))
    assert user.notify.contains("would sell more than is held")
    edits = [b for b in buttons if b.props.get("icon") == "o_edit"]
    await click(user, edits[0])  # the sale
    assert "Edit Trade" in sheet_texts(user)
    await click(user, sheet_button(user, "Delete"))
    await click(user, sheet_button(user, "Delete"))
    assert ctx.store.trades.get(sale.id) is None
    assert notified(user, "Trade deleted")


# ------------------------------------------------------------------ elsewhere
async def test_a_booked_row_opens_its_trade_or_price(user: User, ctx, bought) -> None:
    price = inv_svc.save_price(ctx, bought["nestle"].id, LAST_MONTH, 100)
    [move] = [tx for tx in ctx.store.transactions.all() if tx.invest_id == bought["trade"].id and tx.type == "transfer"]
    [reval] = [tx for tx in ctx.store.transactions.all() if tx.invest_id == price.id]
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=move)
    assert "Edit Trade" in sheet_texts(user)
    await put(user, field(user, ui.number, "Fee"), 0)
    await click(user, sheet_button(user, "Save Changes"))
    assert ctx.store.trades.get(bought["trade"].id).fee == 0
    with user:
        open_transaction_sheet(ctx, editing=reval)
    assert "Edit Price" in sheet_texts(user)
    await click(user, sheet_button(user, "Delete"))
    await click(user, sheet_button(user, "Delete"))
    assert ctx.store.prices.all() == []
    with user:
        open_transaction_sheet(ctx, editing=reval)  # its price is gone
    assert user.notify.contains("its trade or price no longer exists")


async def test_bulk_edit_leaves_booked_rows(user: User, ctx, bought) -> None:
    ids = [tx.id for tx in ctx.store.transactions.all() if tx.invest_id]
    await user.open("/transactions")
    with user:
        open_bulk_edit_sheet(ctx, ids)
    assert "2 transactions booked on the Investments page stay as they are" in sheet_texts(user)


async def test_the_accounts_page_sets_the_asset_class(user: User, ctx, setup) -> None:
    await user.open("/accounts")
    await click(user, page_button(user, "Add Account…"))
    assert "Valued from the balances you record" in sheet_texts(user)
    await put(user, field(user, ui.input, "Name"), "Broker")
    await put(user, field(user, ui.select, "Asset class"), ASSET_SECURITIES)
    assert "Valued from its holdings on the Investments page" in sheet_texts(user)
    await click(user, sheet_button(user, "Add"))
    acc = next(a for a in ctx.store.accounts.all() if a.name == "Broker")
    assert acc.asset_class == ASSET_SECURITIES
    await user.should_see("Securities · 0.00 CHF in holdings")
    acc_svc.edit_account(ctx, setup["eur"].id, "Euro account", "EUR", ASSET_PILLAR3A)
    await user.open("/accounts")
    await user.should_see("Pillar 3a · No balance recorded")


async def test_the_overview_shows_wealth_by_asset_class(user: User, ctx, bought) -> None:
    wealth_svc.upsert_entry(ctx, bought["bank"].id, months.previous_month(), 4000)
    await user.open("/")
    await user.should_see("By Asset Class")
    await user.should_see("Securities")
    await user.should_see("4'000.00 CHF · 81 %")
    await user.should_see("955.00 CHF · 19 %")
    await click(user, page_button(user, "Add Chart"))
    with user:
        items = [
            i
            for i in ElementFilter(kind=ui.menu_item, local_scope=False)
            if any(getattr(e, "text", None) == "Wealth by Asset Class" for e in i.descendants())
        ]
    await click(user, items[0])
    await user.should_see("Month-end balances by asset class")


async def test_the_budget_page_values_a_depot_from_its_holdings(user: User, ctx, bought) -> None:
    await user.open("/budget")
    cm = months.current_month()
    [stepper] = [s for s in user.find(kind=ui.select).elements if cm in (s.options or {}) and not s.props.get("label")]
    await put(user, stepper, months.previous_month())
    await user.should_see("Valued from holdings")


async def test_a_trade_needs_a_holding(user: User, ctx, depot) -> None:
    inv_svc.delete_holding(ctx, depot["nestle"].id)
    await user.open("/investments")
    with user:
        page.open_trade_sheet(ctx)
        page.open_price_sheet(ctx)
        page.open_paste_sheet(ctx)
    assert user.notify.contains("Add a holding first.")
    acc_svc.edit_account(ctx, depot["acc"].id, "Depot", "CHF", "cash")
    with user:
        page.open_holding_form(ctx)
        page.open_holding_sheet(ctx, "gone")  # nothing to show
    assert user.notify.contains("Give an account the asset class Securities or Crypto in Settings first.")


async def test_sheets_report_what_changed_behind_their_back(user: User, ctx, bought) -> None:
    await user.open("/investments")
    await click(user, page_button(user, "Add Holding…"))
    await put(user, field(user, ui.input, "Name"), "nestlé")
    await click(user, sheet_button(user, "Add"))
    assert user.notify.contains("This account already has a holding “Nestlé”")
    await click(user, sheet_button(user, "Cancel"))
    # a trade, a price and a price list whose holding goes while the sheet is open
    other = inv_svc.add_holding(ctx, bought["acc"].id, "Roche")
    await user.open("/investments")
    with user:
        page.open_trade_sheet(ctx, holding_id=other.id)
    await put(user, field(user, ui.number, "Quantity"), 1)
    await put(user, field(user, ui.number, "Price"), 250)
    inv_svc.delete_holding(ctx, other.id)
    await click(user, sheet_button(user, "Save Trade"))
    assert user.notify.contains("Select a holding")
    with user:
        page.open_price_sheet(ctx, holding_id=bought["nestle"].id)
    await put(user, field(user, ui.number, "Price"), 99)
    price = inv_svc.save_price(ctx, bought["nestle"].id, TODAY, 98)
    inv_svc.delete_holding(ctx, bought["nestle"].id)
    await click(user, sheet_button(user, "Save Price"))
    assert user.notify.contains("Select a holding")
    assert ctx.store.prices.get(price.id) is None


async def test_deletes_that_fail(user: User, ctx, bought) -> None:
    inv_svc.save_trade(ctx, TradeInput("sell", bought["nestle"].id, LAST_MONTH, 4, 99, cash_account=bought["bank"].id))
    price = inv_svc.save_price(ctx, bought["nestle"].id, LAST_MONTH, 100)
    await user.open("/investments")
    with user:
        page.open_trade_sheet(ctx, editing=bought["trade"])
    await click(user, sheet_button(user, "Delete"))
    await click(user, sheet_button(user, "Delete"))
    assert user.notify.contains("would sell more than is held")
    with user:
        page.open_price_sheet(ctx, editing=price)
    inv_svc.delete_price(ctx, price.id)
    await click(user, sheet_button(user, "Delete"))
    await click(user, sheet_button(user, "Delete"))
    assert user.notify.contains("Price not found")


async def test_a_pasted_list_whose_holding_goes(user: User, ctx, bought) -> None:
    await user.open("/investments")
    await click(user, page_button(user, "Paste Prices…"))
    await put(user, field(user, ui.textarea, None), f"{TODAY:%Y-%m-%d};NESN;100")
    inv_svc.delete_holding(ctx, bought["nestle"].id)
    await click(user, sheet_button(user, "Save 1 Price"))
    assert user.notify.contains("Select a holding")


async def test_two_holdings_a_dividend_and_a_sale_in_the_history(user: User, ctx, bought) -> None:
    etf = inv_svc.add_holding(ctx, bought["acc"].id, "World ETF")
    cash = bought["bank"].id
    inv_svc.save_trade(ctx, TradeInput(TRADE_BUY, etf.id, EARLIER, 2, 100, cash_account=cash))
    inv_svc.save_trade(ctx, TradeInput("dividend", bought["nestle"].id, LAST_MONTH, amount=12.5, cash_account=cash))
    sale = inv_svc.save_trade(
        ctx, TradeInput("sell", bought["nestle"].id, LAST_MONTH, 1, 96, cash_account=cash, comment="Rebalance")
    )
    fresh = inv_svc.add_holding(ctx, bought["acc"].id, "Zurich")
    await user.open("/investments")
    await user.should_see("Total")
    await user.should_see("1'064.00 CHF")  # 9 × 96 (the sale's price is the latest) + 2 × 100
    rows = sorted((e for e in user.find(kind=ui.element).elements if "cursor-pointer" in e.classes), key=lambda e: e.id)
    await click(user, rows[0])  # Nestlé
    texts = sheet_texts(user)
    assert "Dividend 12.50 CHF" in texts and "Rebalance" in texts
    deletes = [
        e for e in top_sheet(user).descendants() if isinstance(e, ui.button) and e.props.get("icon") == "o_delete"
    ]
    await click(user, deletes[0])  # newest first: the sale (the dividend is on the same day, entered before it)
    await click(user, sheet_button(user, "Delete"))
    assert ctx.store.trades.get(sale.id) is None
    assert notified(user, "Trade deleted")
    await click(user, sheet_button(user, "Close"))
    with user:
        page.open_holding_sheet(ctx, fresh.id)
    assert "No trades or prices yet." in sheet_texts(user)


async def test_a_currency_without_rates_is_reported(user: User, ctx, setup) -> None:
    acc = acc_svc.add_account(ctx, "Pounds", "GBP", asset_class=ASSET_SECURITIES)
    inv_svc.add_holding(ctx, acc.id, "BP")
    await user.open("/investments")
    await user.should_see("BP")
    assert user.notify.contains("GBP")
