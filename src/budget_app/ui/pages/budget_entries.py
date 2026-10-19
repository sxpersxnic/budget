"""Budget entry overview: filter, sort, edit and delete."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry
from budget_app.i18n import N_, t, tn
from budget_app.services import budget as budget_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import (
    data_month_range,
    error,
    frame,
    month_options,
    on_data_changed,
    sheet,
    sheet_buttons,
    success,
)
from budget_app.ui.pages.add_budget import FOREVER, RECURRENCE, open_budget_sheet, recurrence_options, until_options
from budget_app.ui.widgets.grid import confirm, export_menu, make_grid, money_col, month_col, num_col
from budget_app.ui.widgets.selects import currency_select, expenditure_category_select, income_category_select

TYPES = {BUDGET_EXPENDITURE: N_("Expenditure"), BUDGET_INCOME: N_("Income")}


def _columns() -> list[dict]:
    return [
        month_col("month", t("Month"), sort="asc", width=120),
        {"field": "category", "headerName": t("Category"), "width": 300, "flex": 1, "minWidth": 180},
        money_col("amount", t("Amount"), width=150),
        {"field": "type", "headerName": t("Type"), "width": 120, "cellClass": "muted"},
        num_col("amount_chf", t("In CHF"), width=120, cellClass="muted"),
        {"field": "recurrence", "headerName": t("Repeats"), "width": 170, "cellClass": "muted"},
        {"field": "comment", "headerName": t("Note"), "width": 220},
    ]


def category_label(ctx: Context, b: BudgetEntry) -> str:
    if b.type == BUDGET_INCOME:
        cat = ctx.store.income_categories.get(b.category)
        return cat.name if cat else b.category
    return get_coicop().label(b.category)


def edit_dialog(ctx: Context, entry: BudgetEntry, on_saved) -> None:
    later = budget_svc.later_in_series(ctx, entry)
    sched = budget_svc.schedule_of(ctx, entry)
    until_value = FOREVER if sched.forever else sched.end_month
    until_opts = until_options(entry.month, ctx.horizon(), extra=sched.end_month)
    with sheet(t("Budget for {month}", month=months.label(entry.month)), width="30rem") as dlg:
        if entry.type == BUDGET_EXPENDITURE:
            cat = expenditure_category_select(entry.category)
        else:
            cat = income_category_select(ctx, entry.category)
        with ui.row().classes("w-full gap-2 no-wrap items-center"):
            amount = (
                ui.number(t("Amount"), value=entry.amount, min=0, format="%.2f")
                .props("dense outlined")
                .classes("flex-1 min-w-0")
            )
            cur = currency_select(ctx, entry.currency)
        with ui.row().classes("w-full gap-2 no-wrap"):
            until = (
                ui.select(until_opts, value=until_value, label=t("Until"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
            recur = (
                ui.select(recurrence_options(), value=sched.recur_months, label=t("Repeat"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
        replan_hint = ui.label(
            t("Changing Until or Repeat replans this budget from {month} on.", month=months.label(entry.month))
        ).classes("caption px-1 -mt-1")
        comment = ui.textarea(t("Note"), value=entry.comment).props("dense outlined autogrow rows=1").classes("w-full")
        following = (
            ui.switch(
                tn(
                    "Also change the {n} later month of this series",
                    "Also change the {n} later months of this series",
                    len(later),
                )
            ).props("dense")
            if later
            else None
        )

        def replanned() -> bool:
            return until.value != until_value or int(recur.value or 1) != sched.recur_months

        def show_replan() -> None:
            replan_hint.set_visibility(replanned())
            if following is not None:
                following.set_visibility(not replanned())  # a replan always covers the later months

        until.on_value_change(show_replan)
        recur.on_value_change(show_replan)
        show_replan()

        def save() -> None:
            try:
                if replanned():
                    budget_svc.reschedule(
                        ctx,
                        entry.id,
                        BudgetInput(
                            end_month="" if until.value == FOREVER else until.value or "",
                            forever=until.value == FOREVER,
                            recur_months=int(recur.value or 1),
                            currency=cur.value or "",
                            amount=amount.value,
                            category=cat.value or "",
                            comment=comment.value or "",
                        ),
                    )
                else:
                    for target in [entry] + (later if following and following.value else []):
                        budget_svc.update_entry(ctx, target.id, amount.value, cur.value, comment.value, cat.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            success(t("Budget updated"))
            on_saved()

        sheet_buttons(dlg, t("Save"), save)


def page() -> None:
    with frame(t("Budget Plan"), "/budget-entries", subtitle=t("Planned income and spending, month by month")) as (
        ctx,
        actions,
    ):
        first, last = data_month_range(ctx)
        with actions:
            text = (
                ui.input(placeholder=t("Search")).props("dense outlined clearable").classes("search w-64 max-sm:w-full")
            )
            with text.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
            filter_btn = ui.button(t("Filters"), icon="o_filter_list").props("flat").classes("text-primary")
            export_menu("budget-plan", t("Budget Plan"), _columns, lambda: shown)
            ui.button(t("New Budget Entry"), icon="add", on_click=lambda: open_budget_sheet(ctx, apply_filters)).props(
                "unelevated"
            )
        with ui.row().classes("w-full items-start gap-2 flex-wrap") as filters:
            m_from = (
                ui.select(month_options(first, last), label=t("From"), clearable=True)
                .props("dense outlined options-dense")
                .classes("w-40")
            )
            m_to = (
                ui.select(month_options(first, last), label=t("To"), clearable=True)
                .props("dense outlined options-dense")
                .classes("w-40")
            )
            a_min = ui.number(t("Min amount")).props("dense outlined").classes("w-32")
            a_max = ui.number(t("Max amount")).props("dense outlined").classes("w-32")
            typ = (
                ui.select({k: t(v) for k, v in TYPES.items()}, label=t("Type"), clearable=True)
                .props("dense outlined")
                .classes("w-40")
            )
        filters.set_visibility(False)
        filter_btn.on_click(lambda: filters.set_visibility(not filters.visible))
        with ui.element("div").classes("selection-bar") as bar:
            selected_label = ui.label().classes("font-medium")
            ui.space()
            ui.button(t("Edit"), icon="o_edit", on_click=lambda: edit_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-primary"
            )
            ui.button(t("Delete"), icon="o_delete", on_click=lambda: delete_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-negative"
            )
        bar.set_visibility(False)
        grid = make_grid(_columns())
        count = ui.label().classes("caption px-1")
        shown: list[dict] = []  # the rows the filters let through, for the export

        def apply_filters() -> None:
            q = (text.value or "").lower().strip()
            rows = []
            for b in ctx.store.budget.all():
                if (m_from.value and b.month < m_from.value) or (m_to.value and b.month > m_to.value):
                    continue
                if (a_min.value is not None and b.amount < a_min.value) or (
                    a_max.value is not None and b.amount > a_max.value
                ):
                    continue
                if typ.value and b.type != typ.value:
                    continue
                label = category_label(ctx, b)
                if q and q not in f"{label} {b.comment}".lower():
                    continue
                rec = (
                    t(RECURRENCE[b.recur_months])
                    if b.recur_months in RECURRENCE
                    else t("Every {n} months", n=b.recur_months)
                )
                rows.append(
                    {
                        "id": b.id,
                        "month": b.month,
                        "type": t(TYPES[b.type]) if b.type in TYPES else b.type,
                        "_type": b.type,
                        "category": label,
                        "amount": b.amount,
                        "currency": b.currency,
                        "amount_chf": round(b.amount_chf, 2),
                        "recurrence": t("{repeat}, forever", repeat=rec) if b.forever else rec,
                        "comment": b.comment,
                    }
                )
            rows.sort(key=lambda r: (r["month"], r["type"], r["category"]))
            shown[:] = rows
            grid.options["rowData"] = rows
            grid.update()
            count.set_text(tn("{n} budget entry", "{n} budget entries", len(rows)))
            bar.set_visibility(False)

        for el in (m_from, m_to, a_min, a_max, typ, text):
            el.on_value_change(apply_filters)
        apply_filters()
        on_data_changed(apply_filters)

        def open_editor(entry_id: str) -> None:
            entry = ctx.store.budget.get(entry_id)
            if entry:
                edit_dialog(ctx, entry, apply_filters)

        async def selection_changed() -> None:
            rows = await grid.get_selected_rows()
            bar.set_visibility(bool(rows))
            selected_label.set_text(tn("{n} selected", "{n} selected", len(rows)))

        async def edit_selected() -> None:
            rows = await grid.get_selected_rows()
            if len(rows) != 1:
                error(t("Select exactly one budget entry to edit"))
                return
            open_editor(rows[0]["id"])

        async def delete_selected() -> None:
            rows = await grid.get_selected_rows()
            if not rows:
                return

            def do() -> None:
                n = budget_svc.delete(ctx, [r["id"] for r in rows])
                success(tn("Deleted {n} budget entry", "Deleted {n} budget entries", n))
                apply_filters()

            confirm(
                tn("Delete {n} budget entry?", "Delete {n} budget entries?", len(rows)),
                do,
                detail=t("This can’t be undone."),
            )

        grid.on("selectionChanged", selection_changed)
        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
