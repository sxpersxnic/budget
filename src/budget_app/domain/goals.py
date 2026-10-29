"""Savings goals (F14): progress, the monthly amount needed and the on-track line, as pure arithmetic.

Amounts are in the goal's currency; `services.goals` finds the current amount and the target and converts.

- **Whole months** to the target date: the months from today's month to the target's, one less when the target's
  day comes before today's (the last month is not complete), and at least 1 while the date is today or later.
- **Monthly amount needed:** what is left divided by those months, rounded *up* to the cent, so saving it every
  month reaches the target. None without a target date, 0 once reached.
- **On track:** the current amount is at least the straight line from the start (the day the goal began and what
  it held then) to the target on the target date, read at today. A goal without a date has no line.
- **Reserve** (`planned`): what the goal is to hold `n` months from now on its plan: today's amount plus the
  monthly amount needed for each month, and the full target from the month the target is reached on that plan
  (at the latest the target date's month); without a date, today's amount. Never more than the target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

REACHED = "reached"
OVERDUE = "overdue"
ON_TRACK = "on_track"
BEHIND = "behind"
NO_DATE = "no_date"
NO_TARGET = "no_target"  # a target of 0, such as months of spending before any spending is recorded

EPS = 0.005


def whole_months(today: date, due: date) -> int:
    """Whole months from `today` to `due`; at least 1 while `due` is today or later, 0 once it has passed."""
    if due < today:
        return 0
    n = (due.year - today.year) * 12 + due.month - today.month
    if due.day < today.day:
        n -= 1
    return max(n, 1)


def monthly_needed(remaining: float, n_months: int) -> float:
    """`remaining` spread over `n_months`, rounded up to the cent (0 when nothing is left)."""
    if remaining <= EPS:
        return 0.0
    return math.ceil(round(remaining / max(n_months, 1) * 100, 6)) / 100


def expected(start: date, start_amount: float, due: date, target: float, today: date) -> float:
    """Where the straight line from (`start`, `start_amount`) to (`due`, `target`) is on `today`."""
    total = (due - start).days
    if total <= 0 or today >= due:
        return target
    share = min(max((today - start).days / total, 0.0), 1.0)
    return start_amount + (target - start_amount) * share


@dataclass(frozen=True)
class Progress:
    current: float
    target: float
    status: str
    months_left: int | None = None  # whole months to the target date; None without one
    needed: float | None = None  # a month, to reach the target on time; None without a date or once overdue
    expected: float | None = None  # where the on-track line is today
    due_in: int | None = None  # months from today's month to the target's month

    @property
    def remaining(self) -> float:
        return max(self.target - self.current, 0.0)

    @property
    def ratio(self) -> float:
        """Share of the target saved, between 0 and 1."""
        if self.target <= EPS:
            return 0.0
        return min(max(self.current / self.target, 0.0), 1.0)

    @property
    def percent(self) -> int:
        """Whole percent saved, never shown as 100 before the target is reached."""
        if self.target <= EPS:
            return 0
        pct = math.floor(max(self.current, 0.0) / self.target * 100)
        return pct if self.status == REACHED else min(pct, 99)


def progress(
    current: float,
    target: float,
    today: date,
    due: date | None = None,
    start: date | None = None,
    start_amount: float = 0.0,
) -> Progress:
    current, target = round(float(current), 2), round(float(target), 2)
    if target <= EPS:
        return Progress(current, 0.0, NO_TARGET)
    if current >= target - EPS:
        return Progress(current, target, REACHED, whole_months(today, due) if due else None, 0.0 if due else None)
    if due is None:
        return Progress(current, target, NO_DATE)
    if due < today:
        return Progress(current, target, OVERDUE, 0, None, target)
    n = whole_months(today, due)
    line = round(expected(start or today, start_amount, due, target, today), 2)
    status = ON_TRACK if current >= line - EPS else BEHIND
    due_in = (due.year - today.year) * 12 + due.month - today.month
    return Progress(current, target, status, n, monthly_needed(target - current, n), line, due_in)


def planned(p: Progress, n: int) -> float:
    """What the goal is to hold `n` months from now on its plan (0 for now; earlier months count as now)."""
    if p.status == NO_TARGET:
        return 0.0
    if p.status in (REACHED, OVERDUE):
        return p.target
    current = max(p.current, 0.0)
    if p.needed is None or p.months_left is None or p.due_in is None:  # no date: what it holds now
        return min(current, p.target)
    if n >= min(p.months_left, p.due_in):
        return p.target
    return min(current + p.needed * max(n, 0), p.target)
