"""Budget entries: series expansion, 'Forever' extension, overwrite logic."""

from __future__ import annotations

from dataclasses import dataclass, replace

from budget_app.domain import months
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry, new_id
from budget_app.i18n import N_, t
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded


@dataclass
class BudgetInput:
    start_month: str = ""
    end_month: str = ""  # ignored when forever=True
    forever: bool = False
    recur_months: int = 1
    type: str = BUDGET_EXPENDITURE
    currency: str = ""
    amount: float | None = None
    category: str = ""
    overwrite: bool = False
    comment: str = ""


def validate_category(ctx: Context, type_: str, category: str) -> None:
    """A budgetable category of the type: a COICOP code or Taxes, or an income category that exists."""
    if type_ == BUDGET_EXPENDITURE:
        if category == ADV or (category != TAX and get_coicop().get(category) is None):
            raise ValidationError(t("Select an expenditure category"))
    elif type_ == BUDGET_INCOME:
        if ctx.store.income_categories.get(category) is None:
            raise ValidationError(t("Select an income category"))
    else:
        raise ValidationError(t("Select income or expenditure"))


def plan_months(ctx: Context, inp: BudgetInput) -> list[str]:
    horizon = ctx.horizon()
    if not inp.start_month:
        raise ValidationError(t("Select the first month"))
    if horizon < inp.start_month:  # no final month can work: say why, instead of blaming the choice
        raise ValidationError(
            t("The plan ends in {month}, before the first month. Extend it in Settings.", month=months.label(horizon))
        )
    end = horizon if inp.forever else inp.end_month
    if not end:
        raise ValidationError(t("Select the final month"))
    if end > horizon:
        raise ValidationError(
            t("The final month cannot be after the budget horizon ({month})", month=months.label(horizon))
        )
    if end < inp.start_month:
        raise ValidationError(t("The final month must not be before the first month"))
    if inp.recur_months < 1:
        raise ValidationError(t("The recurrence must be at least every month"))
    return months.month_range(inp.start_month, end, inp.recur_months)


def validate(ctx: Context, inp: BudgetInput) -> list[str]:
    if inp.amount is None or inp.amount <= 0:
        raise ValidationError(t("Amount must be a positive number"))
    if not inp.currency:
        raise ValidationError(t("Currency is required"))
    validate_category(ctx, inp.type, inp.category)
    return plan_months(ctx, inp)


@recorded(N_("Add a budget entry"))
def create(ctx: Context, inp: BudgetInput) -> list[BudgetEntry]:
    month_list = validate(ctx, inp)
    if inp.overwrite:
        targets = set(month_list)
        same = [b for b in ctx.store.budget.all() if b.type == inp.type and b.category == inp.category]
        ctx.store.budget.delete([b.id for b in same if b.month in targets])
        if inp.forever:  # the new series continues the category's plan; an older 'Forever' one stops where it is
            ctx.store.budget.update_many(
                [replace(b, forever=False, generated_until="") for b in same if b.forever and b.month not in targets]
            )
    amount = round(float(inp.amount or 0), 2)  # validated > 0 above
    amount_chf, fx_date = ctx.to_base_latest(amount, inp.currency)
    series = new_id()
    rows = [
        BudgetEntry(
            month=m,
            type=inp.type,
            category=inp.category,
            amount=amount,
            currency=inp.currency,
            amount_chf=amount_chf,
            fx_date=fx_date,
            series_id=series,
            recur_months=inp.recur_months,
            forever=inp.forever,
            comment=inp.comment.strip(),
            generated_until=ctx.horizon() if inp.forever else "",
        )
        for m in month_list
    ]
    return ctx.store.budget.add_many(rows)


@recorded(N_("Change a budget entry"))
def update_entry(
    ctx: Context,
    entry_id: str,
    amount: float,
    currency: str | None = None,
    comment: str | None = None,
    category: str | None = None,
) -> BudgetEntry:
    entry = ctx.store.budget.get(entry_id)  # a copy: nothing changes unless update() succeeds
    if entry is None:
        raise ValidationError(t("Budget entry not found"))
    if amount is None or amount <= 0:
        raise ValidationError(t("Amount must be a positive number"))
    if category is not None:
        validate_category(ctx, entry.type, category)
        entry.category = category
    entry.amount = round(float(amount), 2)
    entry.currency = currency or entry.currency
    entry.amount_chf, entry.fx_date = ctx.to_base_latest(entry.amount, entry.currency)
    if comment is not None:
        entry.comment = comment.strip()
    return ctx.store.budget.update(entry)


@recorded(N_("Delete budget entries"))
def delete(ctx: Context, ids: list[str]) -> int:
    return ctx.store.budget.delete(ids)


@recorded(N_("Change the planning horizon"))
def set_horizon(ctx: Context, new_horizon: str) -> int:
    """Change the budget horizon and continue every 'Forever' series up to it.

    Shrinking the horizon never deletes entries: rows beyond it stay as they are, and the series
    resume after them when the horizon is extended again. Returns the number of rows added.
    """
    ctx.store.settings.budget_horizon = new_horizon
    ctx.store.save_settings()
    return extend_forever(ctx, new_horizon)


def extend_forever(ctx: Context, horizon: str) -> int:
    """Generate the missing months of every 'Forever' series up to `horizon`.

    Generation resumes after the furthest month a series was ever generated for, so months the
    user deleted or overwrote are not re-created.
    """
    series: dict[str, list[BudgetEntry]] = {}
    for b in ctx.store.budget.all():
        if b.forever:
            series.setdefault(b.series_id, []).append(b)
    new_rows, touched = [], []
    for rows in series.values():
        last = max(rows, key=lambda r: r.month)  # template for amount, category, recurrence
        done_until = max([last.month] + [r.generated_until for r in rows if r.generated_until])
        if done_until >= horizon:
            continue
        nxt = last.month
        while nxt <= done_until:
            nxt = months.add_months(nxt, last.recur_months)
        for m in months.month_range(nxt, horizon, last.recur_months):
            new_rows.append(replace(last, id=new_id(), month=m, generated_until=horizon))
        touched += [replace(r, generated_until=horizon) for r in rows]
    if touched:
        ctx.store.budget.update_many(touched)
    if new_rows:
        ctx.store.budget.add_many(new_rows)
    return len(new_rows)


@dataclass
class Schedule:
    """How a budget entry continues: its final month (or forever) and its rhythm."""

    end_month: str  # "" when forever
    forever: bool
    recur_months: int


def schedule_of(ctx: Context, entry: BudgetEntry) -> Schedule:
    """The schedule of the series an entry belongs to; a single entry ends in its own month."""
    rows = [b for b in ctx.store.budget.all() if entry.series_id and b.series_id == entry.series_id] or [entry]
    if any(b.forever for b in rows):
        return Schedule("", True, entry.recur_months)
    return Schedule(max(b.month for b in rows), False, entry.recur_months)


def later_in_series(ctx: Context, entry: BudgetEntry) -> list[BudgetEntry]:
    return [
        b
        for b in ctx.store.budget.all()
        if entry.series_id and b.series_id == entry.series_id and b.month > entry.month
    ]


@recorded(N_("Replan a budget"))
def reschedule(ctx: Context, entry_id: str, inp: BudgetInput) -> list[BudgetEntry]:
    """Replan an entry's series from the entry's month on, with `inp`'s schedule, amount and note
    (and category, if `inp` names one).

    Months before the entry keep their values and end there; the replanned part becomes a series of
    its own, so 'Forever' extension continues the new plan, not the old one.
    """
    entry = ctx.store.budget.get(entry_id)
    if entry is None:
        raise ValidationError(t("Budget entry not found"))
    rows = [b for b in ctx.store.budget.all() if entry.series_id and b.series_id == entry.series_id] or [entry]
    return _replan(
        ctx, rows, replace(inp, start_month=entry.month, type=entry.type, category=inp.category or entry.category)
    )


def _replan(ctx: Context, rows: list[BudgetEntry], inp: BudgetInput) -> list[BudgetEntry]:
    """Replace the `rows` from `inp.start_month` on with a new series made from `inp` (its category, if it names
    none, the rows' own); the rows before keep their values and end there."""
    inp = replace(inp, category=inp.category or rows[0].category, overwrite=False)
    validate(ctx, inp)
    earlier = [replace(b, forever=False, generated_until="") for b in rows if b.month < inp.start_month]
    ctx.store.budget.delete([b.id for b in rows if b.month >= inp.start_month])
    if earlier:
        ctx.store.budget.update_many(earlier)
    return create(ctx, inp)


# ------------------------------------------------------------------ the plan as series
@dataclass
class Series:
    """The budget entries planned together: one `series_id` (an entry without one is a series of its own) and one
    type and category (a month moved to another category leaves its series)."""

    key: str
    entries: list[BudgetEntry]  # by month
    current: BudgetEntry  # the entry of the current month, else the next one, else the last

    @property
    def type(self) -> str:
        return self.current.type

    @property
    def category(self) -> str:
        return self.current.category

    @property
    def first(self) -> str:
        return self.entries[0].month

    @property
    def last(self) -> str:
        return self.entries[-1].month

    @property
    def forever(self) -> bool:
        return any(b.forever for b in self.entries)

    @property
    def once(self) -> bool:
        return len(self.entries) == 1 and not self.forever

    @property
    def differing(self) -> int:
        """The months whose amount or currency differs from the current one's (changed one by one)."""
        cur = self.current
        return sum((b.amount, b.currency) != (cur.amount, cur.currency) for b in self.entries)

    def ended(self, month: str) -> bool:
        return not self.forever and self.last < month


def series_key(b: BudgetEntry) -> str:
    return f"{b.series_id or b.id}|{b.type}|{b.category}"


def series(ctx: Context, month: str | None = None) -> list[Series]:
    """The plan as series, income first, then by category and first month; `month` (the current one by default)
    decides each series' current entry."""
    month = month or months.current_month()
    groups: dict[str, list[BudgetEntry]] = {}
    for b in ctx.store.budget.all():
        groups.setdefault(series_key(b), []).append(b)
    out = []
    for key, rows in groups.items():
        rows.sort(key=lambda b: (b.month, b.id))
        current = next((b for b in rows if b.month >= month), rows[-1])
        out.append(Series(key, rows, current))
    out.sort(key=lambda s: (s.type != BUDGET_INCOME, s.category, s.first, s.key))
    return out


def find_series(ctx: Context, key: str) -> Series | None:
    return next((s for s in series(ctx) if s.key == key), None)


@recorded(N_("Replan a budget"))
def replan_series(ctx: Context, key: str, inp: BudgetInput) -> list[BudgetEntry]:
    """Replan a series from `inp.start_month` on (amount, currency, category, schedule and note); the months before
    keep their values and end there. A start before the series' first month replaces the whole series."""
    found = find_series(ctx, key)
    if found is None:
        raise ValidationError(t("Budget entry not found"))
    return _replan(ctx, found.entries, replace(inp, type=found.type))


@recorded(N_("Delete a budget"))
def delete_series(ctx: Context, key: str) -> int:
    found = find_series(ctx, key)
    return ctx.store.budget.delete([b.id for b in found.entries]) if found else 0


def planned(ctx: Context, shown: list[Series], month: str, view_cur: str) -> dict[str, float]:
    """The amounts the `shown` series plan for `month` per type, in the view currency at the latest rate."""
    out = {BUDGET_INCOME: 0.0, BUDGET_EXPENDITURE: 0.0}
    for s in shown:
        for b in s.entries:
            if b.month == month:
                out[b.type] += ctx.convert_on(b.amount, b.currency, view_cur, None)
    return out


def entries_for(ctx: Context, month: str, type_: str, category: str) -> list[BudgetEntry]:
    return [b for b in ctx.store.budget.all() if b.month == month and b.type == type_ and b.category == category]
