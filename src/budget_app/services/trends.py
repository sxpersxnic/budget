"""Trends and unusual spending (F18): changes against another month, and the Overview's *Notable this month*.

Both count expenditure the way the statement does: per part (F11, `aggregates.transaction_parts`), so a split line
counts in its own category, and the share paid for others (*Advance payments*) never counts as spending. Amounts
are in the view currency; the thresholds of `domain.trends` are in CHF and converted at the latest rate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain import trends as rules
from budget_app.domain.coicop import ADV, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, TX_EXPENDITURE, Transaction
from budget_app.services import reports
from budget_app.services.aggregates import (
    actual_expenditure,
    actual_income,
    budgeted,
    part_value,
    rollup,
    split_lines,
    top_expenditure_codes,
    transaction_parts,
)
from budget_app.services.context import Context

COMPARE_BUDGET, COMPARE_MONTH, COMPARE_YEAR = "budget", "month", "year"
COMPARE_MODES = (COMPARE_BUDGET, COMPARE_MONTH, COMPARE_YEAR)


# ------------------------------------------------------------------ the Budget page's comparison
def reference_month(month: str, mode: str) -> str | None:
    """The month a ledger compares with: the one before, the same month a year earlier, or none (the budget)."""
    if mode == COMPARE_MONTH:
        return months.add_months(month, -1)
    if mode == COMPARE_YEAR:
        return months.add_months(month, -12)
    return None


@dataclass
class Comparison:
    """What was recorded in the reference month, per expenditure code (rolled up) and per income key."""

    month: str
    expenditure: dict[str, float] = field(default_factory=dict)
    income: dict[str, float] = field(default_factory=dict)

    @property
    def total_expenditure(self) -> float:
        return sum(self.expenditure.get(c, 0.0) for c in top_expenditure_codes())

    @property
    def total_income(self) -> float:
        return sum(self.income.values())

    def change(self, line: reports.Line, income: bool = False) -> rules.Change:
        """A ledger line against the same line in the reference month."""
        source = self.income if income else self.expenditure
        if line.key == "TOTAL":
            reference = self.total_income if income else self.total_expenditure
        else:
            reference = source.get(line.key, 0.0)
        return rules.Change(line.actual, reference)


def compare(ctx: Context, rep: reports.MonthReport, mode: str) -> Comparison | None:
    """The reference month's amounts for `rep`, in its currency; None when comparing with the budget.

    Categories recorded only in the reference month get a line of their own in `rep` (nothing this month, no
    budget), so a category that stopped shows as a drop rather than disappearing."""
    ref = reference_month(rep.month, mode)
    if ref is None:
        return None
    mset = {ref}
    out = Comparison(ref, rollup(actual_expenditure(ctx, mset, rep.currency)), actual_income(ctx, mset, rep.currency))
    coicop = get_coicop()
    for code, value in out.expenditure.items():
        if code not in rep.expenditure_tree and abs(value) >= rules.EPS:
            cat = coicop.get(code)
            rep.expenditure_tree[code] = reports.Line(
                code,
                cat.label if cat else code,
                level=cat.level if cat else 1,
                has_children=bool(cat and cat.children),
                editable=code != ADV,
            )
    shown = {line.key for line in rep.income_lines}
    names = reports.income_names(ctx)
    added = [
        reports.Line(key, names.get(key, key), editable=key != reports.ADV_REPAY)
        for key, value in out.income.items()
        if key not in shown and abs(value) >= rules.EPS
    ]
    if added:
        rep.income_lines = sorted([*rep.income_lines, *added], key=lambda line: line.label.lower())
    return out


# ------------------------------------------------------------------ notable this month (Overview)
OVER_BUDGET, LARGE, ABOVE_USUAL, PACE = "over_budget", "large", "above_usual", "pace"
ORDER = (OVER_BUDGET, LARGE, ABOVE_USUAL, PACE)  # the card lists exceeded budgets first


@dataclass(frozen=True)
class Item:
    """One notable thing. `category` is a COICOP code (or TAX); amounts are in the view currency.

    over_budget: `amount` spent of the `reference` budget. pace: `amount` spent so far, `projected` at this pace,
    of the `reference` budget. above_usual: `amount` this month, `reference` the usual (median) month. large:
    `amount` of one payment (the part of it in `category`), `reference` a usual month's spending, `day` its date and
    `name` its payee (or note)."""

    kind: str
    category: str
    amount: float
    reference: float
    projected: float = 0.0
    day: date | None = None
    name: str = ""


@dataclass
class Notable:
    month: str
    currency: str
    items: list[Item] = field(default_factory=list)
    history_months: int = 0  # recorded complete months the usual level is computed from
    has_data: bool = False  # any expenditure recorded at all

    @property
    def short_history(self) -> bool:
        return self.history_months < rules.MIN_HISTORY_MONTHS


def group_of(code: str) -> str:
    """The COICOP group (level 2) a code belongs to; a division, Taxes or an unknown code stands for itself."""
    chain = get_coicop().ancestors(code)
    return chain[-2] if len(chain) >= 2 else chain[0]


def _related(a: str, b: str) -> bool:
    """Whether one code is the other or one of its ancestors."""
    coicop = get_coicop()
    return a in coicop.ancestors(b) or b in coicop.ancestors(a)


def notable(ctx: Context, view_cur: str, today: date | None = None) -> Notable:
    """What stands out in the current month so far; the rules and thresholds are in `domain.trends`."""
    today = today or date.today()
    month = months.current_month(today)
    out = Notable(month, view_cur)
    expenditure = [tx for tx in ctx.store.transactions.all() if tx.type == TX_EXPENDITURE]
    if not expenditure:
        return out
    out.has_data = True
    first = min(months.month_of(tx.date) for tx in expenditure)
    history = [
        m
        for m in months.month_range(months.add_months(month, -rules.HISTORY_MONTHS), months.add_months(month, -1))
        if m >= first
    ]
    out.history_months = len(history)

    def chf(amount: float) -> float:  # a threshold in the view currency, at the latest rate like budgets
        return ctx.convert_on(amount, BASE_CURRENCY, view_cur, None)

    # one pass: each payment's parts per COICOP group (F11), in the months that count
    wanted = {month, *history}
    lines = split_lines(ctx)
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    samples: dict[str, list[float]] = defaultdict(list)  # payments per group in the history
    current: list[tuple[Transaction, str, float]] = []  # this month's payments per group
    payments: dict[str, int] = defaultdict(int)  # this month: payments per code and every ancestor
    for tx in expenditure:
        m = months.month_of(tx.date)
        if m not in wanted:
            continue
        pieces: dict[str, float] = defaultdict(float)
        codes: set[str] = set()
        for part in transaction_parts(ctx, tx, lines.get(tx.id, [])):
            if part.category == ADV:  # paid for others: it comes back, so it isn't spending
                continue
            pieces[group_of(part.category)] += part_value(ctx, tx, part, view_cur)
            codes.update(get_coicop().ancestors(part.category))
        for group, value in pieces.items():
            by_month[m][group] += value
            if m == month:
                current.append((tx, group, value))
            else:
                samples[group].append(value)
        if m == month:
            for code in codes:
                payments[code] += 1

    items: list[Item] = []
    # budgets: exceeded, or about to be at this month's pace (any code with a budget of its own)
    mset = {month}
    spent = rollup(actual_expenditure(ctx, mset, view_cur))
    direct = budgeted(ctx, mset, BUDGET_EXPENDITURE, view_cur)
    budget = rollup(direct)
    day, days = today.day, months.last_day(month).day
    min_over = chf(rules.OVER_BUDGET_MIN_CHF)
    for code in sorted(c for c, v in direct.items() if v > rules.EPS and c != ADV):
        s, b = spent.get(code, 0.0), budget[code]
        if rules.exceeded(s, b, min_over):
            items.append(Item(OVER_BUDGET, code, s, b))
        elif (projected := rules.about_to_exceed(s, b, day, days, payments.get(code, 0), min_over)) is not None:
            items.append(Item(PACE, code, s, b, projected))
    # of a budget and the budgets inside it, the most specific one that stands out says it best
    flagged = {i.category for i in items}
    coicop = get_coicop()
    items = [i for i in items if not any(c != i.category and i.category in coicop.ancestors(c) for c in flagged)]

    if not out.short_history:
        usual_month = rules.median([sum(by_month[m].values()) for m in history])
        min_large = chf(rules.LARGE_MIN_CHF)
        large = [
            Item(LARGE, group, value, usual_month, day=tx.date, name=tx.payee or tx.comment)
            for tx, group, value in current
            if rules.is_large(value, usual_month, samples[group], min_large)
        ]
        min_above = chf(rules.ABOVE_USUAL_MIN_CHF)
        for group, actual in by_month[month].items():
            usual = rules.median([by_month[m].get(group, 0.0) for m in history])
            if not rules.above_usual(actual, usual, min_above):
                continue
            if budget.get(group, 0.0) >= actual - rules.EPS:  # planned: the budget covers it
                continue
            if any(_related(group, i.category) for i in items):  # already listed for its budget
                continue
            if sum(i.amount for i in large if i.category == group) >= (actual - usual) / 2:  # one payment explains it
                continue
            items.append(Item(ABOVE_USUAL, group, actual, usual))
        exceeded = [i.category for i in items if i.kind == OVER_BUDGET]  # the budget says it first, and links there
        items += [i for i in large if not any(_related(i.category, code) for code in exceeded)]

    def weight(item: Item) -> float:  # within a kind, the biggest first
        if item.kind == PACE:
            return item.projected - item.reference
        return item.amount if item.kind == LARGE else item.amount - item.reference

    items.sort(key=lambda i: (ORDER.index(i.kind), -weight(i)))
    out.items = items[: rules.MAX_ITEMS]
    return out
