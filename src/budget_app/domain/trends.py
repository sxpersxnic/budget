"""F18, pure. Changes against another month, and the rules and thresholds of "Notable this month".

Every threshold of the Overview's *Notable this month* card is defined here, in one place (architecture §7,
decision 23). Amounts are in CHF and converted to the display currency by the caller, at the latest rate like
budget entries, so a threshold means the same in every view currency.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

EPS = 0.005

# ------------------------------------------------------------------ thresholds of "Notable this month"
HISTORY_MONTHS = 6  # the usual level: the median of the 6 complete months before this one
MIN_HISTORY_MONTHS = 3  # fewer recorded months than this: no comparison with the usual level
ABOVE_USUAL_RATIO = 1.5  # a category is well above its usual level from 50 % above the median …
ABOVE_USUAL_MIN_CHF = 100.0  # … and at least 100 CHF above it, so small categories don't make the list
LARGE_MIN_CHF = 250.0  # a single payment is large only from 250 CHF, and then …
LARGE_MIN_SAMPLES = 3  # … in a category with at least 3 earlier payments (its usual size is known) …
LARGE_TIMES_USUAL = 4.0  # … from four times their median (so the rent is never large),
LARGE_SHARE_OF_MONTH = 0.25  # … elsewhere from a quarter of a usual month's spending
OVER_BUDGET_MIN_CHF = 10.0  # a budget counts as exceeded once the spending is 10 CHF above it
PACE_MIN_DAY = 7  # the pace projection starts on the 7th, when a week of spending shows a pace
PACE_MIN_PAYMENTS = 3  # and only for categories paid 3 times or more this month (not rent paid once)
MAX_ITEMS = 5  # the card lists at most this many


# ------------------------------------------------------------------ changes (Budget page)
@dataclass(frozen=True)
class Change:
    """`actual` against `reference`: the difference and, when the reference isn't zero, the ratio of it."""

    actual: float
    reference: float

    @property
    def delta(self) -> float:
        return self.actual - self.reference

    @property
    def ratio(self) -> float | None:
        """(actual − reference) / |reference|; None without a reference to compare with."""
        if abs(self.reference) < EPS:
            return None
        return self.delta / abs(self.reference)

    @property
    def empty(self) -> bool:
        return abs(self.actual) < EPS and abs(self.reference) < EPS


def percent(ratio: float) -> str:
    """0.123 -> '12 %'; very large changes are capped, since '+2'350 %' says no more than '>999 %'."""
    value = abs(ratio) * 100
    return ">999 %" if value > 999 else f"{value:.0f} %"


# ------------------------------------------------------------------ rules (Overview)
def median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def above_usual(actual: float, usual: float, min_above: float) -> bool:
    """Well above the usual level: ABOVE_USUAL_RATIO times it, and at least `min_above` more."""
    return actual > usual * ABOVE_USUAL_RATIO + EPS and actual - usual >= min_above - EPS


def is_large(value: float, usual_month: float, samples: list[float], min_value: float) -> bool:
    """A large single payment: at least `min_value`, and far above the usual size of a payment in its category
    (the median of the earlier payments `samples`) when there are enough of them to know it, else a big share of a
    usual month's spending. A category paid regularly decides by itself: the rent is big but never unusual."""
    if value < min_value - EPS:
        return False
    if len(samples) >= LARGE_MIN_SAMPLES:
        return value >= median(samples) * LARGE_TIMES_USUAL - EPS
    return usual_month > EPS and value >= usual_month * LARGE_SHARE_OF_MONTH - EPS


def exceeded(spent: float, budget: float, min_over: float) -> bool:
    return budget > EPS and spent - budget >= min_over - EPS


def pace(spent: float, day: int, days: int) -> float:
    """What a month ends with at the pace so far: spent / day × days."""
    return spent / max(day, 1) * days


def about_to_exceed(spent: float, budget: float, day: int, days: int, payments: int, min_over: float) -> float | None:
    """The projected spending when the pace so far exceeds the budget by `min_over` or more, else None.

    Only from PACE_MIN_DAY on, for categories with PACE_MIN_PAYMENTS payments or more, and while the budget isn't
    used up yet (a budget already exceeded is a different item; one paid in full at once isn't a pace)."""
    if budget <= EPS or day < PACE_MIN_DAY or payments < PACE_MIN_PAYMENTS or spent >= budget - EPS:
        return None
    projected = pace(spent, day, days)
    return projected if projected - budget >= min_over - EPS else None
