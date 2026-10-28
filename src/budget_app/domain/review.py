"""F19, pure. The period a year in review covers, and the arithmetic of its figures.

A past year is January to December. The current year is reviewed *so far*: January to the current month, and
compared with the same months of the year before, so the comparison stays fair in October. A later year has no
review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from budget_app.domain import months

EPS = 0.005
TOP_EXPENSES = 5  # the biggest single payments listed


@dataclass(frozen=True)
class Period:
    year: int
    first: str  # the first month counted (January)
    last: str  # the last month counted (December, or the current month of the current year)
    complete: bool  # the year lies entirely in the past

    @property
    def months(self) -> list[str]:
        return months.month_range(self.first, self.last)

    @property
    def count(self) -> int:
        return len(self.months)

    @property
    def before(self) -> str:
        """The month before the period: wealth at its end is where the year starts."""
        return months.add_months(self.first, -1)

    def previous(self) -> Period:
        """The same months a year earlier."""
        return Period(self.year - 1, months.add_months(self.first, -12), months.add_months(self.last, -12), True)


def period(year: int, today: date) -> Period:
    """What a review of `year` covers on `today`."""
    if year > today.year:
        raise ValueError(f"{year} has not started")
    first = f"{year:04d}-01"
    if year < today.year:
        return Period(year, first, f"{year:04d}-12", True)
    return Period(year, first, months.current_month(today), False)


def savings_rate(income: float, expenditure: float) -> float | None:
    """(income − expenditure) / income; None without income, since a rate of nothing says nothing."""
    if income <= EPS:
        return None
    return (income - expenditure) / income


def share(value: float, total: float) -> float:
    """A part of a total, 0 when the total is nothing (or negative, which a sum of spending isn't)."""
    if total <= EPS:
        return 0.0
    return value / total
