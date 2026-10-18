"""Page for adding (recurring) budget entries."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import ValidationError
from budget_app.ui.layout import card, data_month_range, error, frame, month_options, success
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


def page() -> None:
    with frame("Add budget entry", "/add-budget") as ctx:
        horizon = ctx.horizon()
        start_range, _ = data_month_range(ctx)
        with card():
            with ui.row().classes("gap-3 items-start flex-wrap"):
                start = (
                    ui.select(
                        month_options(start_range, max(horizon, months.current_month())),
                        value=months.current_month(),
                        label="First month",
                    )
                    .props("dense outlined")
                    .classes("w-40")
                )
                end = ui.select({}, label="Final month").props("dense outlined").classes("w-48")
                recur = ui.select(RECURRENCE, value=1, label="Recurrence").props("dense outlined").classes("w-44")
            ui.label(
                f"The final month can be at most the budget horizon ({months.label(horizon)}, see Settings)."
                + (" The horizon has passed – extend it in Settings first." if horizon < months.current_month() else "")
            ).classes("text-xs text-slate-500 -mt-2")
            typ = ui.toggle({BUDGET_EXPENDITURE: "Expenditure", BUDGET_INCOME: "Income"}, value=BUDGET_EXPENDITURE)
            typ.props("no-caps unelevated toggle-color=primary")
            with ui.row().classes("gap-3 items-start flex-wrap"):
                cur = currency_select(ctx, ctx.store.settings.display_currency)
                amount = ui.number("Amount", min=0, format="%.2f").props("dense outlined").classes("w-40")
                exp_cat = expenditure_category_select()
                inc_cat = income_category_select(ctx)
            overwrite = ui.checkbox("Overwrite existing budget entries of this category in the selected months")
            comment = ui.textarea("Comment (optional)").props("dense outlined autogrow").classes("w-full max-w-2xl")
            with ui.row().classes("items-center gap-2"):
                save_btn = ui.button("Save", icon="save")
                status = ui.label().classes("text-sm text-slate-500")

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
                span = months.label(ms[0]) + (f" … {months.label(ms[-1])}" if len(ms) > 1 else "")
                status.set_text(
                    f"Creates {len(ms)} monthly entr{'y' if len(ms) == 1 else 'ies'}: {span}"
                    + (" (continued when the horizon is extended)" if end.value == FOREVER else "")
                )
            except ValidationError as exc:
                save_btn.disable()
                status.set_text(str(exc))

        def save() -> None:
            try:
                rows = budget_svc.create(ctx, read())
            except ValidationError as exc:
                error(str(exc))
                return
            success(f"Saved {len(rows)} budget entries")
            amount.value = None
            exp_cat.value = None
            inc_cat.value = None
            comment.value = ""
            overwrite.value = False
            changed()

        save_btn.on_click(save)
        for el in (start, end, recur, typ, cur, amount, exp_cat, inc_cat, overwrite):
            el.on_value_change(changed)
        changed()
