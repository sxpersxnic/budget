"""What-if scenarios (F15, decision 18): named sets of budget changes laid over the plan, never written into it.

A **scenario** is a name and any number of **changes**. A change says: in these months (from a first month, every
`recur_months` months, up to a last month or the end of the plan) the budget of this category is this amount, whatever
the plan budgets there; 0 takes the category's budget out of those months. Everything else stays as in the plan, so
"80 % work from January" is one income change and "a car in March" one expenditure change.

The scenario's **projection** is the plan's (F47, `wealth.projected_wealth`) with the scenario's budgets in place of
the plan's for the months and categories it changes (`overlay`, `projected_net`): recorded amounts still count where
they are higher, so a scenario never undoes what has happened. The Overview draws each scenario marked *shown* as a
line of its own beside the plan; `outcome` compares the two at the end of the plan.

**Adopt into Plan** (`adopt`) writes the changes into the budget as one undoable action: per change, the plan's
entries of that type and category in the change's months are replaced by a series of the change's amount (none when
it is 0), in the order the changes were entered, so a later change wins where two meet, as in the overlay. The
scenario is removed in the same action, because its line would from then on only repeat the plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from budget_app.domain import months
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, Scenario, ScenarioChange
from budget_app.i18n import N_, t
from budget_app.services import aggregates
from budget_app.services import budget as budget_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

EPS = 0.005


@dataclass
class ChangeInput:
    type: str = BUDGET_EXPENDITURE
    category: str = ""
    amount: float | None = None  # 0 takes the budget out of the months
    currency: str = ""
    start_month: str = ""
    end_month: str = ""  # "" means up to the end of the plan
    recur_months: int = 1
    comment: str = ""


@dataclass(frozen=True)
class Outcome:
    """A scenario against the plan at the end of `month`: projected wealth in both, and the difference."""

    month: str
    plan: float
    scenario: float

    @property
    def delta(self) -> float:
        return self.scenario - self.plan


# ------------------------------------------------------------------ lookups
def scenarios(ctx: Context) -> list[Scenario]:
    """All scenarios, by name."""
    return sorted(ctx.store.scenarios.all(), key=lambda s: (s.name.lower(), s.id))


def shown(ctx: Context) -> list[Scenario]:
    """The scenarios drawn on the Overview's wealth chart."""
    return [s for s in scenarios(ctx) if s.shown]


def get(ctx: Context, scenario_id: str) -> Scenario:
    scenario = ctx.store.scenarios.get(scenario_id)
    if scenario is None:
        raise ValidationError(t("This scenario no longer exists"))
    return scenario


def changes(ctx: Context, scenario_id: str) -> list[ScenarioChange]:
    """A scenario's changes in the order they were entered (the order they count in)."""
    rows = [c for c in ctx.store.scenario_changes.all() if c.scenario_id == scenario_id]
    return sorted(rows, key=lambda c: (c.seq, c.id))


def get_change(ctx: Context, change_id: str) -> ScenarioChange:
    change = ctx.store.scenario_changes.get(change_id)
    if change is None:
        raise ValidationError(t("This change no longer exists"))
    return change


def change_months(ctx: Context, change: ScenarioChange) -> list[str]:
    """The months a change applies to: from its first month, in its rhythm, to its last month or the plan's end."""
    end = change.end_month or ctx.horizon()
    if end < change.start_month:
        return []
    return months.month_range(change.start_month, end, max(change.recur_months, 1))


def category_label(ctx: Context, change: ScenarioChange) -> str:
    if change.type == BUDGET_INCOME:
        cat = ctx.store.income_categories.get(change.category)
        return cat.name if cat else change.category
    return get_coicop().label(change.category)


# ------------------------------------------------------------------ the projection with the scenario's budgets
def overlay(ctx: Context, scenario_id: str, month: str, type_: str, view_cur: str) -> dict[str, float]:
    """The budgets of `type_` in `month` with the scenario's changes laid over the plan's, per category, in the
    view currency at the latest rate (like budget entries). A change entered later wins where two meet."""
    out = aggregates.budgeted(ctx, {month}, type_, view_cur)
    for change in changes(ctx, scenario_id):
        if change.type != type_ or month not in change_months(ctx, change):
            continue
        if change.amount > EPS:
            out[change.category] = ctx.convert_on(change.amount, change.currency, view_cur, None)
        else:
            out.pop(change.category, None)
    return out


def projected_net(ctx: Context, scenario_id: str, month: str, view_cur: str) -> float:
    """`aggregates.projected_net` with the scenario's budgets."""
    return aggregates.net_with_budgets(
        ctx,
        month,
        view_cur,
        overlay(ctx, scenario_id, month, BUDGET_EXPENDITURE, view_cur),
        overlay(ctx, scenario_id, month, BUDGET_INCOME, view_cur),
    )


def _net_of(scenario_id: str) -> wealth_svc.NetFn:
    return lambda ctx, month, view_cur: projected_net(ctx, scenario_id, month, view_cur)


def projected_wealth(ctx: Context, scenario_id: str, month: str, view_cur: str) -> float:
    """Projected wealth at the end of `month` in the scenario (recorded months are what they are)."""
    return wealth_svc.projected_wealth(ctx, month, view_cur, net=_net_of(scenario_id))


def series(ctx: Context, scenario_id: str, view_cur: str, today: date | None = None) -> list[tuple[str, float]]:
    """The scenario's projected months, (month, wealth), up to the end of the plan."""
    return [(m, v) for m, v, p in wealth_svc.wealth_series(ctx, view_cur, today=today, net=_net_of(scenario_id)) if p]


def outcome(ctx: Context, scenario_id: str, view_cur: str, month: str | None = None) -> Outcome:
    """The scenario against the plan at the end of `month` (default: the end of the plan)."""
    month = month or ctx.horizon()
    plan = wealth_svc.projected_wealth(ctx, month, view_cur)
    return Outcome(month, plan, projected_wealth(ctx, scenario_id, month, view_cur))


# ------------------------------------------------------------------ scenarios
def _check_name(ctx: Context, name: str, scenario: Scenario | None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValidationError(t("Name is required"))
    for other in ctx.store.scenarios.all():
        if other.name.lower() == name.lower() and (scenario is None or other.id != scenario.id):
            raise ValidationError(t("A scenario called “{name}” already exists", name=other.name))
    return name


@recorded(N_("Add a scenario"))
def add(ctx: Context, name: str, comment: str = "", today: date | None = None) -> Scenario:
    name, note, day = _check_name(ctx, name, None), (comment or "").strip(), today or date.today()
    return ctx.store.scenarios.add(Scenario(name=name, comment=note, created=day))


@recorded(N_("Change a scenario"))
def edit(ctx: Context, scenario_id: str, name: str, comment: str = "") -> Scenario:
    scenario = get(ctx, scenario_id)
    scenario.name, scenario.comment = _check_name(ctx, name, scenario), (comment or "").strip()
    return ctx.store.scenarios.update(scenario)


@recorded(N_("Show or hide a scenario on the Overview"))
def set_shown(ctx: Context, scenario_id: str, shown: bool) -> Scenario:
    scenario = get(ctx, scenario_id)
    scenario.shown = bool(shown)
    return ctx.store.scenarios.update(scenario)


@recorded(N_("Delete a scenario"))
def delete(ctx: Context, scenario_id: str) -> None:
    """Delete a scenario with its changes; the plan is untouched."""
    scenario = get(ctx, scenario_id)
    _delete_with_changes(ctx, scenario)


def _delete_with_changes(ctx: Context, scenario: Scenario) -> None:
    ctx.store.scenario_changes.delete([c.id for c in changes(ctx, scenario.id)])
    ctx.store.scenarios.delete([scenario.id])


# ------------------------------------------------------------------ changes
def validate_change(ctx: Context, inp: ChangeInput) -> list[str]:
    """The months a change applies to; ValidationError when it can't."""
    if inp.amount is None or inp.amount < 0:
        raise ValidationError(t("Enter an amount; 0 takes the budget out of these months"))
    if not inp.currency:
        raise ValidationError(t("Currency is required"))
    budget_svc.validate_category(ctx, inp.type, inp.category)
    return budget_svc.plan_months(
        ctx,
        BudgetInput(
            start_month=inp.start_month,
            end_month=inp.end_month,
            forever=not inp.end_month,
            recur_months=inp.recur_months,
        ),
    )


def _build(ctx: Context, inp: ChangeInput, change: ScenarioChange) -> ScenarioChange:
    validate_change(ctx, inp)
    amount = round(float(inp.amount or 0), 2)
    change.type, change.category, change.amount, change.currency = inp.type, inp.category, amount, inp.currency
    change.amount_chf, change.fx_date = ctx.to_base_latest(amount, inp.currency)
    change.start_month, change.end_month = inp.start_month, inp.end_month
    change.recur_months, change.comment = max(int(inp.recur_months or 1), 1), (inp.comment or "").strip()
    return change


@recorded(N_("Add a scenario change"))
def add_change(ctx: Context, scenario_id: str, inp: ChangeInput) -> ScenarioChange:
    scenario = get(ctx, scenario_id)
    seq = max([c.seq for c in changes(ctx, scenario.id)], default=0) + 1
    blank = ScenarioChange(scenario.id, inp.type, "", 0.0, "", 0.0, date.today(), "", seq=seq)
    return ctx.store.scenario_changes.add(_build(ctx, inp, blank))


@recorded(N_("Change a scenario change"))
def edit_change(ctx: Context, change_id: str, inp: ChangeInput) -> ScenarioChange:
    return ctx.store.scenario_changes.update(_build(ctx, inp, get_change(ctx, change_id)))


@recorded(N_("Delete a scenario change"))
def delete_change(ctx: Context, change_id: str) -> None:
    ctx.store.scenario_changes.delete([get_change(ctx, change_id).id])


def plan_budget(ctx: Context, type_: str, category: str, month: str, view_cur: str) -> float:
    """What the plan budgets for a category in a month, in the view currency (0 without a budget)."""
    return aggregates.budgeted(ctx, {month}, type_, view_cur).get(category, 0.0)


# ------------------------------------------------------------------ adopt into the plan
def _plan_entries(ctx: Context, type_: str, category: str, month_list: Iterable[str]) -> list[str]:
    targets = set(month_list)
    return [b.id for b in ctx.store.budget.all() if b.type == type_ and b.category == category and b.month in targets]


@recorded(N_("Adopt a scenario into the plan"))
def adopt(ctx: Context, scenario_id: str) -> int:
    """Write a scenario's changes into the budget, in the order they were entered, replacing the plan's entries of
    the same type and category in each change's months; then remove the scenario. Returns the number of budget
    entries created. Nothing changes when a change can't be created (its months lie beyond the plan's end)."""
    scenario = get(ctx, scenario_id)
    planned: list[tuple[ScenarioChange, BudgetInput, list[str]]] = []
    for change in changes(ctx, scenario.id):  # every change is checked before anything is written
        inp = BudgetInput(
            start_month=change.start_month,
            end_month=change.end_month,
            forever=not change.end_month,
            recur_months=change.recur_months,
            type=change.type,
            currency=change.currency,
            amount=change.amount,
            category=change.category,
            overwrite=True,
            comment=change.comment,
        )
        try:
            budget_svc.validate_category(ctx, inp.type, inp.category)
            month_list = budget_svc.plan_months(ctx, inp)
        except ValidationError as exc:
            raise ValidationError(f"{category_label(ctx, change)}: {exc}") from exc
        planned.append((change, inp, month_list))
    added = 0
    for change, inp, month_list in planned:
        if change.amount > EPS:
            added += len(budget_svc.create(ctx, inp))
        else:
            ctx.store.budget.delete(_plan_entries(ctx, change.type, change.category, month_list))
    _delete_with_changes(ctx, scenario)
    return added
