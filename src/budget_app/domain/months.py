"""Helpers for months represented as 'YYYY-MM' strings."""

from __future__ import annotations

import calendar
from datetime import date


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse(month: str) -> tuple[int, int]:
    year, mon = month.split("-")
    return int(year), int(mon)


def add_months(month: str, n: int) -> str:
    year, mon = parse(month)
    idx = year * 12 + (mon - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_range(start: str, end: str, step: int = 1) -> list[str]:
    """Inclusive list of months from start to end, every `step` months."""
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, step)
    return out


def current_month(today: date | None = None) -> str:
    return month_of(today or date.today())


def previous_month(today: date | None = None) -> str:
    return add_months(current_month(today), -1)


def is_complete(month: str, today: date | None = None) -> bool:
    """A month is complete once it lies entirely in the past."""
    return month < current_month(today)


def first_day(month: str) -> date:
    year, mon = parse(month)
    return date(year, mon, 1)


def last_day(month: str) -> date:
    year, mon = parse(month)
    return date(year, mon, calendar.monthrange(year, mon)[1])


def label(month: str) -> str:
    year, mon = parse(month)
    return f"{calendar.month_abbr[mon]} {year}"
