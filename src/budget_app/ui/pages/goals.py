"""Savings goals (F14): every goal with its progress bar, the monthly amount needed and whether it is on track; add,
edit, archive, restore and delete through a sheet, each one undoable step. `goals_card` is the Overview's card."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.domain import goals as plan
from budget_app.domain import months
from budget_app.domain.models import GOAL_AMOUNT, GOAL_MONTHS, Goal
from budget_app.i18n import t, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import goals as goal_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.goals import GoalInput, GoalStatus
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    frame,
    on_data_changed,
    parse_date,
    segmented,
    sheet,
    sheet_buttons,
    undoable,
)
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import account_options, currency_select

ROW_ACTION = "flat dense round size=sm"
TONE = {plan.REACHED: "text-pos", plan.BEHIND: "text-neg", plan.OVERDUE: "text-neg"}


# ------------------------------------------------------------------ texts
def of_line(st: GoalStatus) -> str:
    """ "1'250.00 of 4'000.00 CHF" (only the amount while there is no target)."""
    p, cur = st.progress, st.goal.currency
    if p.status == plan.NO_TARGET:
        return fmt(p.current, cur)
    return t("{actual} of {budget}", actual=fmt(p.current), budget=fmt(p.target, cur))


def percent(st: GoalStatus) -> str:
    return "" if st.progress.status == plan.NO_TARGET else f"{st.progress.percent} %"


def amount_line(st: GoalStatus) -> str:
    """ "1'250.00 of 4'000.00 CHF · 31 %"."""
    return " · ".join(x for x in (of_line(st), percent(st)) if x)


def plan_line(st: GoalStatus) -> str:
    """What the goal needs: the monthly amount and whether it is on track, reached, overdue or open-ended."""
    p, cur, due = st.progress, st.goal.currency, st.goal.due
    if p.status == plan.NO_TARGET:
        return t("No spending recorded in the last 12 months yet")
    if p.status == plan.REACHED:
        return t("Reached")
    rest = t("{amount} to go", amount=fmt(p.remaining, cur))
    if p.status == plan.NO_DATE or due is None:
        return " · ".join([rest, t("no target date")])
    if p.status == plan.OVERDUE:
        return " · ".join([t("Overdue since {date}", date=i18n.date_label(due)), rest])
    a_month = t("{amount} a month until {date}", amount=fmt(p.needed, cur), date=i18n.date_label(due))
    return " · ".join([a_month, t("on track") if p.status == plan.ON_TRACK else t("behind")])


def source_line(ctx: Context, goal: Goal, today: date | None = None) -> str:
    """Where the progress comes from and what the target is made of, and whether it is reserved."""
    parts = []
    acc = ctx.store.accounts.get(goal.account_id) if goal.account_id else None
    parts.append(t("Balance of {account}", account=acc_svc.account_label(acc)) if acc else t("Saved by hand"))
    if goal.kind == GOAL_MONTHS:
        average = goal_svc.monthly_spending(ctx, goal.currency, today)
        parts.append(
            tn(
                "{n} month of spending at {amount}",
                "{n} months of spending at {amount}",
                goal.months,
                amount=fmt(average, goal.currency),
            )
        )
    if goal.reserve:
        parts.append(t("reserved in the projection"))
    return " · ".join(parts)


def meter(st: GoalStatus) -> None:
    done = " done" if st.progress.status == plan.REACHED else ""
    with ui.element("div").classes("meter" + done):
        ui.element("div").style(f"width: {st.progress.ratio * 100:.1f}%")


# ------------------------------------------------------------------ the sheet
def open_goal_sheet(ctx: Context, on_saved: Callable[[], None], editing: Goal | None = None) -> None:
    """A new goal, or a goal's name, target, date, account and reserve."""
    g = editing
    with sheet(t("Edit Goal") if g else t("New Goal"), width="30rem") as dlg:
        name = (
            ui.input(t("Name"), value=g.name if g else "", placeholder=t("e.g. Holiday Italy"))
            .props("dense outlined autofocus")
            .classes("w-full")
        )
        kind = segmented(
            {GOAL_AMOUNT: t("Amount"), GOAL_MONTHS: t("Months of Spending")}, g.kind if g else GOAL_AMOUNT, full=True
        )
        with ui.row().classes("w-full gap-2 items-center no-wrap"):
            target = (
                ui.number(t("Target"), value=(g.target or None) if g else None, min=0, format="%.2f")
                .props("dense outlined inputmode=decimal")
                .classes("flex-1 min-w-0")
            )
            n_months = (
                ui.number(t("Months"), value=(g.months or None) if g else 3, min=1, max=goal_svc.MAX_MONTHS, step=1)
                .props("dense outlined inputmode=numeric")
                .classes("flex-1 min-w-0")
            )
            cur = currency_select(ctx, g.currency if g else ctx.store.settings.display_currency)
        months_hint = ui.label().classes("caption -mt-2 px-1")
        due = date_input(t("Target Date"), g.target_date if g else "").props("clearable").classes("w-full")
        accounts = {"": t("None – I enter what I have saved")} | account_options(ctx, holdings=True)
        account = (
            ui.select(accounts, value=g.account_id if g and g.account_id in accounts else "", label=t("Linked Account"))
            .props("dense outlined options-dense")
            .classes("w-full")
        )
        saved = (
            ui.number(t("Saved So Far"), value=g.saved if g else None, min=0, format="%.2f")
            .props("dense outlined inputmode=decimal")
            .classes("w-full")
        )
        ui.label(t("A linked account’s balance is the goal’s progress, converted at the latest rate.")).classes(
            "caption -mt-2 px-1"
        )
        reserve = ui.switch(t("Reserve in the projection"), value=g.reserve if g else False).props("dense")
        ui.label(
            t(
                "The Overview then also shows the projected wealth without this goal’s money: what it holds now, "
                "growing by the monthly amount needed until the target date."
            )
        ).classes("caption -mt-3 px-1")

        def update() -> None:
            by_months = kind.value == GOAL_MONTHS
            target.set_visibility(not by_months)
            n_months.set_visibility(by_months)
            months_hint.set_visibility(by_months)
            saved.set_visibility(not account.value)
            if by_months and cur.value:
                average = goal_svc.monthly_spending(ctx, cur.value)
                n = int(n_months.value or 0)
                months_hint.set_text(
                    t(
                        "{amount}: {n} × the average spending of the last 12 complete months ({average} a month)",
                        amount=fmt(n * average, cur.value),
                        n=n,
                        average=fmt(average, cur.value),
                    )
                )

        for control in (kind, n_months, cur, account):
            control.on_value_change(update)
        update()

        def save() -> None:
            inp = GoalInput(
                name=name.value or "",
                currency=cur.value or "",
                kind=kind.value,
                target=target.value,
                months=n_months.value,
                target_date=parse_date(due.value),
                account_id=account.value or "",
                saved=saved.value,
                reserve=bool(reserve.value),
            )
            try:
                done = goal_svc.edit(ctx, g.id, inp) if g else goal_svc.add(ctx, inp)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Saved “{name}”", name=done.name))
            on_saved()

        sheet_buttons(dlg, t("Save") if g else t("Add Goal"), save)


# ------------------------------------------------------------------ the Overview card
def goals_card(ctx: Context, view_cur: str) -> None:
    """The first three goals not archived, and with goals reserved in the projection what is free at the horizon.
    Nothing without goals."""
    shown = goal_svc.top_open(ctx)
    if not shown:
        return
    with ui.element("div").classes("surface flex flex-col gap-3 goals-card"):
        with ui.row().classes("w-full items-baseline gap-2"):
            ui.label(t("Goals")).classes("section-title")
            ui.space()
            ui.link(t("All Goals"), "/goals").classes("link-btn no-underline text-[13px]")
        with ui.element("div").classes("goal-grid"):
            for st in shown:
                with ui.column().classes("gap-1 min-w-0"):
                    with ui.row().classes("w-full items-baseline gap-2 no-wrap"):
                        ui.label(st.goal.name).classes("flex-1 min-w-0 truncate text-[14px] font-medium")
                        ui.label(percent(st)).classes("goal-amount caption")
                    meter(st)
                    ui.label(of_line(st)).classes("goal-amount caption")
                    ui.label(plan_line(st)).classes("caption " + TONE.get(st.progress.status, ""))
        if goal_svc.reserving(ctx):
            horizon = ctx.horizon()
            reserved = goal_svc.reserve_at(ctx, horizon, view_cur)
            free = wealth_svc.projected_wealth(ctx, horizon, view_cur) - reserved
            ui.label(
                t(
                    "Projected for {month}: {free} free after {reserved} reserved for goals",
                    month=months.label(horizon),
                    free=fmt(free, view_cur),
                    reserved=fmt(reserved, view_cur),
                )
            ).classes("caption")


# ------------------------------------------------------------------ the page
def page() -> None:
    with frame(t("Goals"), "/goals", narrow=True) as (ctx, actions):
        with actions:
            ui.button(t("New Goal"), icon="add", on_click=lambda: open_goal_sheet(ctx, refresh)).props("unelevated")
        ui.label(
            t(
                "Save towards a holiday, a car or an emergency fund. Link a goal to an account to follow its "
                "balance, or enter what you have saved; with a target date you see what to put aside each month."
            )
        ).classes("caption -mt-2")

        @ui.refreshable
        def goal_list() -> None:
            rows = goal_svc.statuses(ctx)
            with ui.column().classes("w-full gap-0"):
                ui.label(tn("{n} goal", "{n} goals", len(rows))).classes("group-header")
                with ui.element("div").classes("group"):
                    if not rows:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No goals yet.")).classes("text-2")
                    for st in rows:
                        _row(st)

        def _row(st: GoalStatus) -> None:
            goal = st.goal
            with ui.element("div").classes("group-row items-start py-2.5"):
                with ui.column().classes("gap-1 flex-1 min-w-0"):
                    with ui.row().classes("w-full items-baseline gap-2"):
                        ui.label(goal.name).classes("flex-1 min-w-[8rem] truncate font-medium")
                        ui.label(amount_line(st)).classes("goal-amount")
                    meter(st)
                    ui.label(plan_line(st)).classes("caption " + TONE.get(st.progress.status, ""))
                    ui.label(source_line(ctx, goal)).classes("caption")
                with ui.row().classes("row-actions gap-0 no-wrap"):
                    ui.button(icon="o_edit", on_click=lambda: open_goal_sheet(ctx, refresh, goal)).props(
                        ROW_ACTION
                    ).classes("text-primary").tooltip(t("Edit"))
                    ui.button(icon="o_archive", on_click=lambda: archive(goal)).props(ROW_ACTION).classes(
                        "text-primary"
                    ).tooltip(t("Archive"))
                    ui.button(icon="o_delete", on_click=lambda: delete(goal)).props(ROW_ACTION).classes(
                        "text-negative"
                    ).tooltip(t("Delete"))

        @ui.refreshable
        def archived_list() -> None:
            rows = goal_svc.statuses(ctx, archived=True)
            if not rows:
                return
            title = tn("Archived · {n} goal", "Archived · {n} goals", len(rows))
            with ui.expansion(title).classes("w-full group closed-bills").props("dense"):
                for st in rows:
                    with ui.element("div").classes("group-row flex-wrap"):
                        with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                            ui.label(st.goal.name).classes("truncate")
                            ui.label(amount_line(st)).classes("caption")
                        with ui.row().classes("items-center gap-1 no-wrap ml-auto"):
                            ui.button(t("Restore"), on_click=lambda g=st.goal: restore(g)).props(
                                "flat dense no-caps"
                            ).classes("text-primary")
                            ui.button(icon="o_delete", on_click=lambda g=st.goal: delete(g)).props(ROW_ACTION).classes(
                                "text-negative"
                            ).tooltip(t("Delete"))

        def refresh() -> None:
            goal_list.refresh()
            archived_list.refresh()

        def act(fn: Callable[[Context, str], object], goal: Goal, message: str) -> None:
            try:
                fn(ctx, goal.id)
            except ValidationError as exc:
                error(str(exc))
                refresh()
                return
            undoable(message)
            refresh()

        def archive(goal: Goal) -> None:
            act(goal_svc.archive, goal, t("Archived “{name}”", name=goal.name))

        def restore(goal: Goal) -> None:
            act(goal_svc.restore, goal, t("Restored “{name}”", name=goal.name))

        def delete(goal: Goal) -> None:
            confirm(
                t("Delete “{name}”?", name=goal.name),
                lambda: act(goal_svc.delete, goal, t("Deleted {name}", name=goal.name)),
                detail=t("Archive it instead to keep it out of the way. Accounts and transactions don’t change."),
            )

        goal_list()
        archived_list()
        on_data_changed(refresh)
