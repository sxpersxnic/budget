"""Investments (F21): holdings, trades and prices, the transactions they book, valuation and performance."""

import random
from datetime import date

import pytest

from budget_app.domain import investments as inv
from budget_app.domain import months
from budget_app.domain.coicop import TAX
from budget_app.domain.models import (
    ASSET_CRYPTO,
    ASSET_PILLAR3A,
    ASSET_SECURITIES,
    REVAL_INVEST,
    TRADE_BUY,
    TRADE_DIVIDEND,
    TRADE_SELL,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    TX_TRANSFER,
    Price,
    Trade,
)
from budget_app.services import accounts as acc_svc
from budget_app.services import bulk as bulk_svc
from budget_app.services import investments as inv_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import ValidationError
from budget_app.services.investments import TradeInput
from budget_app.services.transactions import TransactionInput

D = date


@pytest.fixture
def depot(ctx, setup):
    """A CHF securities account with two holdings, paid from the CHF bank account."""
    acc = acc_svc.add_account(ctx, "Depot", "CHF", asset_class=ASSET_SECURITIES)
    nestle = inv_svc.add_holding(ctx, acc.id, "Nestlé", "CH0038863350")
    etf = inv_svc.add_holding(ctx, acc.id, "World ETF", "VWRL")
    return {"acc": acc, "nestle": nestle, "etf": etf, "bank": setup["chf"], **setup}


def buy(ctx, depot, day, quantity, price, fee=0.0, holding="nestle", kind=TRADE_BUY, **kw):
    inp = TradeInput(
        kind=kind,
        holding_id=depot[holding].id,
        date=day,
        quantity=quantity,
        price=price,
        fee=fee,
        cash_account=depot["bank"].id,
        **kw,
    )
    return inv_svc.save_trade(ctx, inp)


def sell(ctx, depot, day, quantity, price, fee=0.0, holding="nestle"):
    return buy(ctx, depot, day, quantity, price, fee, holding, kind=TRADE_SELL)


def linked(ctx, source_id: str) -> dict[str, object]:
    return {tx.type: tx for tx in ctx.store.transactions.all() if tx.invest_id == source_id}


def booked(ctx, account_id: str, until: date | None = None) -> float:
    """What the transactions on an account add up to (in its currency), up to the end of `until`."""
    total = 0.0
    for tx in ctx.store.transactions.all():
        if until is not None and tx.date > until:
            continue
        if tx.to_account == account_id:
            total += tx.amount
        if tx.from_account == account_id:
            total -= tx.amount
    return round(total, 2)


# ------------------------------------------------------------------ domain
def test_replay_keeps_the_booked_value_at_quantity_times_price():
    trades = [
        Trade("h", D(2026, 1, 5), TRADE_BUY, 3, 33.333333, seq=1, id="b1"),
        Trade("h", D(2026, 2, 5), TRADE_SELL, 1, 35.555555, seq=2, id="s1"),
        Trade("h", D(2026, 2, 5), TRADE_BUY, 0.333333, 36.1, seq=3, id="b2"),
    ]
    prices = [Price("h", D(2026, 1, 31), 34.444444, id="p1"), Price("h", D(2026, 2, 5), 37.0, id="p2")]
    steps = inv.replay(inv.events(trades, prices))
    assert [s.event.id for s in steps] == ["b1", "p1", "s1", "b2", "p2"]  # a day's quoted price after its trades
    running = 0.0
    for s in steps:
        running = round(running + s.moved + s.revaluation, 2)
        assert running == s.value == round(s.held_after * s.price, 2)
    assert steps[0].revaluation == 0  # nothing was held before the first buy
    assert inv.value_on(steps, D(2026, 1, 31)) == round(3 * 34.444444, 2)
    assert inv.value_on(steps, D(2025, 12, 31)) == 0.0
    assert inv.price_date(steps, D(2026, 2, 4)) == D(2026, 1, 31)


def test_performance_splits_into_realized_and_unrealized():
    trades = [
        Trade("h", D(2026, 1, 5), TRADE_BUY, 10, 100, fee=10, seq=1, id="b1"),
        Trade("h", D(2026, 2, 5), TRADE_BUY, 10, 120, fee=10, seq=2, id="b2"),
        Trade("h", D(2026, 3, 5), TRADE_SELL, 5, 130, fee=5, seq=3, id="s1"),
        Trade("h", D(2026, 3, 20), TRADE_DIVIDEND, amount=40, seq=4, id="d1"),
    ]
    prices = [Price("h", D(2026, 4, 1), 125, id="p1")]
    perf = inv.performance(inv.replay(inv.events(trades, prices)))
    assert perf.quantity == 15 and perf.price == 125 and perf.price_date == D(2026, 4, 1)
    assert perf.value == 1875 and perf.bought == 2200 and perf.sold == 650
    assert perf.invested == 1550 and perf.dividends == 40 and perf.fees == 25
    assert perf.gain == pytest.approx(1875 - 1550 + 40 - 25)
    assert perf.pct == pytest.approx(perf.gain / 1550 * 100)
    assert perf.realized == pytest.approx(650 - 550)  # average cost 110 a unit
    assert perf.unrealized == pytest.approx(1875 - 1650)
    assert perf.realized + perf.unrealized == pytest.approx(perf.value - perf.invested)
    early = inv.performance(inv.replay(inv.events(trades, prices)), on=D(2026, 1, 31))
    assert early.quantity == 10 and early.value == 1000 and early.trades == 1
    total = perf + perf.scaled(2)
    assert total.value == pytest.approx(3 * 1875) and total.quantity == 0


def test_a_holding_sold_completely_has_no_percentage():
    trades = [
        Trade("h", D(2026, 1, 5), TRADE_BUY, 2, 100, seq=1, id="b"),
        Trade("h", D(2026, 2, 5), TRADE_SELL, 2, 150, seq=2, id="s"),
    ]
    perf = inv.performance(inv.replay(inv.events(trades, [])))
    assert perf.value == 0 and perf.invested == -100 and perf.gain == 100 and perf.pct is None
    assert perf.realized == 100 and perf.cost == 0


def test_oversold_finds_the_first_sale_beyond_what_is_held():
    trades = [
        Trade("h", D(2026, 1, 5), TRADE_BUY, 2, 10, seq=1, id="b"),
        Trade("h", D(2026, 1, 6), TRADE_SELL, 3, 10, seq=2, id="s"),
    ]
    bad = inv.oversold(inv.replay(inv.events(trades, [])))
    assert bad is not None and bad.event.id == "s" and bad.held_before == 2


def test_quantity_and_price_formats():
    assert inv.fmt_quantity(12) == "12"
    assert inv.fmt_quantity(1250.125) == "1'250.125"
    assert inv.fmt_quantity(0.000125) == "0.000125"
    assert inv.fmt_price(95.5) == "95.50"
    assert inv.fmt_price(0.123456) == "0.123456"
    assert inv.fmt_price(1234) == "1'234.00"


# ------------------------------------------------------------------ booking
def test_a_buy_books_a_transfer_and_a_fee(ctx, depot):
    trade = buy(ctx, depot, D(2026, 8, 10), 10, 95.5, fee=9.9)
    rows = linked(ctx, trade.id)
    assert set(rows) == {TX_TRANSFER, TX_EXPENDITURE}
    move, fee = rows[TX_TRANSFER], rows[TX_EXPENDITURE]
    assert (move.from_account, move.to_account, move.amount) == (depot["bank"].id, depot["acc"].id, 955.0)
    assert (fee.from_account, fee.amount, fee.category) == (depot["bank"].id, 9.9, inv_svc.FEE_CATEGORY)
    assert "Buy 10 Nestlé at 95.50" in move.comment
    assert inv_svc.account_value(ctx, depot["acc"].id) == 955.0
    assert booked(ctx, depot["acc"].id) == 955.0


def test_a_price_update_books_a_linked_revaluation_kept_in_step(ctx, depot):
    buy(ctx, depot, D(2026, 8, 10), 10, 95.5)
    price = inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 97.25)
    reval = linked(ctx, price.id)[TX_REVALUATION]
    assert (reval.to_account, reval.amount, reval.category) == (depot["acc"].id, 17.5, REVAL_INVEST)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 94.0, editing_id=price.id)
    again = linked(ctx, price.id)[TX_REVALUATION]
    assert again.id == reval.id and again.from_account == depot["acc"].id and again.amount == 15.0
    # the same day once more replaces the price rather than adding a second one
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 96.0)
    assert len(inv_svc.prices_of(ctx, depot["nestle"].id)) == 1
    assert booked(ctx, depot["acc"].id) == inv_svc.account_value(ctx, depot["acc"].id) == 960.0
    inv_svc.delete_price(ctx, price.id)
    assert linked(ctx, price.id) == {}
    assert booked(ctx, depot["acc"].id) == 955.0


def test_a_sale_between_two_prices(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 15), 110)
    sale = sell(ctx, depot, D(2026, 8, 20), 4, 105, fee=5)
    p2 = inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 120)
    rows = linked(ctx, sale.id)
    assert rows[TX_TRANSFER].from_account == depot["acc"].id and rows[TX_TRANSFER].amount == 420
    assert rows[TX_REVALUATION].from_account == depot["acc"].id and rows[TX_REVALUATION].amount == 50
    assert linked(ctx, p2.id)[TX_REVALUATION].amount == 90  # 6 × (120 − 105)
    assert booked(ctx, depot["acc"].id) == inv_svc.account_value(ctx, depot["acc"].id) == 720
    assert inv_svc.account_value(ctx, depot["acc"].id, D(2026, 8, 19)) == 1100


def test_back_dated_trades_and_prices_rebook_the_later_ones(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    late = inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 120)
    assert linked(ctx, late.id)[TX_REVALUATION].amount == 200
    early = inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 15), 90)  # back-dated: between the two
    assert linked(ctx, early.id)[TX_REVALUATION].from_account == depot["acc"].id
    assert linked(ctx, early.id)[TX_REVALUATION].amount == 100
    assert linked(ctx, late.id)[TX_REVALUATION].amount == 300  # now from 90 to 120
    extra = buy(ctx, depot, D(2026, 8, 10), 5, 95)  # back-dated buy before both prices
    assert linked(ctx, extra.id)[TX_REVALUATION].amount == 50  # 10 × (95 − 100)
    assert booked(ctx, depot["acc"].id) == inv_svc.account_value(ctx, depot["acc"].id) == 15 * 120


def test_random_histories_stay_exact_after_edits_and_deletes(ctx, depot):
    rng = random.Random(7)  # noqa: S311 - test data, the same on every run
    acc = depot["acc"].id
    days = [D(2026, 8, 1 + rng.randrange(60) % 31) if i % 2 else D(2026, 9, 1 + rng.randrange(30)) for i in range(40)]
    for i, day in enumerate(days):
        holding = rng.choice(["nestle", "etf"])
        price = round(rng.uniform(10, 200), rng.choice([2, 4, 6]))
        if i % 3 == 2:
            inv_svc.save_price(ctx, depot[holding].id, day, price)
            continue
        try:
            if rng.random() < 0.35:
                sell(ctx, depot, day, round(rng.uniform(0.1, 5), 6), price, holding=holding)
            else:
                buy(ctx, depot, day, round(rng.uniform(0.1, 8), 6), price, fee=rng.choice([0, 4.95]), holding=holding)
        except ValidationError:
            pass  # a sale of more than held is refused
    for _ in range(15):  # edits and deletes in any order
        trades = ctx.store.trades.all()
        trade = rng.choice(trades)
        try:
            if rng.random() < 0.5:
                inv_svc.delete_trade(ctx, trade.id)
            else:
                inp = inv_svc.to_input(trade)
                inp.date = D(2026, 8 + rng.randrange(2), 1 + rng.randrange(28))
                inp.price = round(rng.uniform(10, 200), 3)
                inv_svc.save_trade(ctx, inp, trade.id)
        except ValidationError:
            pass
        price = rng.choice(ctx.store.prices.all())
        inv_svc.save_price(ctx, price.holding_id, price.date, round(rng.uniform(10, 200), 5), editing_id=price.id)
    for h in inv_svc.holdings(ctx):
        assert inv.oversold(inv_svc.steps(ctx, h.id)) is None
    for day in (D(2026, 8, 15), D(2026, 8, 31), D(2026, 9, 15), D(2026, 9, 30), None):
        assert booked(ctx, acc, day) == inv_svc.account_value(ctx, acc, day), day
    every_source = {tr.id for tr in ctx.store.trades.all()} | {p.id for p in ctx.store.prices.all()}
    assert {tx.invest_id for tx in ctx.store.transactions.all() if tx.invest_id} <= every_source


def test_selling_more_than_held_is_refused(ctx, depot):
    first = buy(ctx, depot, D(2026, 8, 3), 10, 100)
    with pytest.raises(ValidationError, match="Only 10 held on"):
        sell(ctx, depot, D(2026, 8, 20), 11, 100)
    with pytest.raises(ValidationError, match="Only 0 held on"):
        sell(ctx, depot, D(2026, 8, 1), 1, 100)  # before the buy
    sale = sell(ctx, depot, D(2026, 8, 20), 8, 100)
    with pytest.raises(ValidationError, match=r"Then the sale on .* would sell more than is held"):
        inv_svc.delete_trade(ctx, first.id)
    smaller = inv_svc.to_input(first)
    smaller.quantity = 5
    with pytest.raises(ValidationError, match="would sell more than is held"):
        inv_svc.save_trade(ctx, smaller, first.id)
    moved = inv_svc.to_input(first)
    moved.holding_id = depot["etf"].id
    with pytest.raises(ValidationError, match="would sell more than is held"):
        inv_svc.save_trade(ctx, moved, first.id)
    inv_svc.delete_trade(ctx, sale.id)
    inv_svc.save_trade(ctx, moved, first.id)  # nothing left that needs it
    assert inv_svc.steps(ctx, depot["nestle"].id) == []
    assert inv_svc.account_value(ctx, depot["acc"].id) == 1000


def test_trade_validation(ctx, depot):
    eur_cash = depot["eur"]
    base = TradeInput(TRADE_BUY, depot["nestle"].id, D(2026, 8, 3), 1, 10, cash_account=depot["bank"].id)
    cases = [
        (TradeInput(**{**base.__dict__, "kind": "gift"}), "Select buy, sell or dividend"),
        (TradeInput(**{**base.__dict__, "holding_id": ""}), "Select a holding"),
        (TradeInput(**{**base.__dict__, "date": None}), "Date is required"),
        (TradeInput(**{**base.__dict__, "date": D(2099, 1, 1)}), "future"),
        (TradeInput(**{**base.__dict__, "quantity": 0}), "Enter the quantity"),
        (TradeInput(**{**base.__dict__, "price": None}), "Enter the price"),
        (TradeInput(**{**base.__dict__, "fee": -1}), "The fee can’t be negative"),
        (TradeInput(**{**base.__dict__, "cash_account": ""}), "Select the cash account"),
        (TradeInput(**{**base.__dict__, "cash_account": depot["acc"].id}), "Select the cash account"),
        (TradeInput(**{**base.__dict__, "cash_account": eur_cash.id}), "must be in CHF"),
        (TradeInput(**{**base.__dict__, "kind": TRADE_DIVIDEND, "amount": None}), "Enter the gross dividend"),
    ]
    for inp, message in cases:
        with pytest.raises(ValidationError, match=message):
            inv_svc.save_trade(ctx, inp)
    assert ctx.store.trades.all() == []
    with pytest.raises(ValidationError, match="Trade not found"):
        inv_svc.delete_trade(ctx, "nope")
    with pytest.raises(ValidationError, match="Price not found"):
        inv_svc.delete_price(ctx, "nope")
    with pytest.raises(ValidationError, match="Enter the price"):
        inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 3), 0)


def test_a_dividend_is_income_in_dividends(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    div = buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, amount=31.5, comment="2026")
    income = linked(ctx, div.id)[TX_INCOME]
    cat = ctx.store.income_categories.get(income.category)
    assert cat is not None and cat.name == "Dividends"
    assert (income.to_account, income.amount) == (depot["bank"].id, 31.5)
    assert income.comment == "Dividend Nestlé · 2026"
    assert div.quantity == 0 and div.price == 0
    again = buy(ctx, depot, D(2026, 9, 3), None, None, kind=TRADE_DIVIDEND, amount=5, holding="etf")
    assert linked(ctx, again.id)[TX_INCOME].category == cat.id  # one category for every dividend
    assert len(ctx.store.income_categories.all()) == 2  # Salary and Dividends
    assert inv_svc.performance(ctx, depot["nestle"].id).dividends == 31.5
    assert booked(ctx, depot["acc"].id) == 1000  # a dividend doesn't change the account's value


def test_a_dividend_books_the_gross_as_income_and_the_withholding_tax_as_taxes(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    div = buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, amount=100, tax=35)
    rows = linked(ctx, div.id)
    assert set(rows) == {TX_INCOME, TX_EXPENDITURE}  # one action, two linked rows
    income, tax = rows[TX_INCOME], rows[TX_EXPENDITURE]
    assert (income.amount, income.to_account) == (100, depot["bank"].id)
    assert (tax.amount, tax.category, tax.from_account) == (35, TAX, depot["bank"].id)
    assert tax.comment == "Withholding tax: Dividend Nestlé"
    assert booked(ctx, depot["bank"].id, D(2026, 9, 30)) - booked(ctx, depot["bank"].id, D(2026, 8, 31)) == 65
    perf = inv_svc.performance(ctx, depot["nestle"].id)
    assert (perf.dividends, perf.withholding) == (65, 35)  # the gain counts what was received
    assert perf.gain == pytest.approx(65)
    rep = reports.month_report(ctx, "2026-09", "CHF", today=D(2026, 10, 21))
    assert rep.total_income.actual == pytest.approx(100)
    assert rep.expenditure_tree[TAX].actual == pytest.approx(35)
    assert rep.total_income.actual - rep.total_expenditure.actual == pytest.approx(65)
    # reclaimable: the Swiss withholding tax on CHF dividends, per year
    buy(ctx, depot, D(2025, 12, 1), 1, 100, holding="etf")
    buy(ctx, depot, D(2025, 12, 20), None, None, kind=TRADE_DIVIDEND, amount=10, tax=3.5, holding="etf")
    buy(ctx, depot, D(2026, 10, 1), None, None, kind=TRADE_DIVIDEND, amount=20, tax=7, holding="etf")
    buy(ctx, depot, D(2026, 10, 2), None, None, kind=TRADE_DIVIDEND, amount=20, holding="etf")  # no tax
    usd = acc_svc.add_account(ctx, "USD cash", "USD")
    broker = acc_svc.add_account(ctx, "Broker", "USD", asset_class=ASSET_SECURITIES)
    apple = inv_svc.add_holding(ctx, broker.id, "Apple")
    inv_svc.save_trade(
        ctx, TradeInput(TRADE_DIVIDEND, apple.id, D(2026, 9, 5), amount=10, tax=1.5, cash_account=usd.id)
    )
    assert inv_svc.reclaimable_tax(ctx) == {2026: 42, 2025: 3.5}  # the foreign tax is not reclaimable here
    assert list(inv_svc.reclaimable_tax(ctx)) == [2026, 2025]
    assert inv.default_withholding(31.4, "CHF") == 10.99 and inv.default_withholding(31.4, "USD") == 0
    assert inv.default_withholding(None, "CHF") == 0
    # no tax: the expenditure row goes
    edited = inv_svc.to_input(div)
    assert (edited.amount, edited.tax) == (100, 35)
    edited.tax = None
    inv_svc.save_trade(ctx, edited, div.id)
    assert set(linked(ctx, div.id)) == {TX_INCOME}
    assert inv_svc.performance(ctx, depot["nestle"].id).dividends == 100


def test_withholding_tax_validation(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    with pytest.raises(ValidationError, match="Enter the gross dividend"):
        buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, tax=3)
    with pytest.raises(ValidationError, match="can’t be negative"):
        buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, amount=10, tax=-1)
    with pytest.raises(ValidationError, match="can’t be more than the dividend"):
        buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, amount=10, tax=10.01)
    whole = buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, amount=10, tax=10)
    assert linked(ctx, whole.id)[TX_EXPENDITURE].amount == 10
    sale = buy(ctx, depot, D(2026, 9, 3), 1, 100, kind=TRADE_SELL, tax=5)
    assert sale.tax == 0  # only dividends have a withholding tax
    assert inv_svc.to_input(sale).tax is None


def test_dividends_written_before_the_withholding_tax_load_as_gross_without_tax(tmp_path, rates):
    from budget_app.services.context import Context  # noqa: PLC0415
    from budget_app.storage.store import Store  # noqa: PLC0415

    (tmp_path / "accounts.csv").write_text(
        "name,currency,kind,created,asset_class,id\n"
        "Bank,CHF,regular,2026-01-01,cash,bank\nDepot,CHF,regular,2026-01-01,securities,depot\n",
        encoding="utf-8",
    )
    (tmp_path / "holdings.csv").write_text("account_id,name,symbol,id\ndepot,Nestlé,,h1\n", encoding="utf-8")
    (tmp_path / "trades.csv").write_text(
        "holding_id,date,kind,quantity,price,fee,amount,cash_account,comment,seq,id\n"
        "h1,2026-08-03,buy,10.0,100.0,0.0,0.0,bank,,1,t1\n"
        "h1,2026-09-02,dividend,0.0,0.0,0.0,19.5,bank,,2,t2\n",
        encoding="utf-8",
    )
    ctx = Context(store=Store(tmp_path), _fx=rates)
    old = ctx.store.trades.get("t2")
    assert old is not None and (old.amount, old.tax) == (19.5, 0)
    assert inv_svc.performance(ctx, "h1").dividends == 19.5 and inv_svc.reclaimable_tax(ctx) == {}
    edited = inv_svc.to_input(old)
    assert (edited.amount, edited.tax) == (19.5, 0)  # the stored amount is the gross, without tax
    edited.amount, edited.tax = 30, 10.5
    inv_svc.save_trade(ctx, edited, "t2")
    rows = linked(ctx, "t2")
    assert (rows[TX_INCOME].amount, rows[TX_EXPENDITURE].amount) == (30, 10.5)
    assert "tax" in (tmp_path / "trades.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert inv_svc.reclaimable_tax(ctx) == {2026: 10.5}


def test_a_renamed_dividends_category_is_kept(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    div = buy(ctx, depot, D(2026, 9, 2), None, None, kind=TRADE_DIVIDEND, amount=10)
    cat_id = linked(ctx, div.id)[TX_INCOME].category
    acc_svc.rename_income_category(ctx, cat_id, "Investment income")
    edited = inv_svc.to_input(div)
    edited.amount = 12
    inv_svc.save_trade(ctx, edited, div.id)
    assert linked(ctx, div.id)[TX_INCOME].category == cat_id


def test_saving_a_trade_is_one_undo_step(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 120)
    sale = sell(ctx, depot, D(2026, 8, 20), 4, 110, fee=5)
    action = undo_svc.latest(ctx)
    assert action is not None and undo_svc.label(ctx, action) == "Save a trade"
    undo_svc.undo(ctx, action.id)
    assert ctx.store.trades.get(sale.id) is None and linked(ctx, sale.id) == {}
    assert booked(ctx, depot["acc"].id) == inv_svc.account_value(ctx, depot["acc"].id) == 1200


def test_editing_a_trade_keeps_its_rows_and_their_valuation(ctx, depot):
    trade = buy(ctx, depot, D(2026, 8, 3), 10, 100, fee=5)
    before = linked(ctx, trade.id)
    edited = inv_svc.to_input(trade)
    edited.comment = "Savings plan"
    inv_svc.save_trade(ctx, edited, trade.id)
    after = linked(ctx, trade.id)
    assert after[TX_TRANSFER].id == before[TX_TRANSFER].id
    assert after[TX_TRANSFER].fx_date == before[TX_TRANSFER].fx_date
    assert after[TX_TRANSFER].comment.endswith("· Savings plan")
    edited.fee = 0
    inv_svc.save_trade(ctx, edited, trade.id)
    assert set(linked(ctx, trade.id)) == {TX_TRANSFER}  # no fee, no fee row
    edited.kind, edited.amount = TRADE_DIVIDEND, 20
    inv_svc.save_trade(ctx, edited, trade.id)
    assert set(linked(ctx, trade.id)) == {TX_INCOME}


def test_holdings(ctx, depot):
    with pytest.raises(ValidationError, match="Select a securities or crypto account"):
        inv_svc.add_holding(ctx, depot["bank"].id, "Gold")
    with pytest.raises(ValidationError, match="Name is required"):
        inv_svc.add_holding(ctx, depot["acc"].id, " ")
    with pytest.raises(ValidationError, match="already has a holding"):
        inv_svc.add_holding(ctx, depot["acc"].id, "nestle")
    other = acc_svc.add_account(ctx, "Wallet", "CHF", asset_class=ASSET_CRYPTO)
    trade = buy(ctx, depot, D(2026, 8, 3), 2, 50)
    inv_svc.edit_holding(ctx, depot["nestle"].id, "Nestlé N", "NESN")
    assert "Nestlé N" in linked(ctx, trade.id)[TX_TRANSFER].comment  # descriptions follow the name
    with pytest.raises(ValidationError, match="stays in its account"):
        inv_svc.edit_holding(ctx, depot["nestle"].id, "Nestlé N", "NESN", other.id)
    moved = inv_svc.edit_holding(ctx, depot["etf"].id, "World ETF", "VWRL", other.id)
    assert moved.account_id == other.id
    with pytest.raises(ValidationError, match="Holding not found"):
        inv_svc.edit_holding(ctx, "nope", "x")
    assert inv_svc.holding_label(moved) == "World ETF · VWRL"
    inv_svc.delete_holding(ctx, depot["nestle"].id)
    assert ctx.store.trades.all() == [] and [tx for tx in ctx.store.transactions.all() if tx.invest_id] == []


# ------------------------------------------------------------------ accounts, transactions, balances
def test_asset_classes_of_accounts(ctx, setup, tmp_path):
    assert setup["chf"].asset_class == "cash"
    pillar = acc_svc.add_account(ctx, "3a", "CHF", asset_class=ASSET_PILLAR3A)
    assert pillar.asset_class == ASSET_PILLAR3A and not pillar.holds_securities
    adv = acc_svc.add_account(ctx, "Owed", "CHF", kind="advance", asset_class=ASSET_SECURITIES)
    assert adv.asset_class == "cash"  # advance-payment accounts are cash
    with pytest.raises(ValidationError, match="Select an asset class"):
        acc_svc.add_account(ctx, "X", "CHF", asset_class="gold")
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-08", 100)
    with pytest.raises(ValidationError, match="through a conversion"):
        acc_svc.edit_account(ctx, setup["chf"].id, "Bank", "CHF", ASSET_SECURITIES)
    acc_svc.edit_account(ctx, setup["chf"].id, "Bank", "CHF", ASSET_PILLAR3A)  # between valued classes: fine
    fresh = acc_svc.edit_account(ctx, setup["eur"].id, "Euro account", "EUR", ASSET_SECURITIES)
    inv_svc.add_holding(ctx, fresh.id, "Airbus")
    with pytest.raises(ValidationError, match="still has holdings, with their trades and prices"):
        acc_svc.edit_account(ctx, fresh.id, "Euro account", "EUR", ASSET_PILLAR3A)
    acc_svc.edit_account(ctx, fresh.id, "Euros", "EUR", ASSET_CRYPTO)  # one holdings class to the other
    assert acc_svc.edit_account(ctx, fresh.id, "Euros", "EUR").asset_class == ASSET_CRYPTO  # None keeps it
    with pytest.raises(ValidationError, match="Accounts with holdings cannot be deleted"):
        acc_svc.delete_account(ctx, fresh.id)


def test_accounts_written_before_asset_classes_are_cash(tmp_path, rates):
    from budget_app.services.context import Context  # noqa: PLC0415
    from budget_app.storage.store import Store  # noqa: PLC0415

    (tmp_path / "accounts.csv").write_text(
        "name,currency,kind,created,id\nBank,CHF,regular,2026-01-01,a1\n", encoding="utf-8"
    )
    ctx = Context(store=Store(tmp_path), _fx=rates)
    acc = ctx.store.accounts.get("a1")
    assert acc is not None and acc.asset_class == "cash" and not acc.holds_securities


def test_manual_transactions_on_investment_accounts_are_refused(ctx, depot):
    trade = buy(ctx, depot, D(2026, 8, 3), 2, 50, fee=1)
    move = linked(ctx, trade.id)[TX_TRANSFER]
    transfer = TransactionInput(
        date=D(2026, 8, 4),
        type=TX_TRANSFER,
        amount=10,
        currency="CHF",
        from_account=depot["bank"].id,
        to_account=depot["acc"].id,
    )
    with pytest.raises(ValidationError, match="change through trades and prices"):
        tx_svc.save(ctx, transfer)
    reval = TransactionInput(
        date=D(2026, 8, 4), type=TX_REVALUATION, currency="CHF", category=REVAL_INVEST, reval_account=depot["acc"].id
    )
    reval.reval_change = 5
    with pytest.raises(ValidationError, match="change through trades and prices"):
        tx_svc.save(ctx, reval)
    with pytest.raises(ValidationError, match="booked on the Investments page"):
        tx_svc.save(ctx, tx_svc.edit_input(ctx, move), editing_id=move.id)
    with pytest.raises(ValidationError, match="booked on the Investments page"):
        tx_svc.delete(ctx, [move.id])
    with pytest.raises(ValidationError, match="valued from its holdings"):
        wealth_svc.upsert_entry(ctx, depot["acc"].id, "2026-08", 100)
    fee = linked(ctx, trade.id)[TX_EXPENDITURE]
    changes = bulk_svc.BulkChanges(expenditure_category="01.1", note_mode=bulk_svc.NOTE_REPLACE, note="x")
    assert bulk_svc.plan(ctx, [fee.id, move.id], changes).edits == []  # left as they are
    assert not bulk_svc.takes_note(fee) and bulk_svc.is_invested(fee)
    assert inv_svc.source(ctx, move) == ctx.store.trades.get(trade.id)


def test_month_end_balance_comes_from_the_holdings(ctx, depot):
    assert wealth_svc.balance_entry(ctx, depot["acc"].id, "2026-08") is None
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 9, 30), 104.4)
    aug = wealth_svc.balance_entry(ctx, depot["acc"].id, "2026-08")
    assert aug is not None and aug.amount == 1000 and aug.month == "2026-08"
    assert wealth_svc.balance_entry(ctx, depot["acc"].id, "2026-09").amount == 1044
    assert wealth_svc.balance_entry(ctx, depot["acc"].id, "2026-07") is None
    assert wealth_svc.first_recorded_month(ctx) == "2026-08"
    assert wealth_svc.last_recorded_month(ctx) is None  # the projection builds on recorded balances only
    rows = {r.account.id: r for r in wealth_svc.account_rows(ctx, "2026-09", "CHF")}
    assert rows[depot["acc"].id].has_entry and not rows[depot["acc"].id].needs_entry
    assert wealth_svc.by_class(ctx, "2026-09", "CHF") == {ASSET_SECURITIES: 1044}
    assert wealth_svc.uses_classes(ctx)


def _record(ctx, account_id: str, month: str) -> None:
    """Record the account's balance at the end of `month` as its transactions add up (they all start at 0)."""
    wealth_svc.upsert_entry(ctx, account_id, month, booked(ctx, account_id, months.last_day(month)))


def test_the_reconciliation_explains_a_month_with_trades_and_prices(ctx, depot):
    bank = depot["bank"].id
    tx_svc.save(
        ctx,
        TransactionInput(
            date=D(2026, 7, 25),
            type=TX_INCOME,
            amount=5000,
            currency="CHF",
            category=depot["salary"].id,
            to_account=bank,
        ),
    )
    buy(ctx, depot, D(2026, 8, 3), 10, 100, fee=9.9)
    buy(ctx, depot, D(2026, 8, 5), 3, 50.123456, holding="etf")
    for m in ("2026-07", "2026-08"):
        _record(ctx, bank, m)
    # September: a price update, a sale between two prices, a back-dated price, a dividend and a buy
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 9, 10), 110)
    sell(ctx, depot, D(2026, 9, 15), 4, 108.5, fee=4.95)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 9, 30), 112.35)
    inv_svc.save_price(ctx, depot["etf"].id, D(2026, 9, 1), 49.777)
    buy(ctx, depot, D(2026, 9, 20), 1.5, 51, holding="etf", fee=1)
    buy(ctx, depot, D(2026, 9, 25), None, None, kind=TRADE_DIVIDEND, amount=12.6, tax=4.41)
    _record(ctx, bank, "2026-09")
    for view in ("CHF", "EUR"):
        rep = reports.month_report(ctx, "2026-09", view, today=D(2026, 10, 21))
        assert rep.unexplained == pytest.approx(0, abs=0.01), view
        row = next(r for r in rep.accounts if r.account.id == depot["acc"].id)
        assert row.unexplained == 0 and row.has_entry
        assert row.amount == inv_svc.account_value(ctx, depot["acc"].id, D(2026, 9, 30))
    first = reports.month_report(ctx, "2026-08", "CHF", today=D(2026, 10, 21))
    assert first.unexplained == pytest.approx(0, abs=0.01)
    row = next(r for r in first.accounts if r.account.id == depot["acc"].id)
    assert row.opening == 0 and row.unexplained == 0  # the month of the first buy


def test_the_reconciliation_of_a_foreign_currency_depot(ctx, setup):
    usd_cash = acc_svc.add_account(ctx, "USD cash", "USD")
    broker = acc_svc.add_account(ctx, "Broker", "USD", asset_class=ASSET_SECURITIES)
    apple = inv_svc.add_holding(ctx, broker.id, "Apple", "AAPL")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=D(2026, 8, 1),
            type=TX_INCOME,
            amount=10000,
            currency="USD",
            category=setup["salary"].id,
            to_account=usd_cash.id,
        ),
    )
    trade = TradeInput(TRADE_BUY, apple.id, D(2026, 8, 10), 20, 220.15, 2, cash_account=usd_cash.id)
    inv_svc.save_trade(ctx, trade)
    _record(ctx, usd_cash.id, "2026-08")
    inv_svc.save_price(ctx, apple.id, D(2026, 9, 12), 231.4)
    inv_svc.save_trade(ctx, TradeInput(TRADE_SELL, apple.id, D(2026, 9, 18), 5, 228, 2, cash_account=usd_cash.id))
    inv_svc.save_price(ctx, apple.id, D(2026, 9, 30), 240)
    _record(ctx, usd_cash.id, "2026-09")
    rep = reports.month_report(ctx, "2026-09", "CHF", today=D(2026, 10, 21))
    assert rep.unexplained == pytest.approx(0, abs=0.01)
    row = next(r for r in rep.accounts if r.account.id == broker.id)
    assert row.unexplained == 0 and row.amount == 15 * 240


# ------------------------------------------------------------------ price lists
def test_reading_a_price_list(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 9, 1), 101)
    text = "\n".join(
        [
            "Datum\tTitel\tKurs",
            "01.09.2026\tCH0038863350\t102,50",
            "02.09.2026\tworld etf\t1'234,5",
            "03.09.2026\tUnknown AG\t5",
            "04.09.2026\tVWRL\tn/a",
            "31.12.2099\tVWRL\t5",
            "",
            "just text",
            "05.09.2026\tVWRL\t60",
            "05.09.2026\tvwrl\t61",
        ]
    )
    lines = inv_svc.read_price_list(ctx, text)
    by_line = {pl.line: pl for pl in lines}
    assert 1 not in by_line  # the header
    assert by_line[2].ok and by_line[2].price == 102.5 and by_line[2].replaces == 101
    assert by_line[2].holding_id == depot["nestle"].id and by_line[2].date == D(2026, 9, 1)
    assert by_line[3].price == 1234.5 and by_line[3].holding_id == depot["etf"].id
    assert "No holding “Unknown AG”" in by_line[4].error
    assert "Not a price" in by_line[5].error
    assert "future" in by_line[6].error
    assert "Expected date, holding and price" in by_line[8].error
    assert by_line[9].error == "Repeated below" and by_line[10].ok
    assert not ctx.store.prices.all()[1:]  # reading saves nothing
    assert inv_svc.save_prices(ctx, lines) == 3
    assert inv_svc.price_on(ctx, depot["nestle"].id, D(2026, 9, 1)).price == 102.5  # replaced
    assert inv_svc.price_on(ctx, depot["etf"].id, D(2026, 9, 5)).price == 61
    assert booked(ctx, depot["acc"].id) == inv_svc.account_value(ctx, depot["acc"].id) == 1025


def test_price_lists_with_other_separators(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    semi = inv_svc.read_price_list(ctx, "2026-09-01;Nestlé;101.25\n2026-09-02;NESTLE;101,75")
    assert [pl.price for pl in semi] == [101.25, 101.75]
    comma = inv_svc.read_price_list(ctx, '2026-09-03,"Nestlé",102.5\n2026-09-04,Nestlé,1')
    assert [pl.price for pl in comma] == [102.5, 1] and all(pl.ok for pl in comma)
    bad = inv_svc.read_price_list(ctx, "Date,Holding,Price\nyesterday,Nestlé,1")
    assert bad[0].error == "Not a date: yesterday"
    assert inv_svc.read_price_list(ctx, "") == []


def test_price_effect_and_defaults(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    step = inv_svc.price_effect(ctx, depot["nestle"].id, D(2026, 8, 31), 103)
    assert step is not None and step.held_after == 10 and step.revaluation == 30 and step.value == 1030
    assert inv_svc.price_effect(ctx, "nope", D(2026, 8, 31), 1) is None
    assert ctx.store.prices.all() == []  # a preview saves nothing
    assert inv_svc.last_cash_account(ctx, "CHF") == depot["bank"].id
    assert inv_svc.last_cash_account(ctx, "USD") == ""


def test_numbers_as_banks_write_them():
    cases = {
        "97.40": 97.4,
        "97,40": 97.4,
        "1'234.50": 1234.5,
        "1.234,50": 1234.5,
        "1,234.50": 1234.5,
        "CHF 97.40": 97.4,
        "1.234.567": 1234567,
        "-3,5": -3.5,
        "abc": None,
        "": None,
        "1-2": None,
    }
    for text, value in cases.items():
        assert inv.parse_number(text) == value, text
    assert inv.date_format(["Date", "x"]) is None
    assert inv.parse_day("2026-09-01T10:00", "%Y-%m-%d") == D(2026, 9, 1)


def test_moving_a_price_to_another_day_or_holding(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    buy(ctx, depot, D(2026, 8, 3), 2, 50, holding="etf")
    first = inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 20), 110)
    second = inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 120)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 115, editing_id=first.id)  # onto the other's day
    assert [p.id for p in inv_svc.prices_of(ctx, depot["nestle"].id)] == [first.id]
    assert linked(ctx, second.id) == {}
    inv_svc.save_price(ctx, depot["etf"].id, D(2026, 8, 31), 55, editing_id=first.id)  # to the other holding
    assert inv_svc.prices_of(ctx, depot["nestle"].id) == []
    assert booked(ctx, depot["acc"].id) == inv_svc.account_value(ctx, depot["acc"].id) == 1000 + 110


def test_odd_price_list_lines_and_stale_holdings(ctx, depot):
    buy(ctx, depot, D(2026, 8, 3), 10, 100)
    lines = inv_svc.read_price_list(ctx, "2026-09-01;;5\n2026-09-02;Nestlé;5")
    assert lines[0].error == "No holding “”" and lines[1].ok
    inv_svc.delete_holding(ctx, depot["nestle"].id)
    with pytest.raises(ValidationError, match="Select a holding"):
        inv_svc.save_prices(ctx, lines)
    assert ctx.store.prices.all() == []
    spend = TransactionInput(
        date=D(2026, 8, 4),
        type=TX_EXPENDITURE,
        amount=5,
        currency="CHF",
        category="01.1",
        from_account=depot["bank"].id,
    )
    assert inv_svc.source(ctx, tx_svc.save(ctx, spend)) is None


def test_export_and_history_name_the_new_records(ctx, depot):
    import json  # noqa: PLC0415

    from budget_app.services import export as export_svc  # noqa: PLC0415

    trade = buy(ctx, depot, D(2026, 8, 3), 10, 100)
    inv_svc.save_price(ctx, depot["nestle"].id, D(2026, 8, 31), 101)
    doc = json.loads(export_svc.everything_json(ctx))
    assert [h["name"] for h in doc["holdings"]] == ["Nestlé", "World ETF"]
    assert doc["trades"][0]["id"] == trade.id and doc["prices"][0]["price"] == 101
    assert doc["accounts"][-1]["asset_class"] == ASSET_SECURITIES
    action = undo_svc.latest(ctx)
    lines = [undo_svc.describe_change(ctx, c) for c in action.changes]
    assert "Price added: 2026-08-31 · 101.0" in lines
    first = undo_svc.recent(ctx, 10)[1]
    assert any(
        line.startswith("Trade added: 2026-08-03 · buy · 10.0 × 100.0")
        for line in (undo_svc.describe_change(ctx, c) for c in first.changes)
    )
