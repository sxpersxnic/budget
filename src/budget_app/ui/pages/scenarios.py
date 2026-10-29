"""What-if scenarios (F15): every scenario with its changes and where it ends against the plan; sheets for a
scenario and for a change; *Adopt into Plan*; each change one undoable step. The Overview draws the shown ones."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, Scenario, ScenarioChange
from budget_app.i18n import t, tn
from budget_app.services import scenarios as scen_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.scenarios import ChangeInput
from budget_app.ui.layout import (
    data_month_range,
    error,
    fmt,
    frame,
    month_options,
    named,
    on_data_changed,
    segmented,
    sheet,
    sheet_buttons,
    undoable,
)
from budget_app.ui.pages.add_budget import FOREVER, recurrence_label, recurrence_options, until_options
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import currency_select, expenditure_category_select, income_category_select

ROW_ACTION = "flat dense round size=sm"


# ------------------------------------------------------------------ texts
def span_text(ctx: Context, change: ScenarioChange) -> str:
    """ "Jan 2027 – Jun 2027", "Mar 2027" for one month, or "from Jan 2027 to the end of the plan"."""
    if not change.end_month:
        return t("from {month} to the end of the plan", month=months.label(change.start_month))
    if change.end_month == change.start_month:
        return months.label(change.start_month)
    return f"{months.label(change.start_month)} – {months.label(change.end_month)}"


def change_line(ctx: Context, change: ScenarioChange) -> str:
    """ "4'960.00 CHF every month · from Jan 2027 to the end of the plan", or "No budget · Mar 2027"."""
    if change.amount <= scen_svc.EPS:
        amount = t("No budget")
    else:
        amount = fmt(change.amount, change.currency)
        if len(scen_svc.change_months(ctx, change)) > 1:
            amount = f"{amount} {recurrence_label(change.recur_months).lower()}"
    return " · ".join([amount, span_text(ctx, change)])


def outcome_line(ctx: Context, scenario: Scenario, view_cur: str) -> tuple[str, str]:
    """ "Projected for Dec 2027: 48'200.00 CHF · −9'600.00 CHF against the plan", and its colour class."""
    out = scen_svc.outcome(ctx, scenario.id, view_cur)
    delta = out.delta
    month, amount = months.label(out.month), fmt(out.scenario, view_cur)
    if abs(delta) < scen_svc.EPS:
        return t("Projected for {month}: {amount} · as in the plan", month=month, amount=amount), ""
    sign = "+" if delta > 0 else ""
    text = t(
        "Projected for {month}: {amount} · {delta} against the plan",
        month=month,
        amount=amount,
        delta=f"{sign}{fmt(delta, view_cur)}",
    )
    return text, "text-pos" if delta > 0 else "text-neg"


# ------------------------------------------------------------------ sheets
def open_scenario_sheet(ctx: Context, on_saved: Callable[[], None], editing: Scenario | None = None) -> None:
    s = editing
    with sheet(t("Edit Scenario") if s else t("New Scenario"), width="28rem") as dlg:
        name = (
            ui.input(t("Name"), value=s.name if s else "", placeholder=t("e.g. 80 % work from January"))
            .props("dense outlined autofocus")
            .classes("w-full")
        )
        comment = (
            ui.textarea(t("Note"), value=s.comment if s else "", placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )

        def save() -> None:
            try:
                done = (
                    scen_svc.edit(ctx, s.id, name.value or "", comment.value or "")
                    if s
                    else scen_svc.add(ctx, name.value or "", comment.value or "")
                )
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Saved “{name}”", name=done.name))
            on_saved()

        sheet_buttons(dlg, t("Save") if s else t("Add Scenario"), save)
        name.on("keydown.enter", save)


def open_change_sheet(
    ctx: Context, scenario: Scenario, on_saved: Callable[[], None], editing: ScenarioChange | None = None
) -> None:
    """A change of the scenario: the budget of a category in some months, instead of the plan's."""
    c = editing
    horizon = ctx.horizon()
    start_range, _ = data_month_range(ctx)
    view_cur = ctx.store.settings.display_currency
    with sheet(t("Edit Change") if c else t("New Change"), width="32rem") as dlg:
        ui.label(t("In “{name}”", name=scenario.name)).classes("caption -mt-3")
        typ = segmented(
            {BUDGET_EXPENDITURE: t("Expenditure"), BUDGET_INCOME: t("Income")},
            c.type if c else BUDGET_EXPENDITURE,
            full=True,
        )
        with ui.row().classes("w-full gap-2 items-center no-wrap"):
            amount = (
                ui.number(t("Amount"), value=c.amount if c else None, min=0, format="%.2f")
                .props("dense outlined inputmode=decimal")
                .classes("flex-1 min-w-0")
            )
            cur = currency_select(ctx, c.currency if c else view_cur)
        ui.label(t("0 takes the category’s budget out of these months.")).classes("caption -mt-2 px-1")
        exp_cat = expenditure_category_select(c.category if c and c.type == BUDGET_EXPENDITURE else None, ctx=ctx)
        inc_cat = income_category_select(ctx, c.category if c and c.type == BUDGET_INCOME else None)
        with ui.row().classes("w-full gap-2 no-wrap"):
            first = c.start_month if c else months.current_month()
            start = (
                ui.select(
                    month_options(min(start_range, first), max(horizon, months.current_month(), first)),
                    value=first,
                    label=t("From"),
                )
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
            end = (
                ui.select(
                    until_options(first, horizon, extra=c.end_month if c else ""),
                    value=(c.end_month or FOREVER) if c else FOREVER,
                    label=t("Until"),
                )
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
        recur = (
            ui.select(recurrence_options(), value=c.recur_months if c else 1, label=t("Repeat"))
            .props("dense outlined options-dense")
            .classes("w-full")
        )
        comment = (
            ui.textarea(t("Note"), value=c.comment if c else "", placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )
        status = ui.label().classes("caption px-1")

        def read() -> ChangeInput:
            is_exp = typ.value == BUDGET_EXPENDITURE
            return ChangeInput(
                type=typ.value,
                category=(exp_cat.value if is_exp else inc_cat.value) or "",
                amount=amount.value,
                currency=cur.value or "",
                start_month=start.value or "",
                end_month="" if end.value == FOREVER else end.value or "",
                recur_months=int(recur.value or 1),
                comment=comment.value or "",
            )

        def changed() -> None:
            exp_cat.set_visibility(typ.value == BUDGET_EXPENDITURE)
            inc_cat.set_visibility(typ.value == BUDGET_INCOME)
            if start.value:
                opts = until_options(start.value, horizon, extra=c.end_month if c else "")
                if end.value not in opts:
                    end.value = FOREVER
                end.set_options(opts, value=end.value)
            inp = read()
            try:
                ms = scen_svc.validate_change(ctx, inp)
            except ValidationError:
                status.set_text("")
                return
            plan = scen_svc.plan_budget(ctx, inp.type, inp.category, ms[0], inp.currency)
            text, first_month = tn("{n} month", "{n} months", len(ms)), months.label(ms[0])
            if plan > scen_svc.EPS:
                has = t("The plan has {amount} for {month}", amount=fmt(plan, inp.currency), month=first_month)
                text += " · " + has
            else:
                text += " · " + t("The plan has no budget for this category in {month}", month=first_month)
            status.set_text(text)

        for el in (typ, amount, cur, exp_cat, inc_cat, start, end, recur):
            el.on_value_change(changed)
        changed()

        def save() -> None:
            try:
                scen_svc.edit_change(ctx, c.id, read()) if c else scen_svc.add_change(ctx, scenario.id, read())
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Saved the change in “{name}”", name=scenario.name))
            on_saved()

        sheet_buttons(dlg, t("Save") if c else t("Add Change"), save)


# ------------------------------------------------------------------ the page
def page() -> None:
    with frame(t("Scenarios"), "/scenarios", narrow=True) as (ctx, actions):
        with actions:
            new = ui.button(t("New Scenario"), icon="add", on_click=lambda: open_scenario_sheet(ctx, refresh)).props(
                "unelevated"
            )

        @ui.refreshable
        def scenario_list() -> None:
            rows = scen_svc.scenarios(ctx)
            new.set_visibility(bool(rows))  # while there are none, the empty state offers it
            view_cur = ctx.store.settings.display_currency
            with ui.column().classes("w-full gap-5"):
                if not rows:
                    with ui.element("div").classes("group"):
                        empty_state(
                            t("No scenarios yet."),
                            t(
                                "Try a future without touching the plan, such as less salary from January or a car "
                                "in March. The Overview’s wealth chart shows it beside the plan."
                            ),
                            t("New Scenario"),
                            lambda: open_scenario_sheet(ctx, refresh),
                        )
                for scenario in rows:
                    _scenario(scenario, view_cur)

        def _scenario(scenario: Scenario, view_cur: str) -> None:
            rows = scen_svc.changes(ctx, scenario.id)
            with ui.column().classes("w-full gap-1 scenario"):
                with ui.row().classes("w-full items-center gap-2 no-wrap px-1"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(scenario.name).classes("section-title truncate")
                        if scenario.comment:
                            ui.label(scenario.comment).classes("caption")
                    ui.switch(
                        t("On the Overview"),
                        value=scenario.shown,
                        on_change=lambda e, s=scenario: show(s, bool(e.value)),
                    ).props("dense left-label").classes("text-[13px]")
                    named(
                        ui.button(icon="o_edit", on_click=lambda s=scenario: open_scenario_sheet(ctx, refresh, s))
                        .props(ROW_ACTION)
                        .classes("text-primary"),
                        t("Edit"),
                    )
                    named(
                        ui.button(icon="o_delete", on_click=lambda s=scenario: delete(s))
                        .props(ROW_ACTION)
                        .classes("text-negative"),
                        t("Delete"),
                    )
                text, tone = outcome_line(ctx, scenario, view_cur)
                ui.label(text).classes("caption px-1 " + tone)
                with ui.element("div").classes("group w-full"):
                    if not rows:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No changes yet: the scenario is the plan.")).classes("text-2")
                    for change in rows:
                        with ui.element("div").classes("group-row"):
                            with ui.column().classes("gap-0 flex-1 min-w-0"):
                                ui.label(scen_svc.category_label(ctx, change)).classes("truncate")
                                ui.label(change_line(ctx, change)).classes("caption")
                                if change.comment:
                                    ui.label(change.comment).classes("caption")
                            with ui.row().classes("row-actions gap-0 no-wrap"):
                                named(
                                    ui.button(
                                        icon="o_edit",
                                        on_click=lambda c=change, s=scenario: open_change_sheet(ctx, s, refresh, c),
                                    )
                                    .props(ROW_ACTION)
                                    .classes("text-primary"),
                                    t("Edit"),
                                )
                                named(
                                    ui.button(icon="o_delete", on_click=lambda c=change: delete_change(c))
                                    .props(ROW_ACTION)
                                    .classes("text-negative"),
                                    t("Delete"),
                                )
                    with ui.element("div").classes("group-row gap-3 flex-wrap"):
                        ui.button(
                            t("Add Change…"), on_click=lambda s=scenario: open_change_sheet(ctx, s, refresh)
                        ).props("flat dense no-caps").classes("link-btn -ml-2")
                        ui.space()
                        if rows:
                            ui.button(t("Adopt into Plan…"), on_click=lambda s=scenario: adopt(s)).props(
                                "flat dense no-caps"
                            ).classes("link-btn")

        def refresh() -> None:
            scenario_list.refresh()

        def act(fn: Callable[[], object], message: str) -> None:
            try:
                fn()
            except ValidationError as exc:
                error(str(exc))
                refresh()
                return
            undoable(message)
            refresh()

        def show(scenario: Scenario, shown: bool) -> None:
            message = (
                t("“{name}” is shown on the Overview", name=scenario.name)
                if shown
                else t("“{name}” is hidden on the Overview", name=scenario.name)
            )
            act(lambda: scen_svc.set_shown(ctx, scenario.id, shown), message)

        def delete(scenario: Scenario) -> None:
            n = len(scen_svc.changes(ctx, scenario.id))
            confirm(
                t("Delete “{name}”?", name=scenario.name),
                lambda: act(lambda: scen_svc.delete(ctx, scenario.id), t("Deleted {name}", name=scenario.name)),
                detail=tn(
                    "Its {n} change goes with it. The plan doesn’t change.",
                    "Its {n} changes go with it. The plan doesn’t change.",
                    n,
                ),
            )

        def delete_change(change: ScenarioChange) -> None:
            act(
                lambda: scen_svc.delete_change(ctx, change.id),
                t("Removed the change for {category}", category=scen_svc.category_label(ctx, change)),
            )

        def adopt(scenario: Scenario) -> None:
            n = len(scen_svc.changes(ctx, scenario.id))

            def do() -> None:
                try:
                    added = scen_svc.adopt(ctx, scenario.id)
                except ValidationError as exc:
                    error(str(exc))
                    refresh()
                    return
                undoable(
                    tn(
                        "Adopted “{name}”: {n} budget entry",
                        "Adopted “{name}”: {n} budget entries",
                        added,
                        name=scenario.name,
                    )
                )
                refresh()

            confirm(
                t("Adopt “{name}” into the plan?", name=scenario.name),
                do,
                action=t("Adopt"),
                detail=tn(
                    "Its {n} change becomes budget entries and replaces the plan’s budget for that category in its "
                    "months (a change of 0 removes it). The scenario is then removed. This can be undone.",
                    "Its {n} changes become budget entries and replace the plan’s budget for their categories in "
                    "their months (a change of 0 removes it). The scenario is then removed. This can be undone.",
                    n,
                ),
            )

        scenario_list()
        on_data_changed(refresh)
