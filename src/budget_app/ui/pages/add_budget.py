"""Form for adding (recurring) budget entries, as a page and as a sheet on the Budget Plan page."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import card, data_month_range, error, frame, month_options, segmented, sheet, success
from budget_app.ui.widgets.selects import currency_select, expenditure_category_select, income_category_select

FOREVER = "forever"
RECURRENCE = {
    1: "Every month",
    2: "Every 2 months",
    3: "Every quarter",
    4: "Every 4 months",
    6: "Every half year",
    12: "Every year",
}


def budget_form(ctx: Context, on_saved: Callable[[], None] | None = None, on_cancel=None) -> None:
    horizon = ctx.horizon()
    start_range, _ = data_month_range(ctx)
    touched = {"value": False}
    with ui.column().classes("w-full gap-4"):
        typ = segmented({BUDGET_EXPENDITURE: "Expenditure", BUDGET_INCOME: "Income"}, BUDGET_EXPENDITURE, full=True)
        with ui.row().classes("w-full gap-2 items-center no-wrap"):
            amount = (
                ui.number("Amount", min=0, format="%.2f")
                .props("outlined inputmode=decimal")
                .classes("hero-amount flex-1 min-w-0")
            )
            cur = currency_select(ctx, ctx.store.settings.display_currency)
        exp_cat = expenditure_category_select()
        inc_cat = income_category_select(ctx)
        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("w-full gap-2 no-wrap"):
                start = (
                    ui.select(
                        month_options(start_range, max(horizon, months.current_month())),
                        value=months.current_month(),
                        label="From",
                    )
                    .props("dense outlined options-dense")
                    .classes("flex-1 min-w-0")
                )
                end = ui.select({}, label="Until").props("dense outlined options-dense").classes("flex-1 min-w-0")
            recur = (
                ui.select(RECURRENCE, value=1, label="Repeat").props("dense outlined options-dense").classes("w-full")
            )
            ui.label(
                f"Budgets can run until {months.label(horizon)}. Change this in Settings."
                + (" That month has passed, so extend it first." if horizon < months.current_month() else "")
            ).classes("caption px-1 pt-1")
        overwrite = ui.switch("Replace existing budget for this category in these months").props("dense")
        comment = ui.textarea("Note", placeholder="Optional").props("dense outlined autogrow rows=1").classes("w-full")
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            if on_cancel:
                ui.button("Cancel", on_click=on_cancel).props("flat").classes("text-primary")
            save_btn = ui.button("Add to Budget").props("unelevated")

    def read() -> BudgetInput:
        is_exp = typ.value == BUDGET_EXPENDITURE
        return BudgetInput(
            start_month=start.value or "",
            end_month="" if end.value == FOREVER else end.value or "",
            forever=end.value == FOREVER,
            recur_months=int(recur.value or 1),
            type=typ.value,
            currency=cur.value or "",
            amount=amount.value,
            category=(exp_cat.value if is_exp else inc_cat.value) or "",
            overwrite=bool(overwrite.value),
            comment=comment.value or "",
        )

    def changed() -> None:
        if start.value:
            opts = {FOREVER: "Forever"} | month_options(start.value, horizon)
            if end.value not in opts:
                end.value = None
            end.set_options(opts, value=end.value)
        exp_cat.set_visibility(typ.value == BUDGET_EXPENDITURE)
        inc_cat.set_visibility(typ.value == BUDGET_INCOME)
        try:
            ms = budget_svc.validate(ctx, read())
            save_btn.enable()
            span = months.label(ms[0]) + (f" – {months.label(ms[-1])}" if len(ms) > 1 else "")
            status.set_text(
                f"{len(ms)} month{'' if len(ms) == 1 else 's'}, {span}"
                + (", continued as the plan grows" if end.value == FOREVER else "")
            )
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if touched["value"] else "")

    def save() -> None:
        try:
            rows = budget_svc.create(ctx, read())
        except ValidationError as exc:
            error(str(exc))
            return
        success(f"Added {len(rows)} budget entr{'y' if len(rows) == 1 else 'ies'}")
        amount.value = None
        exp_cat.value = None
        inc_cat.value = None
        comment.value = ""
        overwrite.value = False
        touched["value"] = False
        changed()
        touched["value"] = True
        if on_saved:
            on_saved()

    save_btn.on_click(save)
    for el in (start, end, recur, typ, cur, amount, exp_cat, inc_cat, overwrite):
        el.on_value_change(changed)
    changed()
    touched["value"] = True


def open_budget_sheet(ctx: Context, on_saved: Callable[[], None]) -> None:
    def saved() -> None:
        dlg.close()
        on_saved()

    with sheet("New Budget Entry", width="34rem") as dlg:
        budget_form(ctx, on_saved=saved, on_cancel=dlg.close)


def page() -> None:
    with frame("New Budget Entry", "/add-budget", narrow=True) as (ctx, _), card():
        budget_form(ctx)
