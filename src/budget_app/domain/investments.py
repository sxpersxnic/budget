"""Holdings in date order (F21): quantities, prices, booked values and performance. Pure functions on the
stored trades and prices of one holding; services/investments.py books what they compute.

A holding's history is a list of events: its trades and its prices, by date. On one day trades come first, in
the order they were entered, and a price typed in or pasted for that day last (a price list quotes the close).
Buys and sales carry a price too, so every one of them is also a price of its day.

The investment account's booked value of a holding stays at round(quantity × latest price, 2) after every
event: a buy or sale moves round(quantity × price, 2) of money, and a revaluation books the rest of the change,

    revaluation = round(held after × price, 2) − round(held before × previous price, 2) − money moved in,

which is quantity × (new price − previous price) up to the cent. Computing it from rounded values rather than
from the price difference keeps the cents exact however many events there are.

A dividend is entered gross with its withholding tax (decision 12): what counts as earned is the net.

An opening holding (decision 13: an account with history became a securities or crypto account) starts the
holding with its quantity and price, before anything else of its day. It moves its value into the holding like
a buy, without any money leaving another account: it was already in the account. Its value counts as money put
in, so the gain is measured from the conversion on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from budget_app.domain.models import TRADE_BUY, TRADE_DIVIDEND, TRADE_OPENING, TRADE_SELL, Price, Trade

PRICE = "price"  # the kind of an event that is a price typed in or pasted
QTY_EPS = 1e-7  # quantities have up to 6 decimals
EPS = 0.005  # money has 2
SWISS_WITHHOLDING = 0.35  # Swiss withholding tax (Verrechnungssteuer) on dividends, reclaimable


def qty(value: float) -> float:
    return round(float(value), 6)


def money(value: float) -> float:
    return round(float(value), 2)


def default_withholding(gross: float | None, currency: str) -> float:
    """The withholding tax to start a dividend with: 35 % in CHF (Swiss withholding tax), else none."""
    return money((gross or 0) * SWISS_WITHHOLDING) if currency == "CHF" else 0.0


@dataclass(frozen=True)
class Event:
    """A trade or a price of a holding, in the form the replay needs."""

    date: date
    kind: str  # TRADE_BUY, TRADE_SELL, TRADE_DIVIDEND, TRADE_OPENING or PRICE
    id: str
    quantity: float = 0.0
    price: float = 0.0
    fee: float = 0.0
    amount: float = 0.0  # dividend, gross
    tax: float = 0.0  # dividend, withholding tax
    seq: int = 0

    @property
    def sets_price(self) -> bool:
        return self.kind in (TRADE_BUY, TRADE_SELL, TRADE_OPENING, PRICE)

    @property
    def change(self) -> float:
        """Quantity bought or opened with (+) or sold (−)."""
        return {TRADE_BUY: self.quantity, TRADE_OPENING: self.quantity, TRADE_SELL: -self.quantity}.get(self.kind, 0.0)

    @property
    def sort_key(self) -> tuple:
        rank = 0 if self.kind == TRADE_OPENING else 2 if self.kind == PRICE else 1  # the opening first, prices last
        return (self.date, rank, self.seq, self.id)


def events(trades: Iterable[Trade], prices: Iterable[Price]) -> list[Event]:
    """The trades and prices of one holding in the order they count."""
    out = [
        Event(
            tr.date, tr.kind, tr.id, qty(tr.quantity), tr.price, money(tr.fee), money(tr.amount), money(tr.tax), tr.seq
        )
        for tr in trades
    ]
    out += [Event(p.date, PRICE, p.id, price=p.price) for p in prices]
    return sorted(out, key=lambda e: e.sort_key)


@dataclass(frozen=True)
class Step:
    """One event replayed: what was held before and after it, and what it books."""

    event: Event
    held_before: float
    held_after: float
    price_before: float | None  # the latest price before the event (None before the first)
    price: float | None  # the latest price after it
    moved: float  # money into the investment account: + for a buy, − for a sale (rounded)
    revaluation: float  # the change in value the event books as a capital gain (+) or loss (−)
    value: float  # the booked value after it: round(held after × price, 2)


def replay(evts: list[Event]) -> list[Step]:
    """Every event in order with its quantities and bookings (a sale may sell more than held; see `oversold`)."""
    held, price, value = 0.0, None, 0.0
    out = []
    for e in evts:
        held_after = qty(held + e.change)
        new_price = e.price if e.sets_price else price
        moved = money(abs(e.change) * e.price) * (1 if e.change > 0 else -1) if e.change else 0.0
        new_value = money(held_after * new_price) if new_price is not None else 0.0
        reval = money(new_value - value - moved) if e.sets_price else 0.0
        out.append(Step(e, held, held_after, price, new_price, moved, reval, new_value if e.sets_price else value))
        held, price = held_after, new_price
        value = out[-1].value
    return out


def oversold(steps: list[Step]) -> Step | None:
    """The first sale that sells more than was held at that point, if any."""
    return next((s for s in steps if s.held_after < -QTY_EPS), None)


def last_step(steps: list[Step], on: date | None = None) -> Step | None:
    """The last step on or before `on` (the last of all without a date)."""
    found = None
    for s in steps:
        if on is not None and s.event.date > on:
            break
        found = s
    return found


def price_date(steps: list[Step], on: date | None = None) -> date | None:
    """The day of the latest price on or before `on`."""
    found = None
    for s in steps:
        if on is not None and s.event.date > on:
            break
        if s.event.sets_price:
            found = s.event.date
    return found


def value_on(steps: list[Step], on: date | None = None) -> float:
    """The booked value at the end of `on`: round(quantity × latest price, 2), 0 before the first event."""
    step = last_step(steps, on)
    return step.value if step else 0.0


# ------------------------------------------------------------------ performance
@dataclass
class Performance:
    """What a holding (or several, added up in one currency) earned.

    gain = value − money put in + dividends − fees, where money put in = bought − sold and dividends are net
    (gross − withholding tax: what was actually received). It splits into realized (sales against the average
    cost of what was sold) and unrealized (value against the average cost of what is still held), plus
    dividends, minus fees. The withholding tax is kept apart (`withholding`): in CHF it can be reclaimed.
    """

    value: float = 0.0
    opening: float = 0.0  # the value of the opening holding (decision 13)
    bought: float = 0.0
    sold: float = 0.0
    dividends: float = 0.0  # net: gross − withholding tax
    withholding: float = 0.0  # withholding tax on the dividends
    fees: float = 0.0
    cost: float = 0.0  # average cost of the quantity still held
    realized: float = 0.0
    quantity: float = 0.0
    price: float | None = None
    price_date: date | None = None
    trades: int = field(default=0)

    @property
    def invested(self) -> float:
        """Money put in: the opening value, plus buys, minus sales."""
        return self.opening + self.bought - self.sold

    @property
    def unrealized(self) -> float:
        return self.value - self.cost

    @property
    def gain(self) -> float:
        return self.value - self.invested + self.dividends - self.fees

    @property
    def pct(self) -> float | None:
        """The gain in percent of the money put in; None when nothing (or less than nothing) is in."""
        return self.gain / self.invested * 100 if self.invested > EPS else None

    def scaled(self, factor: float) -> Performance:
        """The money amounts times `factor` (another currency); quantity and price drop out."""
        return Performance(
            value=self.value * factor,
            opening=self.opening * factor,
            bought=self.bought * factor,
            sold=self.sold * factor,
            dividends=self.dividends * factor,
            withholding=self.withholding * factor,
            fees=self.fees * factor,
            cost=self.cost * factor,
            realized=self.realized * factor,
            trades=self.trades,
        )

    def __add__(self, other: Performance) -> Performance:
        return Performance(
            value=self.value + other.value,
            opening=self.opening + other.opening,
            bought=self.bought + other.bought,
            sold=self.sold + other.sold,
            dividends=self.dividends + other.dividends,
            withholding=self.withholding + other.withholding,
            fees=self.fees + other.fees,
            cost=self.cost + other.cost,
            realized=self.realized + other.realized,
            trades=self.trades + other.trades,
        )


def performance(steps: list[Step], on: date | None = None) -> Performance:
    """A holding's performance up to the end of `on` (everything without a date)."""
    perf = Performance()
    for s in steps:
        e = s.event
        if on is not None and e.date > on:
            break
        if e.kind == TRADE_BUY:
            perf.bought += s.moved
            perf.cost += s.moved
        elif e.kind == TRADE_OPENING:
            perf.opening += s.moved
            perf.cost += s.moved
        elif e.kind == TRADE_SELL:
            proceeds = -s.moved
            share = e.quantity / s.held_before if s.held_before > QTY_EPS else 1.0
            cost_sold = perf.cost if s.held_after <= QTY_EPS else perf.cost * share
            perf.sold += proceeds
            perf.cost -= cost_sold
            perf.realized += proceeds - cost_sold
        elif e.kind == TRADE_DIVIDEND:
            perf.dividends += e.amount - e.tax
            perf.withholding += e.tax
        if e.kind != PRICE:
            perf.fees += e.fee
            perf.trades += 1
        perf.quantity, perf.price, perf.value = s.held_after, s.price, s.value
    perf.price_date = price_date(steps, on)
    return perf


# ------------------------------------------------------------------ display
def fmt_quantity(value: float) -> str:
    """12, 0.5, 1'250.125, 0.000125: up to 6 decimals, no trailing zeros, Swiss thousands."""
    text = f"{value:,.6f}".rstrip("0").rstrip(".")
    return text.replace(",", "'").replace("-", "−")


def fmt_money(value: float, currency: str) -> str:
    """1'234.50 CHF (in descriptions of booked rows; the pages format with layout.fmt)."""
    text = f"{round(value, 2) + 0.0:,.2f}".replace(",", "'").replace("-", "−")
    return f"{text} {currency}"


def fmt_price(value: float) -> str:
    """95.50, 0.123456: at least 2 decimals, up to 6."""
    text = f"{value:,.6f}".rstrip("0")
    whole, _, decimals = text.partition(".")
    return f"{whole}.{decimals.ljust(2, '0')}".replace(",", "'").replace("-", "−")


# ------------------------------------------------------------------ price lists
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%Y%m%d")


def parse_day(text: str, fmt: str | None) -> date | None:
    try:
        return datetime.strptime(text.strip().split(" ")[0].split("T")[0], fmt).date() if fmt else None
    except ValueError:
        return None


def date_format(values: list[str]) -> str | None:
    """The date format most of `values` are written in (a header or a stray line doesn't count)."""
    counts = {fmt: sum(1 for v in values if parse_day(v, fmt)) for fmt in DATE_FORMATS}
    best = max(counts, key=lambda fmt: counts[fmt])
    return best if counts[best] else None


def parse_number(text: str) -> float | None:
    """A price as banks write it: 97.40, 97,40, 1'234.50, 1.234,50, 1,234.50, CHF 97.40; None if it isn't one.

    With both "." and "," the later one is the decimal mark; a single one of either is the decimal mark too.
    """
    s = re.sub(r"[^\d.,-]", "", text or "")
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
    else:
        decimal = "," if s.count(",") == 1 else "." if s.count(".") == 1 else ""
    for mark in {",", "."} - {decimal}:
        s = s.replace(mark, "")
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None
