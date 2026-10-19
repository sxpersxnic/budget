"""Budget entries: series expansion, 'Forever' extension, overwrite logic."""

from __future__ import annotations

from dataclasses import dataclass, replace

from budget_app.domain import months
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry, new_id
from budget_app.i18n import t
from budget_app.services.context import Context, ValidationError


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


def _validate_category(ctx: Context, type_: str, category: str) -> None:
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
    _validate_category(ctx, inp.type, inp.category)
    return plan_months(ctx, inp)


def create(ctx: Context, inp: BudgetInput) -> list[BudgetEntry]:
    month_list = validate(ctx, inp)
    if inp.overwrite:
        targets = set(month_list)
        ctx.store.budget.delete(
            [
                b.id
                for b in ctx.store.budget.all()
                if b.type == inp.type and b.category == inp.category and b.month in targets
            ]
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
        _validate_category(ctx, entry.type, category)
        entry.category = category
    entry.amount = round(float(amount), 2)
    entry.currency = currency or entry.currency
    entry.amount_chf, entry.fx_date = ctx.to_base_latest(entry.amount, entry.currency)
    if comment is not None:
        entry.comment = comment.strip()
    return ctx.store.budget.update(entry)


def delete(ctx: Context, ids: list[str]) -> int:
    return ctx.store.budget.delete(ids)


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


def reschedule(ctx: Context, entry_id: str, inp: BudgetInput) -> list[BudgetEntry]:
    """Replan an entry's series from the entry's month on, with `inp`'s schedule, amount and note
    (and category, if `inp` names one).

    Months before the entry keep their values and end there; the replanned part becomes a series of
    its own, so 'Forever' extension continues the new plan, not the old one.
    """
    entry = ctx.store.budget.get(entry_id)
    if entry is None:
        raise ValidationError(t("Budget entry not found"))
    category = inp.category or entry.category
    inp = replace(inp, start_month=entry.month, type=entry.type, category=category, overwrite=False)
    validate(ctx, inp)
    earlier = [
        replace(b, forever=False, generated_until="")
        for b in ctx.store.budget.all()
        if entry.series_id and b.series_id == entry.series_id and b.month < entry.month
    ]
    ctx.store.budget.delete([entry.id] + [b.id for b in later_in_series(ctx, entry)])
    if earlier:
        ctx.store.budget.update_many(earlier)
    return create(ctx, inp)


def entries_for(ctx: Context, month: str, type_: str, category: str) -> list[BudgetEntry]:
    return [b for b in ctx.store.budget.all() if b.month == month and b.type == type_ and b.category == category]
