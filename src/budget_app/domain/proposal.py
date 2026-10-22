"""Budget from history (F16), pure: which months count, and how a category's history becomes a monthly amount.

**The rule** (architecture §4.4):

* The *basis* is the last 3, 6 or 12 complete months, the newest being the month before the current one. Months
  before the first record are left out, so a history that starts later counts only the months since it started.
* Every basis month counts, a month without spending in a category as 0, so a category spent in one month of six
  gets a sixth of it on average (and 0 as its median).
* The *statistic* is the average or the median of those values; the median of an even number of months is the
  average of the middle two.
* The proposal is the statistic rounded to the nearest multiple of the rounding step (half up), and never below 0
  (a month of refunds can make a category negative).
* COICOP codes are counted at the level the user chooses (division, group or class): a code deeper than the level
  counts in its ancestor at that level, a code at or above the level stays where it is, and *Taxes* stays itself.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from budget_app.domain import months
from budget_app.domain.coicop import get_coicop

AVERAGE = "average"
MEDIAN = "median"
METHODS = (AVERAGE, MEDIAN)
BASES = (3, 6, 12)  # months of history the user can choose
LEVELS = (1, 2, 3)  # COICOP division, group, class
STEPS = (0.01, 1.0, 5.0, 10.0, 50.0, 100.0)  # rounding steps in CHF


def basis_months(current: str, n: int, first: str | None) -> list[str]:
    """The `n` complete months before `current`, oldest first, without the months before `first`.

    `first` is the month of the first record; None (no records) leaves nothing to count.
    """
    if first is None or n < 1:
        return []
    start = max(months.add_months(current, -n), first)
    return months.month_range(start, months.add_months(current, -1))


def at_level(code: str, level: int) -> str:
    """The code a category counts in at a COICOP level: its ancestor at that level, or itself when it is not
    deeper (a division at group level, *Taxes*, an unknown code)."""
    for anc in get_coicop().ancestors(code):
        cat = get_coicop().get(anc)
        if cat is not None and cat.level == level:
            return anc
    return code


def by_level(amounts: dict[str, float], level: int) -> dict[str, float]:
    """`amounts` per code summed into the codes they count in at `level` (`at_level`)."""
    out: dict[str, float] = defaultdict(float)
    for code, value in amounts.items():
        out[at_level(code, level)] += value
    return dict(out)


def statistic(values: Sequence[float], method: str) -> float:
    """The average or the median of `values` (0 for none)."""
    if not values:
        return 0.0
    if method == MEDIAN:
        return float(median(values))
    if method == AVERAGE:
        return sum(values) / len(values)
    raise ValueError(f"unknown method {method!r}")


def round_to(value: float, step: float) -> float:
    """`value` rounded to the nearest multiple of `step`, halves away from zero (12.5 → 13 with step 1)."""
    dstep = Decimal(str(step))
    quotient = (Decimal(str(value)) / dstep).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return float(quotient * dstep)


def propose(values: Sequence[float], method: str, step: float) -> float:
    """The monthly amount proposed for a category with these monthly `values`: rounded, never negative."""
    return max(0.0, round_to(statistic(values, method), step))


def has_activity(values: Iterable[float]) -> bool:
    """Whether a history has any month with money in it (a cent or more either way)."""
    return any(abs(v) >= 0.005 for v in values)
