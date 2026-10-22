"""Savings goals (F14): add, change, archive and delete them; their target, current amount and progress; and what
they reserve in the projection.

- **Target:** a target amount in the goal's currency, or for an emergency fund N × the average monthly spending of
  the last 12 complete months (`monthly_spending`), in the goal's currency.
- **Current amount:** the balance of the linked account, converted to the goal's currency at the latest rate, or
  the amount saved so far typed in by hand. An account's balance is its latest month-end balance plus what was
  booked on it after that month up to today (`account_balance`); a securities or crypto account's is the value of
  its holdings.
- **Progress** and **reserve** follow `domain.goals`.
- **Free wealth** (architecture §7, decision 23): the projection is left as it is (F47); the Overview draws a
  second line, projected wealth minus the reserves of the goals marked *reserve in the projection* (archived ones
  left out), each what the goal is to hold at that month on its plan (`domain.goals.planned`), converted at the
  latest rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from budget_app.domain import goals as plan
from budget_app.domain import months
from budget_app.domain.coicop import ADV
from budget_app.domain.goals import Progress
from budget_app.domain.models import ACCOUNT_REGULAR, GOAL_AMOUNT, GOAL_MONTHS, TX_EXCHANGE, Goal
from budget_app.i18n import N_, t
from budget_app.services import wealth as wealth_svc
from budget_app.services.aggregates import actual_expenditure
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

SPENDING_MONTHS = 12  # an emergency fund's target follows the average of this many complete months
MAX_MONTHS = 36


@dataclass
class GoalInput:
    name: str
    currency: str
    kind: str = GOAL_AMOUNT
    target: float | None = None  # GOAL_AMOUNT
    months: int | None = None  # GOAL_MONTHS
    target_date: date | None = None
    account_id: str = ""  # progress from this account's balance; empty: `saved`
    saved: float | None = None
    reserve: bool = False


@dataclass
class GoalStatus:
    goal: Goal
    progress: Progress

    @property
    def open(self) -> bool:
        return self.progress.status != plan.REACHED


# ------------------------------------------------------------------ lookups
def _today(today: date | None) -> date:
    return today or date.today()


def goals(ctx: Context, archived: bool = False) -> list[Goal]:
    """The goals (or the archived ones): by target date, those without one last, then by name."""
    rows = [g for g in ctx.store.goals.all() if g.archived == archived]
    return sorted(rows, key=lambda g: (g.target_date or "9999", g.name.lower(), g.id))


def get(ctx: Context, goal_id: str) -> Goal:
    goal = ctx.store.goals.get(goal_id)
    if goal is None:
        raise ValidationError(t("This goal no longer exists"))
    return goal


# ------------------------------------------------------------------ amounts
def spending_months(today: date | None = None) -> list[str]:
    """The last 12 complete months, oldest first."""
    last = months.previous_month(_today(today))
    return months.month_range(months.add_months(last, -(SPENDING_MONTHS - 1)), last)


def monthly_spending(ctx: Context, currency: str, today: date | None = None) -> float:
    """Average recorded spending per month over the last 12 complete months, in `currency` (at each transaction's
    rate). The part paid for others (an advance, which comes back) does not count. Months before the first
    transaction are left out, so a new data folder averages the months it has; 0 without any."""
    window = spending_months(today)
    dates = [tx.date for tx in ctx.store.transactions.all()]
    if not dates:
        return 0.0
    first = months.month_of(min(dates))
    counted = {m for m in window if m >= first}
    if not counted:
        return 0.0
    spent = sum(v for code, v in actual_expenditure(ctx, counted, currency).items() if code != ADV)
    return round(spent / len(counted), 2)


def target_of(ctx: Context, goal: Goal, today: date | None = None) -> float:
    if goal.kind == GOAL_MONTHS:
        return round(goal.months * monthly_spending(ctx, goal.currency, today), 2)
    return goal.target


def account_balance(ctx: Context, account_id: str, today: date | None = None) -> float | None:
    """An account's balance today, in its currency: its latest month-end balance (a securities or crypto account's
    holdings) plus what was booked on it after that month, up to today. None for an unknown account."""
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        return None
    day = _today(today)
    month = months.month_of(day)
    entry = wealth_svc.balance_entry(ctx, account_id, month)
    if acc.holds_in(month):  # valued from its holdings, which every trade and price is booked into
        return entry.amount if entry else 0.0
    balance = entry.amount if entry else 0.0
    after = months.last_day(entry.month) if entry else date.min
    for tx in ctx.store.transactions.all():
        if not after < tx.date <= day:
            continue
        if tx.from_account == account_id:
            balance -= tx.amount
        if tx.to_account == account_id:
            balance += tx.to_amount if tx.type == TX_EXCHANGE else tx.amount
    return round(balance, 2)


def current_of(ctx: Context, goal: Goal, today: date | None = None) -> float:
    """What the goal holds now, in its currency: the linked account's balance (latest rate) or the amount saved."""
    if goal.account_id:
        acc = ctx.store.accounts.get(goal.account_id)
        balance = account_balance(ctx, goal.account_id, today)
        if acc is not None and balance is not None:
            return round(ctx.convert_on(balance, acc.currency, goal.currency, None), 2)
    return goal.saved


def status(ctx: Context, goal: Goal, today: date | None = None) -> GoalStatus:
    day = _today(today)
    current = current_of(ctx, goal, day)
    p = plan.progress(current, target_of(ctx, goal, day), day, goal.due, goal.start, goal.start_amount)
    return GoalStatus(goal, p)


def statuses(ctx: Context, archived: bool = False, today: date | None = None) -> list[GoalStatus]:
    return [status(ctx, g, today) for g in goals(ctx, archived)]


def top_open(ctx: Context, n: int = 3, today: date | None = None) -> list[GoalStatus]:
    """The first `n` goals not archived for the Overview: those still to reach first, by target date."""
    rows = statuses(ctx, today=today)
    return sorted(rows, key=lambda s: not s.open)[:n]


# ------------------------------------------------------------------ the reserve in the projection
def _months_from(start: str, month: str) -> int:
    (y0, m0), (y1, m1) = months.parse(start), months.parse(month)
    return (y1 - y0) * 12 + m1 - m0


def reserving(ctx: Context) -> list[Goal]:
    return [g for g in goals(ctx) if g.reserve]


def reserve_at(ctx: Context, month: str, view_cur: str, today: date | None = None) -> float:
    """What the goals marked *reserve in the projection* hold at the end of `month` on their plan, in `view_cur` at
    the latest rate (`domain.goals.planned`)."""
    return reserve_series(ctx, [month], view_cur, today)[month]


def reserve_series(ctx: Context, month_list: list[str], view_cur: str, today: date | None = None) -> dict[str, float]:
    day = _today(today)
    now = months.current_month(day)
    out = dict.fromkeys(month_list, 0.0)
    for goal in reserving(ctx):
        p = status(ctx, goal, day).progress
        for m in month_list:
            out[m] += ctx.convert_on(plan.planned(p, _months_from(now, m)), goal.currency, view_cur, None)
    return {m: round(v, 2) for m, v in out.items()}


def free_wealth(ctx: Context, month: str, view_cur: str, today: date | None = None) -> float:
    """Projected wealth at the end of `month` minus what the reserving goals hold then."""
    return wealth_svc.projected_wealth(ctx, month, view_cur) - reserve_at(ctx, month, view_cur, today)


# ------------------------------------------------------------------ add, change, archive, delete
def _check(ctx: Context, inp: GoalInput, goal: Goal | None, today: date) -> Goal:
    name = " ".join((inp.name or "").split())
    if not name:
        raise ValidationError(t("Name is required"))
    for other in ctx.store.goals.all():
        if other.name.lower() == name.lower() and (goal is None or other.id != goal.id):
            raise ValidationError(t("A goal called “{name}” already exists", name=other.name))
    if not inp.currency:
        raise ValidationError(t("Currency is required"))
    if inp.kind not in (GOAL_AMOUNT, GOAL_MONTHS):
        raise ValidationError(t("Choose the kind of goal"))
    target, n = 0.0, 0
    if inp.kind == GOAL_AMOUNT:
        if inp.target is None or inp.target <= 0:
            raise ValidationError(t("Enter a target amount above 0"))
        target = round(float(inp.target), 2)
    elif inp.months is None or not 1 <= inp.months <= MAX_MONTHS or inp.months != int(inp.months):
        raise ValidationError(t("Enter a whole number of months from 1 to {n}", n=MAX_MONTHS))
    else:
        n = int(inp.months)
    due = inp.target_date.isoformat() if inp.target_date else ""
    if inp.target_date and inp.target_date < today and (goal is None or due != goal.target_date):
        raise ValidationError(t("The target date must not be in the past"))
    if inp.account_id:
        acc = ctx.store.accounts.get(inp.account_id)
        if acc is None or acc.kind != ACCOUNT_REGULAR:
            raise ValidationError(t("Select an account"))
    saved = 0.0
    if not inp.account_id:
        saved = round(float(inp.saved or 0.0), 2)
        if saved < 0:
            raise ValidationError(t("The amount saved must not be negative"))
    out = Goal(name=name, currency=inp.currency) if goal is None else goal
    out.name, out.currency, out.kind, out.target, out.months = name, inp.currency, inp.kind, target, n
    out.target_date, out.account_id, out.saved, out.reserve = due, inp.account_id, saved, bool(inp.reserve)
    return out


@recorded(N_("Add a savings goal"))
def add(ctx: Context, inp: GoalInput, today: date | None = None) -> Goal:
    day = _today(today)
    goal = _check(ctx, inp, None, day)
    goal.start, goal.start_amount = day, current_of(ctx, goal, day)
    return ctx.store.goals.add(goal)


@recorded(N_("Change a savings goal"))
def edit(ctx: Context, goal_id: str, inp: GoalInput, today: date | None = None) -> Goal:
    """Change a goal. Its on-track line starts again today when it is linked to another account (or unlinked), or
    when its currency changes, since what it held at the start no longer compares."""
    day = _today(today)
    goal = get(ctx, goal_id)
    was = (goal.account_id, goal.currency)
    goal = _check(ctx, inp, goal, day)
    if (goal.account_id, goal.currency) != was:
        goal.start, goal.start_amount = day, current_of(ctx, goal, day)
    return ctx.store.goals.update(goal)


def _set_archived(ctx: Context, goal_id: str, archived: bool) -> Goal:
    goal = get(ctx, goal_id)
    goal.archived = archived
    return ctx.store.goals.update(goal)


@recorded(N_("Archive a savings goal"))
def archive(ctx: Context, goal_id: str) -> Goal:
    """Archive a goal: it leaves the list of goals, the Overview and the reserve, and can be restored."""
    return _set_archived(ctx, goal_id, True)


@recorded(N_("Restore a savings goal"))
def restore(ctx: Context, goal_id: str) -> Goal:
    return _set_archived(ctx, goal_id, False)


@recorded(N_("Delete a savings goal"))
def delete(ctx: Context, goal_id: str) -> None:
    ctx.store.goals.delete([get(ctx, goal_id).id])
