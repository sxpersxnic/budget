"""F17, pure. What makes a run of charges a subscription: the rhythm and the amount rules, in one place.

A subscription is a payee charging again and again at the same rhythm (every month, two months, quarter, half
year or year) for about the same amount. The thresholds are fixed and explained here (architecture §7, decision
NEW-F17); amounts are compared in the charges' own currency, so a price is a price whatever the display currency.
"""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import date

PERIODS = (1, 2, 3, 6, 12)  # months between charges: monthly, bi-monthly, quarterly, half-yearly, yearly
DAYS_PER_MONTH = 365.25 / 12
GAP_TOLERANCE_DAYS = 4  # a gap may miss the rhythm by 4 days plus one per month of the period (monthly ±5, yearly ±16)
MIN_CHARGES = 3  # charges needed to call it a rhythm …
MIN_CHARGES_LONG = 2  # … two for half-yearly and yearly rhythms (three would take over a year to see)
LONG_PERIOD = 6  # from this period on, MIN_CHARGES_LONG applies
AMOUNT_TOLERANCE = 0.20  # every charge within 20 % of the median …
AMOUNT_TOLERANCE_MIN = 2.0  # … or 2 units of the currency, so a 9.90 that became 11.90 still counts
STALE_PERIODS = 2  # no charge for two periods (plus the tolerance): the subscription has stopped


@dataclass(frozen=True)
class Rhythm:
    """A detected run: its period in months, the usual (median) amount, its first and last charge and how many."""

    period: int
    amount: float
    first: date
    last: date
    count: int

    @property
    def yearly(self) -> float:
        return self.amount * 12 / self.period


def tolerance_days(period: int) -> float:
    return GAP_TOLERANCE_DAYS + period


def period_of_gap(days: int) -> int | None:
    """The period whose length the gap matches within its tolerance, else None."""
    for period in PERIODS:
        if abs(days - period * DAYS_PER_MONTH) <= tolerance_days(period):
            return period
    return None


def latest_run(days: list[date], amounts: list[float]) -> tuple[int, int] | None:
    """The run the latest charges make, as (period, index of its first charge), or None when the last gap fits no
    period. `days` are sorted and distinct, `amounts` the charge of each. Going back from the last charge, a charge
    joins while its gap fits the last gap's period and the amounts stay similar; a skipped or late charge, an
    irregular one or an old price ends the run, so years of history still find today's subscription."""
    if len(days) < 2:
        return None
    period = period_of_gap((days[-1] - days[-2]).days)
    if period is None:
        return None
    start = len(days) - 2
    while start > 0 and period_of_gap((days[start] - days[start - 1]).days) == period and similar(amounts[start - 1 :]):
        start -= 1
    return period, start


def enough(period: int, count: int) -> bool:
    return count >= (MIN_CHARGES_LONG if period >= LONG_PERIOD else MIN_CHARGES)


def similar(amounts: list[float]) -> bool:
    """Every amount within AMOUNT_TOLERANCE of the median, or within AMOUNT_TOLERANCE_MIN of it."""
    if not amounts:
        return False
    usual = statistics.median(amounts)
    allowed = max(usual * AMOUNT_TOLERANCE, AMOUNT_TOLERANCE_MIN)
    return all(abs(a - usual) <= allowed + 1e-9 for a in amounts)


def next_charge(last: date, period: int) -> date:
    """The day the next charge is expected: the same day of the month `period` months on (or the month's last)."""
    year, month0 = divmod(last.year * 12 + last.month - 1 + period, 12)
    month = month0 + 1
    return date(year, month, min(last.day, calendar.monthrange(year, month)[1]))


def active(last: date, period: int, today: date) -> bool:
    """Still running: the last charge is no older than STALE_PERIODS periods plus the tolerance."""
    return (today - last).days <= period * DAYS_PER_MONTH * STALE_PERIODS + tolerance_days(period)


def detect(charges: list[tuple[date, float]], today: date) -> Rhythm | None:
    """The rhythm in a payee's latest charges (date, amount), or None when they don't make a running subscription.

    Charges on one day are one charge (a split bill); the amounts are compared as they are."""
    if not charges:
        return None
    by_day: dict[date, float] = {}
    for day, amount in charges:
        by_day[day] = by_day.get(day, 0.0) + amount
    days = sorted(by_day)
    amounts = [by_day[d] for d in days]
    found = latest_run(days, amounts)
    if found is None:
        return None
    period, start = found
    days, amounts = days[start:], amounts[start:]
    if not enough(period, len(days)) or not similar(amounts) or not active(days[-1], period, today):
        return None
    return Rhythm(period, float(statistics.median(amounts)), days[0], days[-1], len(days))
