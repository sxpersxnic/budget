"""Converting an account with history into a securities or crypto account (F21, decision 13): its recorded
balances stay its history, opening holdings take over, and the conversion month reconciles."""

from datetime import date

import pytest

from budget_app.domain import months
from budget_app.domain.models import (
    ASSET_CASH,
    ASSET_CRYPTO,
    ASSET_SECURITIES,
    REVAL_INVEST,
    TRADE_BUY,
    TRADE_OPENING,
    TX_INCOME,
    TX_REVALUATION,
    TX_TRANSFER,
    Recurring,
)
from budget_app.services import accounts as acc_svc
from budget_app.services import investments as inv_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import ValidationError
from budget_app.services.investments import OpeningHolding, TradeInput
from budget_app.services.transactions import TransactionInput

D = date
NOVEMBER = D(2026, 11, 5)  # a day on which October is complete


def move(ctx, day, amount, src, dst, currency="CHF"):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=day, type=TX_TRANSFER, amount=amount, currency=currency, from_account=src, to_account=dst
        ),
    )


def earn(ctx, day, amount, category, account, currency="CHF"):
    tx_svc.save(
        ctx,
        TransactionInput(
            date=day, type=TX_INCOME, amount=amount, currency=currency, category=category, to_account=account
        ),
    )


def revalue(ctx, day, change, account, currency="CHF"):
    inp = TransactionInput(
        date=day, type=TX_REVALUATION, currency=currency, category=REVAL_INVEST, reval_account=account
    )
    inp.reval_change = change
    return tx_svc.save(ctx, inp)


def conversion_row(ctx, account_id):
    rows = [tx for tx in ctx.store.transactions.all() if tx.invest_id == account_id]
    assert len(rows) <= 1
    return rows[0] if rows else None


def signed(tx) -> float:
    return tx.amount if tx.to_account else -tx.amount


@pytest.fixture
def fund(ctx, setup):
    """A CHF fund account with history: a transfer in July, recorded balances for July and August and a manual
    revaluation in August, so August reconciles from its recorded balances."""
    bank, fund = setup["chf"], acc_svc.add_account(ctx, "Fund", "CHF")
    earn(ctx, D(2026, 7, 25), 5000, setup["salary"].id, bank.id)
    old = move(ctx, D(2026, 7, 26), 2000, bank.id, fund.id)
    wealth_svc.upsert_entry(ctx, bank.id, "2026-07", 3000)
    wealth_svc.upsert_entry(ctx, fund.id, "2026-07", 10000)
    move(ctx, D(2026, 8, 10), 500, bank.id, fund.id)
    revalue(ctx, D(2026, 8, 31), 200, fund.id)
    wealth_svc.upsert_entry(ctx, bank.id, "2026-08", 2500)
    wealth_svc.upsert_entry(ctx, fund.id, "2026-08", 10700)
    wealth_svc.upsert_entry(ctx, fund.id, "2026-09", 11000)  # replaced by the holdings' value
    return {"bank": bank, "fund": fund, "old": old, **setup}


OPENING = [
    OpeningHolding("Fund A", "CH0001", 100, 104.5),  # 10'450.00
    OpeningHolding(" Gold ETF ", "ZGLD", 2, 130.25),  # 260.50
    OpeningHolding(),  # a blank row is left out
]


def convert(ctx, fund, rows=OPENING, month="2026-09", asset_class=ASSET_SECURITIES):
    return inv_svc.convert_account(ctx, fund["fund"].id, asset_class, month, rows)


def holding(ctx, account_id, name):
    return next(h for h in inv_svc.holdings(ctx, account_id) if h.name == name)


# ------------------------------------------------------------------ converting
def test_a_conversion_keeps_the_history_and_starts_the_holdings(ctx, fund):
    acc_id = fund["fund"].id
    assert inv_svc.needs_conversion(ctx, acc_id, ASSET_SECURITIES)
    assert not inv_svc.needs_conversion(ctx, acc_id, ASSET_CASH)
    preview = inv_svc.check_conversion(ctx, acc_id, ASSET_SECURITIES, "2026-09", OPENING)
    assert (preview.day, preview.balance, preview.balance_month) == (D(2026, 9, 1), 10700, "2026-08")
    assert (preview.value, preview.difference, preview.replaced) == (10710.5, 10.5, ["2026-09"])
    acc = convert(ctx, fund)
    assert (acc.asset_class, acc.invest_from) == (ASSET_SECURITIES, "2026-09")
    assert not inv_svc.needs_conversion(ctx, acc_id, ASSET_SECURITIES)
    gold = holding(ctx, acc_id, "Gold ETF")
    assert gold.symbol == "ZGLD"
    [opening] = inv_svc.trades_of(ctx, gold.id)
    assert (opening.kind, opening.date, opening.quantity, opening.price, opening.cash_account) == (
        TRADE_OPENING,
        D(2026, 9, 1),
        2,
        130.25,
        "",
    )
    assert not [tx for tx in ctx.store.transactions.all() if tx.invest_id == opening.id]  # no money moved
    row = conversion_row(ctx, acc_id)
    assert (row.type, row.category, row.to_account, row.amount, row.date) == (
        TX_REVALUATION,
        REVAL_INVEST,
        acc_id,
        10.5,
        D(2026, 9, 1),
    )
    assert row.comment == "Opening holdings of Fund worth 10'710.50 CHF, last recorded balance 10'700.00 CHF"
    assert inv_svc.source(ctx, row) == ctx.store.accounts.get(acc_id)
    # the past stays: recorded balances before, holdings from the conversion month on
    assert wealth_svc.entry_for(ctx, acc_id, "2026-09") is None  # the recorded September balance is replaced
    assert wealth_svc.balance_entry(ctx, acc_id, "2026-08").amount == 10700
    assert wealth_svc.balance_entry(ctx, acc_id, "2026-09").amount == 10710.5
    assert ctx.store.transactions.get(fund["old"].id) is not None
    rows = {r.account.id: r for r in wealth_svc.account_rows(ctx, "2026-08", "CHF")}
    assert not rows[acc_id].valued and rows[acc_id].has_entry
    rows = {r.account.id: r for r in wealth_svc.account_rows(ctx, "2026-09", "CHF")}
    assert rows[acc_id].valued and not rows[acc_id].needs_entry
    assert wealth_svc.last_recorded_month(ctx) == "2026-08"
    # no fake gain: the opening value is money put in
    perf = inv_svc.performance(ctx, holding(ctx, acc_id, "Fund A").id)
    assert (perf.opening, perf.invested, perf.gain, perf.value) == (10450, 10450, 0, 10450)


def test_the_conversion_month_and_the_months_around_it_reconcile(ctx, fund):
    acc_id, bank = fund["fund"].id, fund["bank"].id
    convert(ctx, fund)
    fund_a, gold = holding(ctx, acc_id, "Fund A"), holding(ctx, acc_id, "Gold ETF")
    inv_svc.save_trade(ctx, TradeInput(TRADE_BUY, gold.id, D(2026, 9, 15), 1, 131, 2, cash_account=bank))
    inv_svc.save_price(ctx, fund_a.id, D(2026, 9, 30), 105)
    wealth_svc.upsert_entry(ctx, bank, "2026-09", 2500 - 131 - 2)
    inv_svc.save_price(ctx, fund_a.id, D(2026, 10, 15), 106)
    wealth_svc.upsert_entry(ctx, bank, "2026-10", 2500 - 131 - 2)
    for month in ("2026-08", "2026-09", "2026-10"):
        for view in ("CHF", "EUR"):
            rep = reports.month_report(ctx, month, view, today=NOVEMBER)
            assert rep.unexplained == pytest.approx(0, abs=0.01), (month, view)
    rep = reports.month_report(ctx, "2026-09", "CHF", today=NOVEMBER)
    row = next(r for r in rep.accounts if r.account.id == acc_id)
    assert (row.opening, row.amount, row.unexplained) == (10700, 100 * 105 + 3 * 131, 0)
    invest = next(line for line in rep.revaluation_lines if line.key == REVAL_INVEST)
    assert invest.actual == pytest.approx(10.5 + 50 + 1.5)  # the conversion, Fund A's price, Gold's buy price
    august = reports.month_report(ctx, "2026-08", "CHF", today=NOVEMBER)
    row = next(r for r in august.accounts if r.account.id == acc_id)
    assert (row.amount, row.has_entry, row.valued) == (10700, True, False)


def test_a_foreign_currency_account_reconciles_through_its_conversion(ctx, setup):
    cash, broker = setup["eur"], acc_svc.add_account(ctx, "Broker", "EUR")
    move(ctx, D(2026, 7, 10), 1000, cash.id, broker.id, "EUR")
    for acc_id, amount in ((cash.id, 4000), (broker.id, 5000)):
        wealth_svc.upsert_entry(ctx, acc_id, "2026-07", amount)
    revalue(ctx, D(2026, 8, 20), 100, broker.id, "EUR")
    for acc_id, amount in ((cash.id, 4000), (broker.id, 5100)):
        wealth_svc.upsert_entry(ctx, acc_id, "2026-08", amount)
    inv_svc.convert_account(ctx, broker.id, ASSET_SECURITIES, "2026-09", [OpeningHolding("Airbus", "AIR", 40, 125)])
    assert conversion_row(ctx, broker.id).from_account == broker.id  # 5'000 < 5'100: a loss of 100
    airbus = holding(ctx, broker.id, "Airbus")
    inv_svc.save_price(ctx, airbus.id, D(2026, 9, 30), 135)
    inv_svc.save_trade(ctx, TradeInput(TRADE_BUY, airbus.id, D(2026, 10, 2), 2, 134, cash_account=cash.id))
    wealth_svc.upsert_entry(ctx, cash.id, "2026-09", 4000)
    wealth_svc.upsert_entry(ctx, cash.id, "2026-10", 4000 - 268)
    for month in ("2026-08", "2026-09", "2026-10"):
        for view in ("CHF", "EUR", "USD"):
            rep = reports.month_report(ctx, month, view, today=NOVEMBER)
            assert rep.unexplained == pytest.approx(0, abs=0.01), (month, view)
    rep = reports.month_report(ctx, "2026-09", "EUR", today=NOVEMBER)
    row = next(r for r in rep.accounts if r.account.id == broker.id)
    assert (row.opening, row.amount, row.unexplained) == (5100, 40 * 135, 0)


def test_a_conversion_is_one_undo_step(ctx, fund):
    acc_id = fund["fund"].id
    before = len(ctx.store.transactions.all())
    convert(ctx, fund)
    action = undo_svc.latest(ctx)
    assert action is not None and undo_svc.label(ctx, action) == "Convert an account"
    undo_svc.undo(ctx, action.id)
    acc = ctx.store.accounts.get(acc_id)
    assert (acc.asset_class, acc.invest_from) == (ASSET_CASH, "")
    assert ctx.store.holdings.all() == [] and ctx.store.trades.all() == []
    assert len(ctx.store.transactions.all()) == before
    assert wealth_svc.entry_for(ctx, acc_id, "2026-09").amount == 11000


def test_conversion_rules(ctx, fund, monkeypatch):
    acc_id = fund["fund"].id

    def refused(match, rows=OPENING, month="2026-09", asset_class=ASSET_SECURITIES, account=acc_id):
        with pytest.raises(ValidationError, match=match):
            inv_svc.check_conversion(ctx, account, asset_class, month, rows)

    refused("Account not found", account="nope")
    refused("Select securities or crypto", asset_class=ASSET_CASH)
    refused("Select a month", month="")
    refused("can’t be in the future", month=months.add_months(months.current_month(), 1))
    refused("Add at least one holding", rows=[OpeningHolding()])
    refused("Holding 2: enter its name", rows=[OPENING[0], OpeningHolding(symbol="X", quantity=1, price=1)])
    refused("Holding 2: “fund a” is listed twice", rows=[OPENING[0], OpeningHolding("fund a", "", 1, 1)])
    refused("Holding 1: enter the quantity", rows=[OpeningHolding("A", "", 0, 1)])
    refused("Holding 1: enter the price", rows=[OpeningHolding("A", "", 1, None)])
    refused("2 transactions on this account are dated in Aug 2026 or later", month="2026-08")
    refused("3 transactions on this account are dated in Jul 2026 or later", month="2026-07")
    series = ctx.store.recurring.add(
        Recurring(
            "transfer", 100, "CHF", D(2026, 9, 1), D(2026, 10, 1), from_account=fund["bank"].id, to_account=acc_id
        )
    )
    refused("A recurring transaction uses this account")
    series.end = "2026-09-30"  # ended: no longer in the way
    ctx.store.recurring.update(series)
    inv_svc.check_conversion(ctx, acc_id, ASSET_SECURITIES, "2026-09", OPENING)
    convert(ctx, fund, asset_class=ASSET_CRYPTO)
    refused("valued from its holdings already")
    with pytest.raises(ValidationError, match="through a conversion"):
        acc_svc.edit_account(ctx, fund["bank"].id, "Bank", "CHF", ASSET_SECURITIES)


def test_after_the_conversion_the_investment_rules_apply_from_its_month_on(ctx, fund):
    acc_id, bank = fund["fund"].id, fund["bank"].id
    convert(ctx, fund)
    fund_a = holding(ctx, acc_id, "Fund A")
    with pytest.raises(ValidationError, match="change through trades and prices"):
        move(ctx, D(2026, 9, 2), 10, bank, acc_id)
    with pytest.raises(ValidationError, match="change through trades and prices"):
        revalue(ctx, D(2026, 9, 2), 10, acc_id)
    move(ctx, D(2026, 8, 12), 10, bank, acc_id)  # before the conversion month: still an ordinary account
    edited = tx_svc.edit_input(ctx, fund["old"])
    edited.comment = "Savings"
    tx_svc.save(ctx, edited, editing_id=fund["old"].id)
    with pytest.raises(ValidationError, match="Fund is valued from its holdings from Sep 2026 on"):
        wealth_svc.upsert_entry(ctx, acc_id, "2026-09", 1)
    wealth_svc.upsert_entry(ctx, acc_id, "2026-08", 10710)  # the last recorded balance moves the conversion's gain
    assert conversion_row(ctx, acc_id).amount == 0.5
    wealth_svc.upsert_entry(ctx, acc_id, "2026-08", 10710.5)
    assert conversion_row(ctx, acc_id) is None  # no difference left
    wealth_svc.upsert_entry(ctx, acc_id, "2026-08", 10700)
    early = TradeInput(TRADE_BUY, fund_a.id, D(2026, 8, 31), 1, 100, cash_account=bank)
    with pytest.raises(ValidationError, match="from Sep 2026 on; earlier days belong to its recorded balances"):
        inv_svc.save_trade(ctx, early)
    with pytest.raises(ValidationError, match="earlier days belong"):
        inv_svc.save_price(ctx, fund_a.id, D(2026, 8, 31), 100)
    lines = inv_svc.read_price_list(ctx, "2026-08-31;Fund A;100\n2026-09-02;CH0001;104")
    assert "earlier days belong" in lines[0].error and lines[1].ok
    # the opening holding: quantity and price can change, kind, day, holding and cash account can't
    [opening] = inv_svc.trades_of(ctx, fund_a.id)
    edited = inv_svc.to_input(opening)
    edited.quantity, edited.date, edited.cash_account, edited.fee = 101, D(2026, 9, 20), bank, 5
    inv_svc.save_trade(ctx, edited, opening.id)
    changed = ctx.store.trades.get(opening.id)
    assert (changed.quantity, changed.date, changed.cash_account, changed.fee) == (101, D(2026, 9, 1), "", 0)
    assert conversion_row(ctx, acc_id).amount == 115  # 10'815.50 − 10'700
    edited.kind = TRADE_BUY
    with pytest.raises(ValidationError, match="An opening holding stays one"):
        inv_svc.save_trade(ctx, edited, opening.id)
    with pytest.raises(ValidationError, match="Select buy, sell or dividend"):
        inv_svc.save_trade(ctx, TradeInput(TRADE_OPENING, fund_a.id, D(2026, 9, 2), 1, 1))
    inv_svc.delete_trade(ctx, opening.id)
    assert conversion_row(ctx, acc_id).amount == 10700 - 260.5  # only Gold is left: a loss
    assert conversion_row(ctx, acc_id).from_account == acc_id


def test_converting_back_needs_the_holdings_gone(ctx, fund):
    acc_id = fund["fund"].id
    convert(ctx, fund)
    with pytest.raises(ValidationError, match="still has holdings, with their trades and prices"):
        acc_svc.edit_account(ctx, acc_id, "Fund", "CHF", ASSET_CASH)
    acc_svc.edit_account(ctx, acc_id, "Fund", "CHF", ASSET_CRYPTO)  # between holdings classes: kept
    assert ctx.store.accounts.get(acc_id).invest_from == "2026-09"
    for h in inv_svc.holdings(ctx, acc_id):
        inv_svc.delete_holding(ctx, h.id)
    row = conversion_row(ctx, acc_id)
    assert signed(row) == -10700  # without holdings the account is worth nothing from September
    assert wealth_svc.balance_entry(ctx, acc_id, "2026-10").amount == 0
    back = acc_svc.edit_account(ctx, acc_id, "Fund", "CHF", ASSET_CASH)
    assert (back.asset_class, back.invest_from) == (ASSET_CASH, "")
    assert conversion_row(ctx, acc_id) is None and inv_svc.source(ctx, row) is None
    assert wealth_svc.balance_entry(ctx, acc_id, "2026-10").amount == 10700  # carried forward again
    wealth_svc.upsert_entry(ctx, acc_id, "2026-09", 10800)  # a plain account again


def test_accounts_written_before_the_conversion_month_load_unconverted(tmp_path, rates):
    from budget_app.services.context import Context  # noqa: PLC0415
    from budget_app.storage.store import Store  # noqa: PLC0415

    (tmp_path / "accounts.csv").write_text(
        "name,currency,kind,created,asset_class,id\nDepot,CHF,regular,2026-01-01,securities,d1\n", encoding="utf-8"
    )
    acc = Context(store=Store(tmp_path), _fx=rates).store.accounts.get("d1")
    assert acc is not None and acc.invest_from == "" and acc.holds_in("2020-01")
