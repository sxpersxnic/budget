"""Loans and mortgages (F22), pure: the interest of a payment period, the planned payments of a debt, and when a loan
is paid off. Nothing here reads records; `services.loans` finds the debt and books the payments.

- **Interest** for one period is simple interest on the outstanding debt: debt × rate % × months ÷ 12 (the 30/360
  convention Swiss banks quote mortgages in), rounded half up to the cent. A bank may round a few rappen
  differently; the amount is edited before booking, like a recurring transaction's.
- A **payment** pays the period's interest and `amortization` off the debt (the last one only what is left). A loan
  without amortization (an interest-only mortgage) never ends on its own.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

EPS = 0.005
# COICOP 2018 "Financial intermediation services indirectly measured": the lender's interest margin, the one code
# the tax summary counts as debt interest (F20); interest itself is no consumption, so it is the nearest fit.
INTEREST_CATEGORY = "12.2.1"
PREVIEW = 12  # payments the page shows ahead
MAX_PAYMENTS = 1200  # a century of monthly payments: where the schedule of a loan that never amortizes stops


def interest(debt: float, rate: float, recur_months: int) -> float:
    """Simple interest for one payment period: debt × rate % × months ÷ 12, half up to the cent; 0 without debt."""
    if debt <= EPS or rate <= 0 or recur_months <= 0:
        return 0.0
    amount = Decimal(str(debt)) * Decimal(str(rate)) / Decimal(100) * Decimal(recur_months) / Decimal(12)
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def occurrence(first: date, recur_months: int, k: int) -> date:
    """The k-th payment date (0 = first): the first date's day, or the month's last day if it is shorter (the same
    rule as recurring transactions, §4.5)."""
    year, month0 = divmod(first.year * 12 + first.month - 1 + k * recur_months, 12)
    month = month0 + 1
    return date(year, month, min(first.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Payment:
    """One planned payment: the interest for the period, the repayment, and the debt left after it."""

    date: date
    interest: float
    amortization: float
    debt_after: float

    @property
    def total(self) -> float:
        return round(self.interest + self.amortization, 2)


def schedule(
    debt: float,
    rate: float,
    recur_months: int,
    amortization: float,
    first: date,
    limit: int = MAX_PAYMENTS,
    until: date | None = None,
) -> list[Payment]:
    """The payments from `first` on while there is debt, every `recur_months` months: each pays the period's
    interest on the debt before it and `amortization` off it (the last one what is left). At most `limit`
    payments, and none after `until`."""
    out: list[Payment] = []
    debt = round(debt, 2)
    k = 0
    while debt > EPS and len(out) < limit:
        day = occurrence(first, recur_months, k)
        if until is not None and day > until:
            break
        repay = round(min(amortization, debt), 2) if amortization > EPS else 0.0
        out.append(Payment(day, interest(debt, rate, recur_months), repay, round(debt - repay, 2)))
        debt = round(debt - repay, 2)
        k += 1
    return out


def payoff(payments: list[Payment]) -> date | None:
    """The date of the payment that clears the debt, if the schedule reaches it."""
    if payments and payments[-1].debt_after <= EPS:
        return payments[-1].date
    return None


def interest_total(payments: list[Payment]) -> float:
    return round(sum(p.interest for p in payments), 2)


def repaid_share(principal: float, debt: float) -> float:
    """How much of the original debt is paid off, 0 to 1; 0 without a principal."""
    if principal <= EPS:
        return 0.0
    return min(max((principal - debt) / principal, 0.0), 1.0)
