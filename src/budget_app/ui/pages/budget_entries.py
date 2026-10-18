"""Budget entry overview: filter, sort, edit and delete."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry
from budget_app.services import budget as budget_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import card, data_month_range, error, frame, month_options, success
from budget_app.ui.pages.add_budget import RECURRENCE
from budget_app.ui.widgets.grid import confirm, make_grid, num_col
from budget_app.ui.widgets.selects import currency_select, expenditure_category_select, income_category_select

TYPES = {BUDGET_EXPENDITURE: "Expenditure", BUDGET_INCOME: "Income"}
COLUMNS = [
    {"field": "month", "headerName": "Month", "sort": "asc", "width": 110},
    {"field": "type", "headerName": "Type", "width": 120},
    {"field": "category", "headerName": "Category", "width": 300},
    num_col("amount", "Amount", width=120),
    {"field": "currency", "headerName": "Cur.", "width": 75},
    num_col("amount_chf", "Amount CHF", width=130),
    {"field": "recurrence", "headerName": "Recurrence", "width": 150},
    {"field": "comment", "headerName": "Comment", "width": 240},
]


def category_label(ctx: Context, b: BudgetEntry) -> str:
    if b.type == BUDGET_INCOME:
        cat = ctx.store.income_categories.get(b.category)
        return cat.name if cat else b.category
    return get_coicop().label(b.category)


def edit_dialog(ctx: Context, entry: BudgetEntry, on_saved) -> None:
    with ui.dialog() as dlg, ui.card().classes("min-w-[30rem]"):
        ui.label(f"Edit budget entry · {months.label(entry.month)}").classes("text-lg font-semibold")
        if entry.type == BUDGET_EXPENDITURE:
            cat = expenditure_category_select(entry.category)
        else:
            cat = income_category_select(ctx, entry.category)
        with ui.row().classes("gap-2"):
            amount = (
                ui.number("Amount", value=entry.amount, min=0, format="%.2f").props("dense outlined").classes("w-40")
            )
            cur = currency_select(ctx, entry.currency)
        comment = ui.textarea("Comment", value=entry.comment).props("dense outlined autogrow").classes("w-full")
        later = [
            b
            for b in ctx.store.budget.all()
            if entry.series_id and b.series_id == entry.series_id and b.month > entry.month
        ]
        following = ui.checkbox(f"Also apply to the {len(later)} following month(s) of this series") if later else None

        def save() -> None:
            try:
                for target in [entry] + (later if following and following.value else []):
                    budget_svc.update_entry(ctx, target.id, amount.value, cur.value, comment.value, cat.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            success("Budget entry saved")
            on_saved()

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Save", on_click=save)
    dlg.open()


def page() -> None:
    with frame("Budget entries", "/budget-entries") as ctx:
        first, last = data_month_range(ctx)
        with card():
            with ui.row().classes("w-full items-start gap-2 flex-wrap"):
                m_from = (
                    ui.select(month_options(first, last), label="From month", clearable=True)
                    .props("dense outlined")
                    .classes("w-40")
                )
                m_to = (
                    ui.select(month_options(first, last), label="To month", clearable=True)
                    .props("dense outlined")
                    .classes("w-40")
                )
                a_min = ui.number("Min amount").props("dense outlined").classes("w-32")
                a_max = ui.number("Max amount").props("dense outlined").classes("w-32")
                typ = ui.select(TYPES, label="Type", clearable=True).props("dense outlined").classes("w-40")
                text = ui.input("Search").props("dense outlined clearable").classes("w-56")
            grid = make_grid(COLUMNS)
            with ui.row().classes("items-center gap-2"):
                ui.button("Edit selected", icon="edit", on_click=lambda: edit_selected())  # noqa: PLW0108 - defined below
                ui.button("Delete selected", icon="delete", color="negative", on_click=lambda: delete_selected())  # noqa: PLW0108 - defined below
                count = ui.label().classes("text-sm text-slate-500")

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
                rec = RECURRENCE.get(b.recur_months, f"Every {b.recur_months} months")
                rows.append(
                    {
                        "id": b.id,
                        "month": b.month,
                        "type": TYPES.get(b.type, b.type),
                        "category": label,
                        "amount": b.amount,
                        "currency": b.currency,
                        "amount_chf": round(b.amount_chf, 2),
                        "recurrence": rec + (" · forever" if b.forever else ""),
                        "comment": b.comment,
                    }
                )
            grid.options["rowData"] = rows
            grid.update()
            count.set_text(f"{len(rows)} budget entries")

        for el in (m_from, m_to, a_min, a_max, typ, text):
            el.on_value_change(apply_filters)
        apply_filters()

        def open_editor(entry_id: str) -> None:
            entry = ctx.store.budget.get(entry_id)
            if entry:
                edit_dialog(ctx, entry, apply_filters)

        async def edit_selected() -> None:
            rows = await grid.get_selected_rows()
            if len(rows) != 1:
                error("Select exactly one budget entry to edit")
                return
            open_editor(rows[0]["id"])

        async def delete_selected() -> None:
            rows = await grid.get_selected_rows()
            if not rows:
                error("Select at least one budget entry")
                return

            def do() -> None:
                n = budget_svc.delete(ctx, [r["id"] for r in rows])
                success(f"Deleted {n} budget entries")
                apply_filters()

            confirm(f"Delete {len(rows)} budget entr{'y' if len(rows) == 1 else 'ies'}?", do)

        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
