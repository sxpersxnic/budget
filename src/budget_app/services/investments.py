"""Investments (F21): holdings, trades and prices, the transactions they book, and the value of securities
and crypto accounts.

Every change to a holding's trades or prices books its transactions again from all of them in date order
(`_book`), inside the same recorded action, so one change is one step to undo:

- a buy is a transfer of quantity × price from the cash account to the investment account, a sale the other
  way; a fee is an expenditure from the cash account (`FEE_CATEGORY`);
- a dividend is income of its gross amount to the cash account, in the income category "Dividends" (added when
  missing), and its withholding tax an expenditure in Taxes from the same account: the account receives the net
  (decision 12);
- every price, a trade's included, books the change in value as a linked revaluation of the kind *Capital
  gains on investment* (see domain/investments.py), so the account's booked value is quantity × price.

These rows carry the trade's or price's id in `invest_id`; they change only with it. A securities or crypto
account's month-end balance is not typed in: `value_entry` computes it from the holdings.

An account with history becomes one through a conversion (decision 13, `convert_account`): its recorded
balances stay its history up to the month before the conversion month, and its opening holdings (trades of the
kind TRADE_OPENING on the first day of that month) take over. The difference between the last recorded balance
and their value is booked as one linked *Capital gains on investment* revaluation whose `invest_id` is the
account's id (`book_conversion`), so the conversion month reconciles.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from budget_app import i18n
from budget_app.config import BASE_CURRENCY
from budget_app.domain import investments as inv
from budget_app.domain import months
from budget_app.domain.coicop import TAX, fold
from budget_app.domain.investments import Performance, Step, fmt_money, fmt_price, fmt_quantity
from budget_app.domain.models import (
    ACCOUNT_REGULAR,
    HOLDING_CLASSES,
    REVAL_INVEST,
    TRADE_BUY,
    TRADE_DIVIDEND,
    TRADE_KINDS,
    TRADE_OPENING,
    TRADE_SELL,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    TX_TRANSFER,
    Account,
    AccountEntry,
    Holding,
    IncomeCategory,
    Price,
    Trade,
    Transaction,
)
from budget_app.i18n import N_, t, tn
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

FEE_CATEGORY = "12.2.9.9"  # COICOP 2018: Other financial services n.e.c. (brokerage and custody fees)
DIVIDENDS = N_("Dividends")  # the income category of dividends, created in the interface language


# ------------------------------------------------------------------ reading
def investment_accounts(ctx: Context) -> list[Account]:
    """Regular accounts valued from their holdings (securities and crypto), by name."""
    out = [a for a in ctx.store.accounts.all() if a.kind == ACCOUNT_REGULAR and a.holds_securities]
    return sorted(out, key=lambda a: (a.name.lower(), a.currency))


def holdings(ctx: Context, account_id: str | None = None) -> list[Holding]:
    out = [h for h in ctx.store.holdings.all() if account_id is None or h.account_id == account_id]
    return sorted(out, key=lambda h: (h.name.lower(), h.id))


def holding_label(holding: Holding) -> str:
    return f"{holding.name} · {holding.symbol}" if holding.symbol else holding.name


def currency_of(ctx: Context, holding: Holding) -> str:
    acc = ctx.store.accounts.get(holding.account_id)
    return acc.currency if acc else ""


def trades_of(ctx: Context, holding_id: str) -> list[Trade]:
    return [tr for tr in ctx.store.trades.all() if tr.holding_id == holding_id]


def prices_of(ctx: Context, holding_id: str) -> list[Price]:
    return [p for p in ctx.store.prices.all() if p.holding_id == holding_id]


def steps(ctx: Context, holding_id: str) -> list[Step]:
    return inv.replay(inv.events(trades_of(ctx, holding_id), prices_of(ctx, holding_id)))


def history(ctx: Context, holding_id: str) -> list[Step]:
    """The holding's trades and prices, newest first, with what each booked."""
    return list(reversed(steps(ctx, holding_id)))


def performance(ctx: Context, holding_id: str, on: date | None = None) -> Performance:
    return inv.performance(steps(ctx, holding_id), on)


def account_value(ctx: Context, account_id: str, on: date | None = None) -> float:
    """What the account's holdings are worth at the end of `on`: Σ round(quantity × latest price, 2)."""
    return inv.money(sum(inv.value_on(steps(ctx, h.id), on) for h in holdings(ctx, account_id)))


def first_day(ctx: Context, account_id: str) -> date | None:
    """The date of the account's first trade or price."""
    ids = {h.id for h in holdings(ctx, account_id)}
    days = [tr.date for tr in ctx.store.trades.all() if tr.holding_id in ids]
    days += [p.date for p in ctx.store.prices.all() if p.holding_id in ids]
    return min(days) if days else None


def value_entry(ctx: Context, acc: Account, month: str) -> AccountEntry | None:
    """The month-end balance of a securities or crypto account, computed from its holdings (not stored).

    None before its first trade or price (before its conversion month for a converted account, whose recorded
    balances count until then); afterwards the value at the end of `month`, which is what the linked transfers
    and revaluations booked on the account add up to (plus, for a converted account, its last recorded balance).
    """
    first = acc.invest_from or (months.month_of(day) if (day := first_day(ctx, acc.id)) else None)
    if first is None or first > month:
        return None
    rate_date = ctx.month_rate_date(month)
    amount = account_value(ctx, acc.id, months.last_day(month))
    return AccountEntry(
        account_id=acc.id,
        month=month,
        amount=amount,
        currency=acc.currency,
        amount_chf=round(ctx.convert_on(amount, acc.currency, BASE_CURRENCY, rate_date), 6),
        fx_date=rate_date,
        id=f"holdings:{acc.id}:{month}",
    )


def first_month(ctx: Context) -> str | None:
    """The first month with a trade or price in a securities or crypto account."""
    days = [d for acc in investment_accounts(ctx) if (d := first_day(ctx, acc.id))]
    return months.month_of(min(days)) if days else None


def reclaimable(ctx: Context, holding: Holding | None) -> bool:
    """Whether the withholding tax on the holding's dividends can be reclaimed: Swiss tax, in CHF accounts."""
    return holding is not None and currency_of(ctx, holding) == BASE_CURRENCY


def reclaimable_tax(ctx: Context) -> dict[int, float]:
    """The withholding tax on dividends of holdings in CHF accounts, per year (newest first; years without left out).

    The Swiss withholding tax is reclaimed with the tax return of the year the dividend was paid."""
    out: dict[int, float] = {}
    for tr in ctx.store.trades.all():
        if tr.kind == TRADE_DIVIDEND and tr.tax >= inv.EPS and reclaimable(ctx, ctx.store.holdings.get(tr.holding_id)):
            out[tr.date.year] = inv.money(out.get(tr.date.year, 0.0) + tr.tax)
    return dict(sorted(out.items(), reverse=True))


def source(ctx: Context, tx: Transaction) -> Trade | Price | Account | None:
    """The trade or price that booked `tx` (F21), or the converted account whose opening it is, if it still exists."""
    if not tx.invest_id:
        return None
    found = ctx.store.trades.get(tx.invest_id) or ctx.store.prices.get(tx.invest_id)
    acc = ctx.store.accounts.get(tx.invest_id) if found is None else None
    return found or (acc if acc is not None and acc.invest_from else None)


# ------------------------------------------------------------------ holdings
def _check_holding(ctx: Context, account_id: str, name: str, exclude_id: str = "") -> str:
    acc = ctx.store.accounts.get(account_id) if account_id else None
    if acc is None or acc.kind != ACCOUNT_REGULAR or not acc.holds_securities:
        raise ValidationError(t("Select a securities or crypto account"))
    name = (name or "").strip()
    if not name:
        raise ValidationError(t("Name is required"))
    for h in holdings(ctx, account_id):
        if h.id != exclude_id and fold(h.name) == fold(name):
            raise ValidationError(t("This account already has a holding “{name}”", name=h.name))
    return name


@recorded(N_("Add a holding"))
def add_holding(ctx: Context, account_id: str, name: str, symbol: str = "") -> Holding:
    name = _check_holding(ctx, account_id, name)
    return ctx.store.holdings.add(Holding(account_id=account_id, name=name, symbol=(symbol or "").strip()))


@recorded(N_("Change a holding"))
def edit_holding(ctx: Context, holding_id: str, name: str, symbol: str = "", account_id: str = "") -> Holding:
    """Rename a holding; its account changes only while it has no trades and prices."""
    holding = ctx.store.holdings.get(holding_id)
    if holding is None:
        raise ValidationError(t("Holding not found"))
    account_id = account_id or holding.account_id
    if account_id != holding.account_id and (trades_of(ctx, holding_id) or prices_of(ctx, holding_id)):
        raise ValidationError(t("A holding with trades or prices stays in its account"))
    holding.name = _check_holding(ctx, account_id, name, exclude_id=holding_id)
    holding.symbol, holding.account_id = (symbol or "").strip(), account_id
    saved = ctx.store.holdings.update(holding)
    _book(ctx, holding_id)  # the descriptions name the holding
    return saved


@recorded(N_("Delete a holding"))
def delete_holding(ctx: Context, holding_id: str) -> None:
    """Delete a holding with its trades and prices, and the transactions they booked."""
    holding = ctx.store.holdings.get(holding_id)
    trades = [tr.id for tr in trades_of(ctx, holding_id)]
    prices = [p.id for p in prices_of(ctx, holding_id)]
    ctx.store.trades.delete(trades)
    ctx.store.prices.delete(prices)
    ctx.store.holdings.delete([holding_id])
    _book(ctx, holding_id, holding.account_id if holding else "")


# ------------------------------------------------------------------ trades
@dataclass
class TradeInput:
    kind: str = TRADE_BUY
    holding_id: str = ""
    date: date | None = None
    quantity: float | None = None
    price: float | None = None
    fee: float | None = None
    amount: float | None = None  # dividend: the gross amount
    tax: float | None = None  # dividend: the withholding tax (none: 0)
    cash_account: str = ""
    comment: str = ""


def to_input(trade: Trade) -> TradeInput:
    return TradeInput(
        kind=trade.kind,
        holding_id=trade.holding_id,
        date=trade.date,
        quantity=trade.quantity or None,
        price=trade.price or None,
        fee=trade.fee or None,
        amount=trade.amount or None,
        tax=trade.tax if trade.kind == TRADE_DIVIDEND else None,
        cash_account=trade.cash_account,
        comment=trade.comment,
    )


def _holding(ctx: Context, holding_id: str) -> Holding:
    holding = ctx.store.holdings.get(holding_id) if holding_id else None
    if holding is None:
        raise ValidationError(t("Select a holding"))
    return holding


def _check_date(ctx: Context, holding: Holding, day: date | None) -> date:
    if day is None:
        raise ValidationError(t("Date is required"))
    if day > date.today():
        raise ValidationError(t("The date can’t be in the future"))
    if (message := _before_conversion(ctx, holding, day)) is not None:
        raise ValidationError(message)
    return day


def _before_conversion(ctx: Context, holding: Holding, day: date) -> str | None:
    """Why `day` can't have a trade or price of the holding: its account was converted later (decision 13)."""
    acc = ctx.store.accounts.get(holding.account_id)
    if acc is None or not acc.invest_from or day >= months.first_day(acc.invest_from):
        return None
    return t(
        "{account} is valued from its holdings from {month} on; earlier days belong to its recorded balances",
        account=acc.name,
        month=months.label(acc.invest_from),
    )


def _normalized(ctx: Context, inp: TradeInput, editing_id: str) -> TradeInput:
    """The trade as it is saved: an opening holding keeps its holding and day, and has no fee or cash account."""
    old = ctx.store.trades.get(editing_id) if editing_id else None
    if old is None or old.kind != TRADE_OPENING:
        return inp
    return replace(inp, holding_id=old.holding_id, date=old.date, fee=None, amount=None, tax=None, cash_account="")


def validate_trade(ctx: Context, inp: TradeInput, editing_id: str = "") -> None:
    """Every rule a trade has to meet, including that no sale sells more than is held at that point."""
    old = ctx.store.trades.get(editing_id) if editing_id else None
    opening = old is not None and old.kind == TRADE_OPENING
    if opening and inp.kind != TRADE_OPENING:
        raise ValidationError(t("An opening holding stays one; change its quantity or price"))
    if not opening and inp.kind not in TRADE_KINDS:
        raise ValidationError(t("Select buy, sell or dividend"))
    inp = _normalized(ctx, inp, editing_id)
    holding = _holding(ctx, inp.holding_id)
    _check_date(ctx, holding, inp.date)
    currency = currency_of(ctx, holding)
    if inp.kind == TRADE_DIVIDEND:
        if inp.amount is None or inp.amount < inv.EPS:
            raise ValidationError(t("Enter the gross dividend"))
        if (inp.tax or 0) < 0:
            raise ValidationError(t("The withholding tax can’t be negative"))
        if inv.money(inp.tax or 0) > inv.money(inp.amount):
            raise ValidationError(t("The withholding tax can’t be more than the dividend"))
    else:
        if inp.quantity is None or inp.quantity < inv.QTY_EPS:
            raise ValidationError(t("Enter the quantity"))
        if inp.price is None or inp.price <= 0:
            raise ValidationError(t("Enter the price"))
        if (inp.fee or 0) < 0:
            raise ValidationError(t("The fee can’t be negative"))
    cash = ctx.store.accounts.get(inp.cash_account) if inp.cash_account else None
    if opening:
        pass  # its value was in the account already: no money moves
    elif cash is None or cash.kind != ACCOUNT_REGULAR or cash.holds_securities:
        raise ValidationError(t("Select the cash account"))
    elif cash.currency != currency:
        raise ValidationError(
            t(
                "The cash account must be in {currency}, the currency of {name}. Exchange the money first.",
                currency=currency,
                name=holding.name,
            )
        )
    trade = _build_trade(ctx, inp, editing_id)
    _check_sequence(ctx, trade.holding_id, trade, trade.id)
    if editing_id:
        old = ctx.store.trades.get(editing_id)
        if old is not None and old.holding_id != trade.holding_id:  # moving it away must not oversell either
            _check_sequence(ctx, old.holding_id, None, editing_id)


def _check_sequence(ctx: Context, holding_id: str, changed: Trade | None, changed_id: str) -> None:
    """Refuse a change after which a sale of the holding sells more than is held at that point."""
    trades = [tr for tr in trades_of(ctx, holding_id) if tr.id != changed_id]
    if changed is not None and changed.holding_id == holding_id:
        trades.append(changed)
    bad = inv.oversold(inv.replay(inv.events(trades, prices_of(ctx, holding_id))))
    if bad is None:
        return
    held = fmt_quantity(max(bad.held_before, 0.0))
    day = i18n.date_label(bad.event.date)
    if bad.event.id == changed_id:
        raise ValidationError(t("Only {quantity} held on {date}", quantity=held, date=day))
    raise ValidationError(
        t("Then the sale on {date} would sell more than is held ({quantity})", date=day, quantity=held)
    )


def _build_trade(ctx: Context, inp: TradeInput, editing_id: str) -> Trade:
    old = ctx.store.trades.get(editing_id) if editing_id else None
    inp = _normalized(ctx, inp, editing_id)
    dividend = inp.kind == TRADE_DIVIDEND
    if inp.date is None:  # pragma: no cover - checked by validate_trade() first
        raise ValidationError(t("Date is required"))
    trade = Trade(
        holding_id=inp.holding_id,
        date=inp.date,
        kind=inp.kind,
        quantity=0.0 if dividend else inv.qty(inp.quantity or 0),
        price=0.0 if dividend else round(float(inp.price or 0), 6),
        fee=0.0 if dividend else inv.money(inp.fee or 0),
        amount=inv.money(inp.amount or 0) if dividend else 0.0,
        tax=inv.money(inp.tax or 0) if dividend else 0.0,
        cash_account=inp.cash_account,
        comment=(inp.comment or "").strip(),
        seq=old.seq if old else max((tr.seq for tr in ctx.store.trades.all()), default=0) + 1,
    )
    if old is not None:
        trade.id = old.id
    return trade


@recorded(N_("Save a trade"))
def save_trade(ctx: Context, inp: TradeInput, editing_id: str = "") -> Trade:
    validate_trade(ctx, inp, editing_id)
    trade = _build_trade(ctx, inp, editing_id)
    old = ctx.store.trades.get(editing_id) if editing_id else None
    if old is None:
        saved = ctx.store.trades.add(trade)
    else:
        saved = ctx.store.trades.update(trade)
        if old.holding_id != trade.holding_id:
            moved_from = ctx.store.holdings.get(old.holding_id)
            _book(ctx, old.holding_id, moved_from.account_id if moved_from else "")
    _book(ctx, trade.holding_id)
    return saved


@recorded(N_("Delete a trade"))
def delete_trade(ctx: Context, trade_id: str) -> None:
    trade = ctx.store.trades.get(trade_id)
    if trade is None:
        raise ValidationError(t("Trade not found"))
    _check_sequence(ctx, trade.holding_id, None, trade_id)
    ctx.store.trades.delete([trade_id])
    _book(ctx, trade.holding_id)


# ------------------------------------------------------------------ prices
def price_on(ctx: Context, holding_id: str, day: date) -> Price | None:
    """The price typed in or pasted for the holding on `day`, if any."""
    return next((p for p in prices_of(ctx, holding_id) if p.date == day), None)


def validate_price(ctx: Context, holding_id: str, day: date | None, price: float | None) -> tuple[date, float]:
    """Check a price; returns its day and value."""
    day = _check_date(ctx, _holding(ctx, holding_id), day)
    if price is None or price <= 0:
        raise ValidationError(t("Enter the price"))
    return day, price


@recorded(N_("Save a price"))
def save_price(ctx: Context, holding_id: str, day: date | None, price: float | None, editing_id: str = "") -> Price:
    """Record the holding's price of a day; a price already there for that day is replaced."""
    saved = _put_price(ctx, holding_id, *validate_price(ctx, holding_id, day, price), editing_id)
    _book(ctx, holding_id)
    return saved


def _put_price(ctx: Context, holding_id: str, day: date, price: float, editing_id: str = "") -> Price:
    value = round(float(price), 6)
    same_day = price_on(ctx, holding_id, day)
    editing = ctx.store.prices.get(editing_id) if editing_id else None
    if editing is not None:
        if same_day is not None and same_day.id != editing.id:  # moved onto another price's day: that one goes
            ctx.store.prices.delete([same_day.id])
        moved_from = editing.holding_id
        editing.holding_id, editing.date, editing.price = holding_id, day, value
        saved = ctx.store.prices.update(editing)
        if moved_from != holding_id:
            old_holding = ctx.store.holdings.get(moved_from)
            _book(ctx, moved_from, old_holding.account_id if old_holding else "")
        return saved
    if same_day is not None:
        same_day.price = value
        return ctx.store.prices.update(same_day)
    return ctx.store.prices.add(Price(holding_id=holding_id, date=day, price=value))


def price_effect(ctx: Context, holding_id: str, day: date, price: float, editing_id: str = "") -> Step | None:
    """What recording `price` for `day` would do (the quantity held, the value and the gain or loss it books),
    without saving anything."""
    if ctx.store.holdings.get(holding_id) is None or price <= 0:
        return None
    others = [p for p in prices_of(ctx, holding_id) if p.id != editing_id and p.date != day]
    new = Price(holding_id=holding_id, date=day, price=round(float(price), 6), id="")
    replayed = inv.replay(inv.events(trades_of(ctx, holding_id), [*others, new]))
    return next(s for s in replayed if s.event.kind == inv.PRICE and s.event.id == "")


def last_cash_account(ctx: Context, currency: str) -> str:
    """The cash account of the latest trade in `currency`, to start the next one with."""
    for trade in sorted(ctx.store.trades.all(), key=lambda tr: (tr.date, tr.seq), reverse=True):
        acc = ctx.store.accounts.get(trade.cash_account)
        if acc is not None and acc.currency == currency:
            return acc.id
    return ""


@recorded(N_("Delete a price"))
def delete_price(ctx: Context, price_id: str) -> None:
    price = ctx.store.prices.get(price_id)
    if price is None:
        raise ValidationError(t("Price not found"))
    ctx.store.prices.delete([price_id])
    _book(ctx, price.holding_id)


# ------------------------------------------------------------------ price lists
@dataclass
class PriceLine:
    """One line of a pasted price list: date, holding (name, ISIN or symbol) and price."""

    line: int
    text: str
    date: date | None = None
    holding_id: str = ""
    price: float | None = None
    error: str = ""
    replaces: float | None = None  # the price already recorded for that holding and day

    @property
    def ok(self) -> bool:
        return not self.error


def _cells(line: str) -> list[str]:
    sep = "\t" if "\t" in line else ";" if ";" in line else ","
    return [c.strip().strip('"').strip() for c in line.split(sep)]


def _find_holding(ctx: Context, text: str) -> Holding | None:
    key = fold(text)
    if not key:
        return None
    for h in holdings(ctx):
        if fold(h.symbol) == key or fold(h.name) == key:
            return h
    return None


def read_price_list(ctx: Context, text: str) -> list[PriceLine]:
    """Read a price list as copied from a bank or broker: one line per price, "date, holding, price", the
    cells separated by tabs, semicolons or commas. A header line is skipped. Nothing is saved."""
    raw = [(n, line) for n, line in enumerate((text or "").splitlines(), start=1) if line.strip()]
    rows = [(n, line, _cells(line)) for n, line in raw]
    date_format = inv.date_format([c[0] for _, _, c in rows])
    out = []
    for i, (n, line, cells) in enumerate(rows):
        day = inv.parse_day(cells[0], date_format)
        if day is None and i == 0:
            continue  # a header line
        pl = PriceLine(n, line.strip(), day)
        if len(cells) < 3:
            pl.error = t("Expected date, holding and price")
        elif day is None:
            pl.error = t("Not a date: {value}", value=cells[0])
        elif day > date.today():
            pl.error = t("The date can’t be in the future")
        else:
            pl.price = inv.parse_number(cells[-1])
            holding = _find_holding(ctx, ",".join(cells[1:-1]).strip())
            if holding is None:
                pl.error = t("No holding “{value}”", value=", ".join(cells[1:-1]))
            elif pl.price is None or pl.price <= 0:
                pl.error = t("Not a price: {value}", value=cells[-1])
            elif (before := _before_conversion(ctx, holding, day)) is not None:
                pl.error = before
            else:
                pl.holding_id = holding.id
                same = price_on(ctx, holding.id, day)
                pl.replaces = same.price if same else None
        out.append(pl)
    seen: dict[tuple[str, date], PriceLine] = {}
    for pl in out:  # the same holding and day twice: the later line counts
        if pl.ok and pl.date is not None:
            earlier = seen.get((pl.holding_id, pl.date))
            if earlier is not None:
                earlier.error = t("Repeated below")
            seen[(pl.holding_id, pl.date)] = pl
    return out


@recorded(N_("Save prices"))
def save_prices(ctx: Context, lines: Iterable[PriceLine]) -> int:
    """Record every valid line of a price list (a day's price is replaced); returns how many were saved."""
    saved: list[str] = []
    for pl in lines:
        if pl.ok and pl.date is not None and pl.price is not None:
            _holding(ctx, pl.holding_id)  # deleted since the list was read
            _put_price(ctx, pl.holding_id, pl.date, pl.price)
            saved.append(pl.holding_id)
    for holding_id in dict.fromkeys(saved):
        _book(ctx, holding_id)
    return len(saved)


# ------------------------------------------------------------------ booking
def _dividend_category(ctx: Context, old: Transaction | None) -> str:
    """The income category of dividends: the one booked before, else "Dividends" (in any language), else new."""
    if old is not None and ctx.store.income_categories.get(old.category) is not None:
        return old.category
    names = {fold(t(DIVIDENDS)), fold(DIVIDENDS)}
    for cat in ctx.store.income_categories.all():
        if fold(cat.name) in names:
            return cat.id
    return ctx.store.income_categories.add(IncomeCategory(name=t(DIVIDENDS))).id


def _note(text: str, comment: str) -> str:
    return f"{text} · {comment}" if comment else text


def _rows(ctx: Context, holding: Holding, step: Step, trade: Trade | None) -> list[Transaction]:
    """The transactions one event books, without amounts in CHF yet."""
    e, cur, name = step.event, currency_of(ctx, holding), holding.name
    out = []

    def row(type_: str, amount: float, **kw: Any) -> None:
        tx = Transaction(e.date, type_, inv.money(amount), cur, amount_chf=0.0, fx_date=e.date, invest_id=e.id)
        out.append(replace(tx, **kw))

    comment = trade.comment if trade else ""
    if trade is not None and e.kind in (TRADE_BUY, TRADE_SELL):
        values = dict(quantity=fmt_quantity(e.quantity), name=name, price=fmt_price(e.price))
        buy = e.kind == TRADE_BUY
        text = (
            t("Buy {quantity} {name} at {price}", **values) if buy else t("Sell {quantity} {name} at {price}", **values)
        )
        if abs(step.moved) >= inv.EPS:
            ends = (trade.cash_account, holding.account_id)
            src, dst = ends if buy else ends[::-1]
            row(TX_TRANSFER, abs(step.moved), from_account=src, to_account=dst, comment=_note(text, comment))
        if e.fee >= inv.EPS:
            fee_note = _note(t("Fee: {trade}", trade=text), comment)
            row(TX_EXPENDITURE, e.fee, category=FEE_CATEGORY, from_account=trade.cash_account, comment=fee_note)
    elif trade is not None and e.kind == TRADE_DIVIDEND:
        text = t("Dividend {name}", name=name)
        row(TX_INCOME, e.amount, to_account=trade.cash_account, comment=_note(text, comment))
        if e.tax >= inv.EPS:
            tax_note = _note(t("Withholding tax: {dividend}", dividend=text), comment)
            row(TX_EXPENDITURE, e.tax, category=TAX, from_account=trade.cash_account, comment=tax_note)
    if abs(step.revaluation) >= inv.EPS:
        before = fmt_price(step.price_before) if step.price_before is not None else "–"
        text = t("{name}: price {price}, before {before}", name=name, price=fmt_price(e.price), before=before)
        side = {"to_account": holding.account_id} if step.revaluation > 0 else {"from_account": holding.account_id}
        row(TX_REVALUATION, abs(step.revaluation), category=REVAL_INVEST, comment=text, **side)
    return out


def _book(ctx: Context, holding_id: str, account_id: str = "") -> None:
    """Book the holding's transactions again from its trades and prices, changing only what differs.

    Rows of trades and prices that no longer exist go too. Rows keep their ids, and their valuation in CHF
    while amount, currency and date stay the same (like an edited transaction). The conversion's revaluation
    of the holding's account (or of `account_id`, for a holding just deleted) follows its opening holdings.
    """
    holding = ctx.store.holdings.get(holding_id)
    trades = trades_of(ctx, holding_id) if holding else []
    prices = prices_of(ctx, holding_id) if holding else []
    by_id = {tr.id: tr for tr in trades}
    ids = set(by_id) | {p.id for p in prices}
    alive = {tr.id for tr in ctx.store.trades.all()} | {p.id for p in ctx.store.prices.all()}
    alive |= {a.id for a in ctx.store.accounts.all() if a.invest_from}  # the rows of conversions
    store = ctx.store.transactions
    existing = {
        (tx.invest_id, tx.type): tx
        for tx in store.all()
        if tx.invest_id and (tx.invest_id in ids or tx.invest_id not in alive)
    }
    adds, updates = [], []
    if holding is not None:
        for step in inv.replay(inv.events(trades, prices)):
            for tx in _rows(ctx, holding, step, by_id.get(step.event.id)):
                old = existing.pop((tx.invest_id, tx.type), None)
                if tx.type == TX_INCOME:
                    tx.category = _dividend_category(ctx, old)
                if old is None:
                    tx.amount_chf, tx.fx_date = ctx.to_base_on(tx.amount, tx.currency, tx.date)
                    adds.append(tx)
                    continue
                tx.id = old.id
                if abs(old.amount - tx.amount) < inv.EPS and old.currency == tx.currency and old.date == tx.date:
                    tx.amount_chf, tx.fx_date = old.amount_chf, old.fx_date
                else:
                    tx.amount_chf, tx.fx_date = ctx.to_base_on(tx.amount, tx.currency, tx.date)
                if tx != old:
                    updates.append(tx)
    if existing:
        store.delete([tx.id for tx in existing.values()])
    if updates:
        store.update_many(updates)
    if adds:
        store.add_many(adds)
    if account_id or holding is not None:
        book_conversion(ctx, account_id or (holding.account_id if holding else ""))


# ------------------------------------------------------------------ converting an account with history
@dataclass
class OpeningHolding:
    """A holding an account starts with when it is converted: its name, ISIN or symbol, quantity and price."""

    name: str = ""
    symbol: str = ""
    quantity: float | None = None
    price: float | None = None

    @property
    def blank(self) -> bool:
        return (
            not (self.name or "").strip() and not (self.symbol or "").strip() and not self.quantity and not self.price
        )


@dataclass
class Conversion:
    """What converting an account would do (`check_conversion`), or did."""

    day: date  # the opening holdings' day: the first day of the conversion month
    balance: float  # the last balance recorded before the conversion month (0 without one)
    balance_month: str | None  # its month
    value: float  # Σ round(quantity × price, 2) of the opening holdings
    replaced: list[str]  # months from the conversion month on with a recorded balance, which the holdings replace

    @property
    def difference(self) -> float:
        """The revaluation the conversion books: opening value − last recorded balance."""
        return inv.money(self.value - self.balance)


def recorded_before(ctx: Context, account_id: str, month: str) -> AccountEntry | None:
    """The latest balance recorded for the account in a month before `month`."""
    found = [e for e in ctx.store.account_entries.all() if e.account_id == account_id and e.month < month]
    return max(found, key=lambda e: e.month) if found else None


def opening_value(ctx: Context, account_id: str) -> float:
    """What the account's opening holdings were worth on the conversion day: Σ round(quantity × price, 2)."""
    ids = {h.id for h in holdings(ctx, account_id)}
    trades = [tr for tr in ctx.store.trades.all() if tr.holding_id in ids and tr.kind == TRADE_OPENING]
    return inv.money(sum(inv.money(inv.qty(tr.quantity) * tr.price) for tr in trades))


def conversion_of(ctx: Context, acc: Account) -> Conversion | None:
    """The conversion of a converted account as it stands (None for an account that wasn't converted)."""
    if not acc.invest_from or not acc.holds_securities:
        return None
    before = recorded_before(ctx, acc.id, acc.invest_from)
    return Conversion(
        months.first_day(acc.invest_from),
        before.amount if before else 0.0,
        before.month if before else None,
        opening_value(ctx, acc.id),
        [],
    )


def book_conversion(ctx: Context, account_id: str) -> None:
    """Book the conversion's revaluation again: opening value − last recorded balance, on the conversion day.

    It is linked to the account (`invest_id`), kept in step with the opening holdings and the recorded balance,
    and removed when the account is no longer a converted securities or crypto account."""
    acc = ctx.store.accounts.get(account_id) if account_id else None
    store = ctx.store.transactions
    old = [tx for tx in store.all() if tx.invest_id == account_id] if account_id else []
    conv = conversion_of(ctx, acc) if acc else None
    if acc is None or conv is None or abs(conv.difference) < inv.EPS:
        if old:
            store.delete([tx.id for tx in old])
        return
    text = t(
        "Opening holdings of {name} worth {value}, last recorded balance {balance}",
        name=acc.name,
        value=fmt_money(conv.value, acc.currency),
        balance=fmt_money(conv.balance, acc.currency),
    )
    gain = conv.difference > 0
    tx = Transaction(
        conv.day,
        TX_REVALUATION,
        abs(conv.difference),
        acc.currency,
        amount_chf=0.0,
        fx_date=conv.day,
        category=REVAL_INVEST,
        from_account="" if gain else acc.id,
        to_account=acc.id if gain else "",
        comment=text,
        invest_id=acc.id,
    )
    kept = old[0] if old else None
    if (
        kept is not None
        and abs(kept.amount - tx.amount) < inv.EPS
        and (kept.currency, kept.date) == (tx.currency, tx.date)
    ):
        tx.amount_chf, tx.fx_date = kept.amount_chf, kept.fx_date
    else:
        tx.amount_chf, tx.fx_date = ctx.to_base_on(tx.amount, tx.currency, tx.date)
    if len(old) > 1:  # pragma: no cover - one is booked per account; a second would come from a hand-edited file
        store.delete([extra.id for extra in old[1:]])
    if kept is None:
        store.add(tx)
        return
    tx.id = kept.id
    if tx != kept:
        store.update(tx)


def needs_conversion(ctx: Context, account_id: str, asset_class: str) -> bool:
    """Whether giving the account `asset_class` takes a conversion: a securities or crypto class for an account
    with transactions or recorded balances that isn't valued from holdings yet."""
    from budget_app.services import accounts as acc_svc  # noqa: PLC0415 - accounts imports this module

    acc = ctx.store.accounts.get(account_id)
    return (
        acc is not None
        and asset_class in HOLDING_CLASSES
        and not acc.holds_securities
        and acc_svc.account_in_use(ctx, acc.id)
    )


def check_conversion(
    ctx: Context, account_id: str, asset_class: str, month: str, opening: Iterable[OpeningHolding]
) -> Conversion:
    """Every rule a conversion has to meet; returns what it would do. Blank holding rows are left out."""
    acc = ctx.store.accounts.get(account_id) if account_id else None
    if acc is None or acc.kind != ACCOUNT_REGULAR:
        raise ValidationError(t("Account not found"))
    if asset_class not in HOLDING_CLASSES:
        raise ValidationError(t("Select securities or crypto"))
    if acc.holds_securities:
        raise ValidationError(t("This account is valued from its holdings already"))
    if not month:
        raise ValidationError(t("Select a month"))
    if month > months.current_month():
        raise ValidationError(t("The conversion month can’t be in the future"))
    day = months.first_day(month)
    later = [tx for tx in ctx.store.transactions.all() if acc.id in (tx.from_account, tx.to_account) and tx.date >= day]
    if later:
        raise ValidationError(
            tn(
                "{n} transaction on this account is dated in {month} or later. Choose a later month, or move it to "
                "another account first.",
                "{n} transactions on this account are dated in {month} or later. Choose a later month, or move them "
                "to another account first.",
                len(later),
                month=months.label(month),
            )
        )
    for r in ctx.store.recurring.all():
        ended = r.end_date is not None and r.next_due > r.end_date
        if acc.id in (r.from_account, r.to_account) and not ended:
            raise ValidationError(t("A recurring transaction uses this account. Change or delete it first."))
    rows = [o for o in opening if not o.blank]
    if not rows:
        raise ValidationError(t("Add at least one holding"))
    seen: set[str] = set()
    for n, o in enumerate(rows, start=1):
        name = (o.name or "").strip()
        if not name:
            raise ValidationError(t("Holding {n}: enter its name", n=n))
        if fold(name) in seen:
            raise ValidationError(t("Holding {n}: “{name}” is listed twice", n=n, name=name))
        seen.add(fold(name))
        if o.quantity is None or o.quantity < inv.QTY_EPS:
            raise ValidationError(t("Holding {n}: enter the quantity", n=n))
        if o.price is None or o.price <= 0:
            raise ValidationError(t("Holding {n}: enter the price", n=n))
    before = recorded_before(ctx, acc.id, month)
    value = inv.money(sum(inv.money(inv.qty(o.quantity or 0) * round(float(o.price or 0), 6)) for o in rows))
    replaced = sorted({e.month for e in ctx.store.account_entries.all() if e.account_id == acc.id and e.month >= month})
    return Conversion(day, before.amount if before else 0.0, before.month if before else None, value, replaced)


@recorded(N_("Convert an account"))
def convert_account(
    ctx: Context, account_id: str, asset_class: str, month: str, opening: Iterable[OpeningHolding]
) -> Account:
    """Make an account with history a securities or crypto account from `month` on (decision 13).

    Its balances recorded before `month` stay; those from `month` on are replaced by the holdings' value. The
    opening holdings are trades on the first day of `month`, and the difference between the last recorded
    balance and their value is booked as a linked revaluation, so the month reconciles. One undoable step."""
    rows = [o for o in opening if not o.blank]
    conv = check_conversion(ctx, account_id, asset_class, month, rows)
    acc = ctx.store.accounts.get(account_id)
    if acc is None:  # pragma: no cover - checked by check_conversion() first
        raise ValidationError(t("Account not found"))
    acc.asset_class, acc.invest_from = asset_class, month
    saved = ctx.store.accounts.update(acc)
    gone = [e.id for e in ctx.store.account_entries.all() if e.account_id == acc.id and e.month >= month]
    ctx.store.account_entries.delete(gone)
    seq = max((tr.seq for tr in ctx.store.trades.all()), default=0)
    for o in rows:
        holding = ctx.store.holdings.add(
            Holding(account_id=acc.id, name=o.name.strip(), symbol=(o.symbol or "").strip())
        )
        seq += 1
        quantity, price = inv.qty(o.quantity or 0), round(float(o.price or 0), 6)
        ctx.store.trades.add(Trade(holding.id, conv.day, TRADE_OPENING, quantity, price, seq=seq))
        _book(ctx, holding.id)
    return saved
