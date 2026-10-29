"""Reminders and alerts (F31), in the app only: what wants doing, drawn from the other features.

Nothing here is sent anywhere: no e-mail, no system notification, no network. The Overview's *Due* list shows the
items while the app runs, and the sidebar counts exactly those in the badge beside Overview; each item says where
it is dealt with and carries what its action needs (`ref`, `on`, `month`).

Kinds, each a switch in Settings (all on by default; `Settings.reminders_off` holds the kinds turned off):

- **balances:** regular accounts with no balance recorded for last month, while their last balance isn't zero
  (`wealth.account_rows`, the same rule as the Budget page's callout) – the month-end chore;
- **recurring:** due occurrences of recurring transactions (F05), one per occurrence, like the Recurring tab's Due;
- **bills:** open QR bills overdue or due within a week (F03), one per bill;
- **loans:** due loan payments (F22), one per payment, with the schedule's interest and repayment;
- **goals:** savings goals behind their plan line or past their target date (F14).

Budgets exceeded or on pace to be are no kind here: they are *Notable This Month* on the same page (F18).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from budget_app.domain import goals as plan
from budget_app.domain import months
from budget_app.i18n import N_, date_label, t, tn
from budget_app.services import bills as bills_svc
from budget_app.services import goals as goal_svc
from budget_app.services import loans as loan_svc
from budget_app.services import recurring as recurring_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context

BALANCES, RECURRING, BILLS, LOANS, GOALS = "balances", "recurring", "bills", "loans", "goals"
KINDS = (BALANCES, RECURRING, BILLS, LOANS, GOALS)  # the order in Settings, and in the Due list within a tone
TITLES = {
    BALANCES: N_("Balances to record"),
    RECURRING: N_("Recurring transactions due"),
    BILLS: N_("Bills due"),
    LOANS: N_("Loan payments due"),
    GOALS: N_("Goals behind or overdue"),
}
DESCRIPTIONS = {
    BALANCES: N_("Accounts without a month-end balance for last month."),
    RECURRING: N_("Occurrences of recurring transactions that are due."),
    BILLS: N_("Open QR bills that are overdue or due within a week."),
    LOANS: N_("Loan payments that are due, as the schedule plans them."),
    GOALS: N_("Savings goals behind their plan or past their target date."),
}
LINKS = {BALANCES: "/budget", RECURRING: "/recurring", BILLS: "/bills", LOANS: "/loans", GOALS: "/goals"}
WARN, LATE = "warn", "late"


@dataclass(frozen=True)
class Reminder:
    """One item: its kind, a title, a line below it, an amount (or None), where it is dealt with, and a tone
    (`late` for what is overdue, `warn` for what is coming).

    What the item's action needs: `ref` is the account, series, bill, loan or goal it is about; `on` the earliest
    due occurrence or payment (recurring, loans: the one to book next; `times` says how many are due, and `amount`
    is that one's); `month` the month without a balance (balances)."""

    kind: str
    title: str
    sub: str
    link: str
    amount: float | None = None
    currency: str = ""
    tone: str = WARN
    ref: str = ""
    on: date | None = None
    month: str = ""
    times: int = 1


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
                    ref=row.account.id,
                    month=month,
                )
            )
    return out


def _when(on: date, day: date, times: int, several: str) -> tuple[str, str]:
    """ "Due since 1 Oct 2026" (late) or "Due today", or with several due the kind's `several` ("3 occurrences due
    since 1 Aug 2026"); and the tone."""
    if times > 1:
        return several, LATE
    if on < day:
        return t("Due since {date}", date=date_label(on)), LATE
    return t("Due today"), WARN


def _earliest(dates: list[tuple[str, date]]) -> dict[str, tuple[date, int]]:
    """Per series or loan, its earliest due date and how many are due, in the order of those dates."""
    out: dict[str, tuple[date, int]] = {}
    for key, on in dates:
        first, n = out.get(key, (on, 0))
        out[key] = (first, n + 1)
    return out


def recurring(ctx: Context, today: date | None = None) -> list[Reminder]:
    """One item per series with something due: the earliest occurrence, which is the one to book next."""
    day = _today(today)
    due = recurring_svc.due(ctx, day)
    series = {occ.series.id: occ.series for occ in due}
    out = []
    for sid, (on, n) in _earliest([(occ.series.id, occ.date) for occ in due]).items():
        s = series[sid]
        several = tn("{n} occurrence due since {date}", "{n} occurrences due since {date}", n, date=date_label(on))
        when, tone = _when(on, day, n, several)
        out.append(
            Reminder(
                RECURRING,
                recurring_svc.title(ctx, s),
                when,
                LINKS[RECURRING],
                s.amount,
                s.currency,
                tone,
                ref=sid,
                on=on,
                times=n,
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
        out.append(Reminder(BILLS, bill.creditor, when, LINKS[BILLS], amount, bill.currency, tone, ref=bill.id))
    return out


def loans(ctx: Context, today: date | None = None) -> list[Reminder]:
    """One item per loan with payments due (F22): the earliest, with the schedule's interest and repayment together
    as its amount."""
    day = _today(today)
    due = loan_svc.due(ctx, day)
    first = {d.loan.id: d for d in due if d.first}
    out = []
    for lid, (on, n) in _earliest([(d.loan.id, d.date) for d in due]).items():
        d = first[lid]
        acc = ctx.store.accounts.get(d.loan.account_id)
        several = tn("{n} payment due since {date}", "{n} payments due since {date}", n, date=date_label(on))
        when, tone = _when(on, day, n, several)
        out.append(
            Reminder(
                LOANS,
                loan_svc.title(ctx, d.loan),
                when,
                LINKS[LOANS],
                round(d.interest + d.amortization, 2),
                acc.currency if acc else "",
                tone,
                ref=lid,
                on=on,
                times=n,
            )
        )
    return out


def goals(ctx: Context, today: date | None = None) -> list[Reminder]:
    day = _today(today)
    out = []
    for st in goal_svc.statuses(ctx, today=day):
        p, goal = st.progress, st.goal
        if p.status == plan.OVERDUE and goal.due is not None:
            to_go = _money(p.remaining, goal.currency)
            sub = t("Overdue since {date} · {amount} to go", date=date_label(goal.due), amount=to_go)
            out.append(Reminder(GOALS, goal.name, sub, LINKS[GOALS], p.current, goal.currency, LATE, ref=goal.id))
        elif p.status == plan.BEHIND and p.expected is not None:
            sub = t("Behind: {amount} short of the plan line", amount=_money(p.expected - p.current, goal.currency))
            out.append(Reminder(GOALS, goal.name, sub, LINKS[GOALS], p.current, goal.currency, WARN, ref=goal.id))
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
        LOANS: lambda: loans(ctx, day),
        GOALS: lambda: goals(ctx, day),
    }
    out: dict[str, list[Reminder]] = {}
    for kind in KINDS:
        if not enabled(ctx, kind):
            continue
        if items := builders[kind]():
            out[kind] = items
    return out


def due_list(ctx: Context, view_cur: str, today: date | None = None) -> list[Reminder]:
    """The Overview's Due list: every item of the kinds turned on, what is overdue first, then in the order of
    `KINDS` (a series' or loan's occurrences stay in date order)."""
    found = [item for items in reminders(ctx, view_cur, today).values() for item in items]
    return sorted(found, key=lambda r: r.tone != LATE)


def count(ctx: Context, today: date | None = None) -> int:
    """How many items the Due list holds (the sidebar badge beside Overview), in the display currency."""
    return len(due_list(ctx, ctx.store.settings.display_currency, today))
