"""Budget from history (F16): a monthly budget per category proposed from the last complete months.

`propose` reads what was spent and received in the basis months and returns one `Row` per category: its history,
the proposed monthly amount (`domain.proposal`), the budget the target months already have, budgets of related
categories they overlap with, and the recurring series (F05) that book in the category. `create` turns the rows
the user ticked into budget series, all in one undoable action.

**Counted** (architecture §4.4): amounts in CHF, the base currency, each transaction at its own rate
(`amount_chf`), so the proposal does not move with the display currency or with today's rate. Expenditure counts
per part (F11): a split line in its own category, the rest in the transaction's; the share paid for others
(*Advance payments*) is left out, and so are advance repayments on the income side. Transfers, exchanges and
revaluations are neither spending nor income.

**Existing budgets:** a row whose category already has a budget in the target months is a *conflict*: it starts
unticked, and ticking it replaces that budget in those months (the New Budget Entry form's *Replace*). A budget
for a category inside or above the row's (01.1.1 under 01.1) *overlaps*: it would be counted with the new one, so
the row starts unticked too, but ticking it adds alongside and leaves the other budget as it is.

**Recurring transactions** are bookings like any other, so they are part of the history; their rows are flagged
so the user sees why an amount is what it is. The projection does not use series (F05), so a budget beside a
series counts once.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months, proposal
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import (
    BUDGET_EXPENDITURE,
    BUDGET_INCOME,
    TX_EXPENDITURE,
    TX_INCOME,
    BudgetEntry,
    Recurring,
)
from budget_app.i18n import N_, t
from budget_app.services import aggregates
from budget_app.services import budget as budget_svc
from budget_app.services import recurring as rec_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

TX_TYPE = {BUDGET_EXPENDITURE: TX_EXPENDITURE, BUDGET_INCOME: TX_INCOME}


@dataclass
class Options:
    basis: int = 6  # months of history (proposal.BASES)
    method: str = proposal.AVERAGE
    level: int = 2  # COICOP level of the expenditure rows (proposal.LEVELS)
    step: float = 10.0  # rounding, in CHF
    start_month: str = ""
    end_month: str = ""  # ignored when forever
    forever: bool = True


@dataclass
class Budgeted:
    """A budget the target months already have for one category: in how many of them, and the monthly amounts."""

    category: str
    months: int
    low: float  # CHF per month, at the latest rate like every budget display
    high: float


@dataclass
class Row:
    type: str  # BUDGET_EXPENDITURE or BUDGET_INCOME
    category: str
    history: list[float]  # CHF per basis month, oldest first
    proposed: float
    existing: Budgeted | None = None  # the same category's budget in the target months: ticking replaces it
    overlaps: list[Budgeted] = field(default_factory=list)  # budgets of categories inside or above this one
    recurring: list[Recurring] = field(default_factory=list)  # running series that book in this category

    @property
    def key(self) -> str:
        return f"{self.type}:{self.category}"

    @property
    def conflict(self) -> bool:
        return self.existing is not None

    @property
    def ticked(self) -> bool:
        """Whether the row starts ticked: something to propose and no budget in the way."""
        return self.proposed > 0 and not self.conflict and not self.overlaps


@dataclass
class Proposal:
    basis: list[str]  # the months counted, oldest first
    first: str | None  # the month of the first record
    target: list[str]  # the months the budget would cover ([] when the period is invalid)
    target_error: str  # why the period is invalid, or ""
    rows: list[Row]
    wanted: int = 0  # the months the user chose to count

    @property
    def partial(self) -> bool:
        """Whether the records start inside the chosen basis, so fewer months count."""
        return 0 < len(self.basis) < self.wanted


@dataclass
class Choice:
    """A ticked row: the category, the monthly amount (as edited) and whether it replaces an existing budget."""

    type: str
    category: str
    amount: float | None
    replace: bool = False


# ------------------------------------------------------------------ reading the history
def first_record(ctx: Context) -> str | None:
    """The month of the earliest expenditure or income, or None without any."""
    dates = [tx.date for tx in ctx.store.transactions.all() if tx.type in (TX_EXPENDITURE, TX_INCOME)]
    return months.month_of(min(dates)) if dates else None


def history(ctx: Context, basis: list[str], level: int) -> dict[tuple[str, str], list[float]]:
    """CHF per (type, category) and basis month: expenditure per part at `level` without advance shares, income
    without repayments."""
    index = {m: i for i, m in enumerate(basis)}
    lines = aggregates.split_lines(ctx)
    out: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0] * len(basis))
    for tx in ctx.store.transactions.all():
        i = index.get(months.month_of(tx.date))
        if i is None:
            continue
        if tx.type == TX_EXPENDITURE:
            for code, value in aggregates.expenditure_parts(ctx, tx, BASE_CURRENCY, lines.get(tx.id, [])):
                if code != ADV:
                    out[(BUDGET_EXPENDITURE, proposal.at_level(code, level))][i] += value
        elif tx.type == TX_INCOME and not tx.is_advance:
            out[(BUDGET_INCOME, tx.category)][i] += aggregates.tx_value(ctx, tx, BASE_CURRENCY)
    return {key: [round(v, 2) for v in values] for key, values in out.items()}


def _budgetable(ctx: Context, type_: str, code: str) -> bool:
    if type_ == BUDGET_INCOME:
        return ctx.store.income_categories.get(code) is not None
    return code == TAX or (code != ADV and get_coicop().get(code) is not None)


def _order(ctx: Context):
    position = {code: i for i, code in enumerate(get_coicop().categories)}

    def key(row: Row) -> tuple:
        if row.type == BUDGET_EXPENDITURE:
            return (0, position.get(row.category, len(position)), "")
        cat = ctx.store.income_categories.get(row.category)
        return (1, 0, (cat.name if cat else row.category).casefold())

    return key


def _summary(category: str, entries: list[BudgetEntry], ctx: Context) -> Budgeted:
    per_month: dict[str, float] = defaultdict(float)
    for b in entries:
        per_month[b.month] += ctx.convert_on(b.amount, b.currency, BASE_CURRENCY, None)
    values = [round(v, 2) for v in per_month.values()]
    return Budgeted(category, len(per_month), min(values), max(values))


def _related(a: str, b: str) -> bool:
    """Whether one COICOP code lies inside the other (and they differ)."""
    coicop = get_coicop()
    return a != b and (a in coicop.ancestors(b) or b in coicop.ancestors(a))


def target_months(ctx: Context, opts: Options) -> list[str]:
    """The months a budget from `opts` covers, every month; ValidationError like the New Budget Entry form."""
    return budget_svc.plan_months(
        ctx, BudgetInput(start_month=opts.start_month, end_month=opts.end_month, forever=opts.forever)
    )


def propose(ctx: Context, opts: Options, today: date | None = None) -> Proposal:
    """One row per category with money in the basis months, expenditure in COICOP order (Taxes last), then income
    by name. Writes nothing."""
    first = first_record(ctx)
    basis = proposal.basis_months(months.current_month(today), opts.basis, first)
    try:
        target, target_error = target_months(ctx, opts), ""
    except ValidationError as exc:
        target, target_error = [], str(exc)
    in_target = set(target)
    planned = [b for b in ctx.store.budget.all() if b.month in in_target]
    running = [s for s in ctx.store.recurring.all() if not rec_svc.ended(s)]
    rows = []
    for (type_, code), values in history(ctx, basis, opts.level).items():
        if not proposal.has_activity(values) or not _budgetable(ctx, type_, code):
            continue
        row = Row(type_, code, values, proposal.propose(values, opts.method, opts.step))
        same = [b for b in planned if b.type == type_ and b.category == code]
        if same:
            row.existing = _summary(code, same, ctx)
        if type_ == BUDGET_EXPENDITURE:
            related: dict[str, list[BudgetEntry]] = defaultdict(list)
            for b in planned:
                if b.type == type_ and _related(b.category, code):
                    related[b.category].append(b)
            row.overlaps = [_summary(c, related[c], ctx) for c in sorted(related)]
        row.recurring = [
            s
            for s in running
            if s.type == TX_TYPE[type_]
            and (proposal.at_level(s.category, opts.level) if type_ == BUDGET_EXPENDITURE else s.category) == code
        ]
        rows.append(row)
    rows.sort(key=_order(ctx))
    return Proposal(basis, first, target, target_error, rows, opts.basis)


# ------------------------------------------------------------------ creating
def category_label(ctx: Context, type_: str, code: str) -> str:
    if type_ == BUDGET_INCOME:
        cat = ctx.store.income_categories.get(code)
        return cat.name if cat else code
    return get_coicop().label(code)


def note(method: str, basis: list[str]) -> str:
    """The note the created entries carry: how they were proposed ("From history: average of Apr – Sep 2026")."""
    span = months.label(basis[0]) + (f" – {months.label(basis[-1])}" if len(basis) > 1 else "") if basis else ""
    if method == proposal.MEDIAN:
        return t("From history: median of {span}", span=span)
    return t("From history: average of {span}", span=span)


@recorded(N_("Add budget entries from history"))
def create(ctx: Context, opts: Options, choices: list[Choice], comment: str = "") -> list[BudgetEntry]:
    """A monthly CHF series per choice over the target months, all or nothing; a choice with `replace` takes the
    place of its category's budget in those months."""
    if not choices:
        raise ValidationError(t("Tick at least one category"))
    target_months(ctx, opts)  # a period that doesn't work is no fault of a category
    inputs = []
    for c in choices:
        inp = BudgetInput(
            start_month=opts.start_month,
            end_month=opts.end_month,
            forever=opts.forever,
            type=c.type,
            currency=BASE_CURRENCY,
            amount=c.amount,
            category=c.category,
            overwrite=c.replace,
            comment=comment,
        )
        try:
            budget_svc.validate(ctx, inp)
        except ValidationError as exc:
            raise ValidationError(f"{category_label(ctx, c.type, c.category)}: {exc}") from exc
        inputs.append(inp)
    created: list[BudgetEntry] = []
    for inp in inputs:
        created += budget_svc.create(ctx, inp)
    return created
