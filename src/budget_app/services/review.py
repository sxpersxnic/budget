"""Year in review (F19): a year's income, spending by category, savings, wealth change and biggest payments, each
against the year before.

The figures are the twelve monthly statements added up (`aggregates`, per part like the statement, F11: a split
line counts in its own category, the share paid for others under *Advance payments*, its repayments as income),
so the year agrees with the Budget page month by month. Changes against the year before reuse F18's `Change`
(`domain.trends`). Wealth is the recorded month-end wealth (`services.wealth`), nothing is projected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from budget_app.domain import months, trends
from budget_app.domain import review as rules
from budget_app.domain.coicop import ADV, get_coicop
from budget_app.domain.models import TX_EXPENDITURE, Transaction
from budget_app.domain.review import Period
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.services.aggregates import (
    actual_expenditure,
    actual_income,
    part_value,
    rollup,
    split_lines,
    top_expenditure_codes,
    transaction_parts,
)
from budget_app.services.context import Context

TOTAL = "TOTAL"


@dataclass
class Figure:
    """One line of the review: this year's amount and the year before's, in the view currency."""

    key: str
    label: str
    amount: float = 0.0
    previous: float = 0.0

    @property
    def change(self) -> trends.Change:
        return trends.Change(self.amount, self.previous)

    @property
    def empty(self) -> bool:
        return abs(self.amount) < rules.EPS and abs(self.previous) < rules.EPS


@dataclass(frozen=True)
class BigExpense:
    """One of the year's biggest payments: the own share of an expenditure (F11), in the view currency."""

    transaction_id: str
    date: date
    name: str  # the payee, else the note
    category: str  # COICOP code or TAX
    amount: float


@dataclass(frozen=True)
class MonthFlow:
    month: str
    income: float
    expenditure: float

    @property
    def saved(self) -> float:
        return self.income - self.expenditure


@dataclass
class YearReview:
    period: Period
    currency: str
    income: Figure
    expenditure: Figure
    income_lines: list[Figure] = field(default_factory=list)
    expenditure_lines: list[Figure] = field(default_factory=list)  # divisions, taxes, advances with a value
    biggest: list[BigExpense] = field(default_factory=list)
    monthly: list[MonthFlow] = field(default_factory=list)
    wealth_start: float | None = None  # at the end of the month before the year
    wealth_end: float | None = None  # at the end of `wealth_month`
    wealth_month: str = ""  # the last complete month of the period with balances
    previous_wealth_change: float | None = None  # the year before's change, for the comparison

    @property
    def year(self) -> int:
        return self.period.year

    @property
    def saved(self) -> Figure:
        return Figure(
            TOTAL,
            "saved",
            self.income.amount - self.expenditure.amount,
            self.income.previous - self.expenditure.previous,
        )

    @property
    def savings_rate(self) -> float | None:
        return rules.savings_rate(self.income.amount, self.expenditure.amount)

    @property
    def previous_savings_rate(self) -> float | None:
        return rules.savings_rate(self.income.previous, self.expenditure.previous)

    @property
    def wealth_change(self) -> float | None:
        if self.wealth_start is None or self.wealth_end is None:
            return None
        return self.wealth_end - self.wealth_start

    @property
    def has_data(self) -> bool:
        """Anything recorded in the year: a transaction or a balance."""
        return not (self.income.amount < rules.EPS and self.expenditure.amount < rules.EPS and self.wealth_end is None)


# ------------------------------------------------------------------ the years
def _data_years(ctx: Context, today: date) -> set[int]:
    """The years up to this one with a transaction or a balance."""
    found = {tx.date.year for tx in ctx.store.transactions.all()}
    found |= {int(e.month[:4]) for e in ctx.store.account_entries.all()}
    return {y for y in found if y <= today.year}


def years(ctx: Context, today: date | None = None) -> list[int]:
    """The years to choose from, newest first: those with data, and this year (so far) in any case."""
    today = today or date.today()
    return sorted(_data_years(ctx, today) | {today.year}, reverse=True)


def default_year(ctx: Context, today: date | None = None) -> int:
    """Last year, the one a review is usually about, when it has data; else the latest year that has (or this one)."""
    today = today or date.today()
    found = _data_years(ctx, today)
    if today.year - 1 in found:
        return today.year - 1
    return max(found) if found else today.year


# ------------------------------------------------------------------ the review
def _totals(ctx: Context, p: Period, view_cur: str) -> tuple[dict[str, float], dict[str, float]]:
    """Rolled-up expenditure per code and income per key over the period's months."""
    mset = set(p.months)
    return rollup(actual_expenditure(ctx, mset, view_cur)), actual_income(ctx, mset, view_cur)


def _own_value(ctx: Context, tx: Transaction, lines, view_cur: str) -> float:
    """What a payment cost the user: every part but the share paid for others."""
    return sum(
        part_value(ctx, tx, part, view_cur) for part in transaction_parts(ctx, tx, lines) if part.category != ADV
    )


def biggest_expenses(ctx: Context, p: Period, view_cur: str, limit: int = rules.TOP_EXPENSES) -> list[BigExpense]:
    """The `limit` biggest payments of the period by their own share, biggest first (the earlier on a tie)."""
    lines = split_lines(ctx)
    first, last = months.first_day(p.first), months.last_day(p.last)
    found = [
        BigExpense(
            tx.id,
            tx.date,
            tx.payee or tx.comment,
            tx.category,
            _own_value(ctx, tx, lines.get(tx.id, []), view_cur),
        )
        for tx in ctx.store.transactions.all()
        if tx.type == TX_EXPENDITURE and first <= tx.date <= last
    ]
    found = [b for b in found if b.amount > rules.EPS]
    found.sort(key=lambda b: (-round(b.amount, 2), b.date, b.transaction_id))
    return found[:limit]


def _wealth_at(ctx: Context, month: str, view_cur: str) -> float | None:
    return wealth_svc.wealth(ctx, month, view_cur) if wealth_svc.has_wealth_data(ctx, month) else None


def _last_complete(p: Period, today: date) -> str | None:
    """The period's last month that is over, or None when the year has only just begun."""
    last = min(p.last, months.previous_month(today))
    return last if last >= p.first else None


def review(ctx: Context, year: int, view_cur: str, today: date | None = None) -> YearReview:
    today = today or date.today()
    p = rules.period(year, today)
    prev = p.previous()
    exp, inc = _totals(ctx, p, view_cur)
    pexp, pinc = _totals(ctx, prev, view_cur)
    top = top_expenditure_codes()
    coicop = get_coicop()
    out = YearReview(
        p,
        view_cur,
        Figure(TOTAL, "income", sum(inc.values()), sum(pinc.values())),
        Figure(TOTAL, "expenditure", sum(exp.get(c, 0.0) for c in top), sum(pexp.get(c, 0.0) for c in top)),
    )
    out.expenditure_lines = [
        line
        for code in top
        if not (line := Figure(code, coicop.label(code), exp.get(code, 0.0), pexp.get(code, 0.0))).empty
    ]
    names = reports.income_names(ctx)
    out.income_lines = sorted(
        (
            line
            for key in set(inc) | set(pinc)
            if not (line := Figure(key, names.get(key, key), inc.get(key, 0.0), pinc.get(key, 0.0))).empty
        ),
        key=lambda line: (-line.amount, line.label.lower()),
    )
    out.biggest = biggest_expenses(ctx, p, view_cur)
    for m in p.months:
        e, i = _totals(ctx, Period(year, m, m, True), view_cur)
        out.monthly.append(MonthFlow(m, sum(i.values()), sum(e.get(c, 0.0) for c in top)))
    out.wealth_start = _wealth_at(ctx, p.before, view_cur)
    if (last := _last_complete(p, today)) is not None:
        out.wealth_month = last
        out.wealth_end = _wealth_at(ctx, last, view_cur)
        before, after = _wealth_at(ctx, prev.before, view_cur), _wealth_at(ctx, months.add_months(last, -12), view_cur)
        if before is not None and after is not None:
            out.previous_wealth_change = after - before
    return out
