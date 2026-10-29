"""Reminders and alerts (F31), in the app only: what wants doing or looking at, drawn from the other features.

Nothing here is sent anywhere: no e-mail, no system notification, no network. The Reminders page lists the items
when the app runs, and the sidebar counts them in a badge; each item links to the place where it is dealt with.

Kinds, each a switch in Settings (all on by default; `Settings.reminders_off` holds the kinds turned off):

- **balances:** regular accounts with no balance recorded for last month, while their last balance isn't zero
  (`wealth.account_rows`, the same rule as the Budget page's callout) – the month-end chore;
- **recurring:** due occurrences of recurring transactions (F05), one per occurrence, like the Due list;
- **bills:** open QR bills overdue or due within a week (F03), one per bill;
- **budgets:** budgets exceeded this month or on pace to be exceeded (F18's rules and thresholds, decision 26);
- **goals:** savings goals behind their plan line or past their target date (F14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from budget_app.domain import goals as plan
from budget_app.domain import months
from budget_app.domain.coicop import get_coicop
from budget_app.i18n import N_, date_label, t
from budget_app.services import bills as bills_svc
from budget_app.services import goals as goal_svc
from budget_app.services import recurring as recurring_svc
from budget_app.services import trends
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context

BALANCES, RECURRING, BILLS, BUDGETS, GOALS = "balances", "recurring", "bills", "budgets", "goals"
KINDS = (BALANCES, RECURRING, BILLS, BUDGETS, GOALS)  # the order on the page and in Settings
TITLES = {
    BALANCES: N_("Balances to record"),
    RECURRING: N_("Recurring transactions due"),
    BILLS: N_("Bills due"),
    BUDGETS: N_("Budgets exceeded or about to be"),
    GOALS: N_("Goals behind or overdue"),
}
DESCRIPTIONS = {
    BALANCES: N_("Accounts without a month-end balance for last month."),
    RECURRING: N_("Occurrences waiting in the Due list."),
    BILLS: N_("Open QR bills that are overdue or due within a week."),
    BUDGETS: N_("Budgets spent beyond their amount this month, or on pace to be."),
    GOALS: N_("Savings goals behind their plan or past their target date."),
}
LINKS = {BALANCES: "/budget", RECURRING: "/recurring", BILLS: "/bills", BUDGETS: "/budget", GOALS: "/goals"}
WARN, LATE = "warn", "late"


@dataclass(frozen=True)
class Reminder:
    """One item: its kind, a title, a line below it, an amount (or None), where it is dealt with, and a tone
    (`late` for what is overdue or exceeded, `warn` for what is coming)."""

    kind: str
    title: str
    sub: str
    link: str
    amount: float | None = None
    currency: str = ""
    tone: str = WARN


# ------------------------------------------------------------------ settings
def enabled(ctx: Context, kind: str) -> bool:
    return kind not in ctx.store.settings.reminders_off


def set_enabled(ctx: Context, kind: str, on: bool) -> None:
    """Turn a kind of reminder on or off (a setting, so not part of the undo history)."""
    off = [k for k in ctx.store.settings.reminders_off if k != kind and k in KINDS]
    if not on and kind in KINDS:
        off.append(kind)
    ctx.store.settings.reminders_off = sorted(off, key=KINDS.index)
    ctx.store.save_settings()


# ------------------------------------------------------------------ the items
def _today(today: date | None) -> date:
    return today or date.today()


def balances(ctx: Context, view_cur: str, today: date | None = None) -> list[Reminder]:
    month = months.previous_month(_today(today))
    out = []
    for row in wealth_svc.account_rows(ctx, month, view_cur):
        if row.needs_entry:
            out.append(
                Reminder(
                    BALANCES,
                    row.account.name,
                    t(
                        "No balance for {month} · last {amount}",
                        month=months.label(month),
                        amount=_money(row.amount, row.account.currency),
                    ),
                    f"/budget?{urlencode({'month': month})}",
                    None,
                    row.account.currency,
                )
            )
    return out


def recurring(ctx: Context, today: date | None = None) -> list[Reminder]:
    day = _today(today)
    out = []
    for occ in recurring_svc.due(ctx, day):
        late = occ.date < day
        when = t("Due since {date}", date=date_label(occ.date)) if late else t("Due today")
        out.append(
            Reminder(
                RECURRING,
                recurring_svc.title(ctx, occ.series),
                when,
                LINKS[RECURRING],
                occ.series.amount,
                occ.series.currency,
                LATE if late else WARN,
            )
        )
    return out


def bills(ctx: Context, today: date | None = None) -> list[Reminder]:
    day = _today(today)
    out = []
    for bill in bills_svc.due(ctx, day):
        due = bill.due_date or day  # `due` lists dated bills only
        if due < day:
            when, tone = t("Overdue since {date}", date=date_label(due)), LATE
        elif due == day:
            when, tone = t("Due today"), WARN
        else:
            when, tone = t("Due on {date}", date=date_label(due)), WARN
        amount = bill.amount if bill.amount else None
        out.append(Reminder(BILLS, bill.creditor, when, LINKS[BILLS], amount, bill.currency, tone))
    return out


def budgets(ctx: Context, view_cur: str, today: date | None = None) -> list[Reminder]:
    """Exceeded budgets and budgets on pace to be exceeded, as *Notable this month* finds them (F18)."""
    notable = trends.notable(ctx, view_cur, _today(today))
    out = []
    for item in notable.items:
        if item.kind not in (trends.OVER_BUDGET, trends.PACE):
            continue
        label = get_coicop().label(item.category)
        link = f"/budget?{urlencode({'month': notable.month, 'category': item.category})}"
        budget = _money(item.reference, view_cur)
        if item.kind == trends.OVER_BUDGET:
            sub = t("{amount} over the {budget} budget", amount=_fmt(item.amount - item.reference), budget=budget)
            out.append(Reminder(BUDGETS, label, sub, link, item.amount, view_cur, LATE))
        else:
            sub = t("On pace for {amount} of the {budget} budget", amount=_fmt(item.projected), budget=budget)
            out.append(Reminder(BUDGETS, label, sub, link, item.amount, view_cur, WARN))
    return out


def goals(ctx: Context, today: date | None = None) -> list[Reminder]:
    day = _today(today)
    out = []
    for st in goal_svc.statuses(ctx, today=day):
        p, goal = st.progress, st.goal
        if p.status == plan.OVERDUE and goal.due is not None:
            to_go = _money(p.remaining, goal.currency)
            sub = t("Overdue since {date} · {amount} to go", date=date_label(goal.due), amount=to_go)
            out.append(Reminder(GOALS, goal.name, sub, LINKS[GOALS], p.current, goal.currency, LATE))
        elif p.status == plan.BEHIND and p.expected is not None:
            sub = t("Behind: {amount} short of the plan line", amount=_money(p.expected - p.current, goal.currency))
            out.append(Reminder(GOALS, goal.name, sub, LINKS[GOALS], p.current, goal.currency, WARN))
    return out


def _fmt(value: float) -> str:
    value = round(value, 2) + 0.0
    return f"{value:,.2f}".replace(",", "'").replace("-", "−")


def _money(value: float, currency: str) -> str:
    return f"{_fmt(value)} {currency}".strip()


def reminders(ctx: Context, view_cur: str, today: date | None = None) -> dict[str, list[Reminder]]:
    """The items per kind, in the order of `KINDS`, kinds turned off left out; a kind without items is left out too."""
    day = _today(today)
    builders = {
        BALANCES: lambda: balances(ctx, view_cur, day),
        RECURRING: lambda: recurring(ctx, day),
        BILLS: lambda: bills(ctx, day),
        BUDGETS: lambda: budgets(ctx, view_cur, day),
        GOALS: lambda: goals(ctx, day),
    }
    out: dict[str, list[Reminder]] = {}
    for kind in KINDS:
        if not enabled(ctx, kind):
            continue
        if items := builders[kind]():
            out[kind] = items
    return out


def count(ctx: Context, today: date | None = None) -> int:
    """How many reminders there are (the sidebar badge), in the display currency."""
    return sum(len(v) for v in reminders(ctx, ctx.store.settings.display_currency, today).values())
