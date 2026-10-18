"""Budget overview of one month: wealth, accounts, expenditure tree, income, exports."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import ADV, UNACCOUNTED
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import export as export_svc
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import card, data_month_range, error, fmt, frame, month_options, success
from budget_app.ui.widgets.selects import account_options, currency_select

GRID = "minmax(0,1fr) repeat(3, minmax(5.5rem, 8.5rem))"
INCOME_GRID = "minmax(0,1fr) repeat(3, minmax(5rem, 7.5rem))"


# ------------------------------------------------------------------ dialogs
def account_entry_dialog(ctx: Context, on_saved, month: str | None = None, account_id: str | None = None) -> None:
    """Add or edit the recorded balance of an account at the end of a month."""
    first, _ = data_month_range(ctx, back=60)
    with ui.dialog() as dlg, ui.card().classes("min-w-[26rem]"):
        ui.label("Budget recording (account balance at month end)").classes("text-lg font-semibold")
        month_sel = (
            ui.select(
                month_options(first, months.current_month()), value=month or months.previous_month(), label="Month"
            )
            .props("dense outlined")
            .classes("w-48")
        )
        acc_sel = ui.select(account_options(ctx), value=account_id, label="Account", with_input=True)
        acc_sel.props("dense outlined").classes("w-full")
        amount = ui.number("Amount", format="%.2f").props("dense outlined").classes("w-48")
        hint = ui.label().classes("text-sm text-slate-500")
        save_btn = ui.button("Save")

        def load() -> None:
            acc = ctx.store.accounts.get(acc_sel.value) if acc_sel.value else None
            amount.props(f"suffix={acc.currency}" if acc else "suffix=")
            entry = wealth_svc.entry_for(ctx, acc_sel.value, month_sel.value) if acc and month_sel.value else None
            amount.value = entry.amount if entry else None
            hint.set_text("Existing entry – saving changes it." if entry else "")
            check()

        def check() -> None:
            save_btn.set_enabled(bool(month_sel.value and acc_sel.value and amount.value is not None))

        def save() -> None:
            try:
                wealth_svc.upsert_entry(ctx, acc_sel.value, month_sel.value, amount.value)
            except Exception as exc:
                error(str(exc))
                return
            dlg.close()
            success("Account balance saved")
            on_saved()

        month_sel.on_value_change(load)
        acc_sel.on_value_change(load)
        amount.on_value_change(check)
        save_btn.on_click(save)
        ui.button("Cancel", on_click=dlg.close).props("flat")
        load()
    dlg.open()


def budget_edit_dialog(ctx: Context, month: str, type_: str, category: str, label: str, on_saved) -> None:
    """Edit the budget entries of one category in one month (or add one)."""
    entries: list[BudgetEntry] = budget_svc.entries_for(ctx, month, type_, category)
    with ui.dialog() as dlg, ui.card().classes("min-w-[30rem]"):
        ui.label(f"Budget · {label} · {months.label(month)}").classes("text-lg font-semibold")
        fields = []
        for e in entries:
            with ui.row().classes("items-center gap-2"):
                num = ui.number("Amount", value=e.amount, min=0, format="%.2f").props("dense outlined").classes("w-40")
                cur = currency_select(ctx, e.currency)
                remove = ui.checkbox("Delete")
                ui.label(e.comment).classes("text-xs text-slate-500")
                fields.append((e, num, cur, remove))
        ui.label("Add amount" if entries else "Budget amount").classes("text-sm text-slate-600 pt-2")
        with ui.row().classes("items-center gap-2"):
            new_amount = ui.number("Amount", min=0, format="%.2f").props("dense outlined").classes("w-40")
            new_cur = currency_select(ctx, ctx.store.settings.display_currency)

        def save() -> None:
            new_entry = None
            if new_amount.value:
                new_entry = BudgetInput(
                    start_month=month,
                    end_month=month,
                    type=type_,
                    currency=new_cur.value,
                    amount=new_amount.value,
                    category=category,
                )
            # validate everything first so that a bad row cannot leave a half-applied change
            try:
                for _, num, cur, remove in fields:
                    if not remove.value and (num.value is None or num.value <= 0 or not cur.value):
                        raise ValidationError("Every kept amount must be a positive number with a currency")
                if new_entry is not None:
                    budget_svc.validate(ctx, new_entry)
            except ValidationError as exc:
                error(str(exc))
                return
            budget_svc.delete(ctx, [e.id for e, _, _, remove in fields if remove.value])
            for e, num, cur, remove in fields:
                if not remove.value and (num.value != e.amount or cur.value != e.currency):
                    budget_svc.update_entry(ctx, e.id, num.value, cur.value)
            if new_entry is not None:
                budget_svc.create(ctx, new_entry)
            dlg.close()
            on_saved()

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Save", on_click=save)
    dlg.open()


# --------------------------------------------------------------------- page
def page() -> None:
    with frame("Budget overview", "/budget") as ctx:
        first, last = data_month_range(ctx)
        state = {
            "month": months.current_month(),
            "currency": ctx.store.settings.display_currency,
            "expanded": set(),
            "show_empty": False,
        }

        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.select(
                month_options(first, last),
                value=state["month"],
                label="Month",
                on_change=lambda e: (state.update(month=e.value), content.refresh()),
            ).props("dense outlined").classes("w-44")
            currency_select(
                ctx,
                state["currency"],
                label="Currency",
                on_change=lambda e: (state.update(currency=e.value), content.refresh()),
            )
            ui.button("Add budget recording", icon="add", on_click=lambda: account_entry_dialog(ctx, content.refresh))
            ui.space()
            ui.button("Excel", icon="table_view", on_click=lambda: export("xlsx")).props("outline")
            ui.button("PDF", icon="picture_as_pdf", on_click=lambda: export("pdf")).props("outline")

        def export(kind: str) -> None:
            rep = reports.month_report(ctx, state["month"], state["currency"])
            name = f"statement_{state['month']}_{state['currency']}.{kind}"
            data = export_svc.statement_xlsx(rep) if kind == "xlsx" else export_svc.statement_pdf(rep)
            ui.download.content(data, name)

        @ui.refreshable
        def content() -> None:
            month, cur = state["month"], state["currency"]
            try:
                rep = reports.month_report(ctx, month, cur)
                current = wealth_svc.current_wealth(ctx, cur)
            except Exception as exc:
                error(f"Cannot compute the overview: {exc}")
                return

            with ui.row().classes("w-full gap-4 flex-wrap"):
                kpi("Current wealth", fmt(current, cur), f"end of {months.label(months.previous_month())}")
                if rep.complete:
                    kpi(
                        "Wealth end of month",
                        fmt(rep.wealth_end if rep.wealth_end is not None else wealth_svc.wealth(ctx, month, cur), cur),
                        months.label(month),
                    )
                else:
                    kpi("Projected wealth", fmt(rep.projected_wealth, cur), f"end of {months.label(month)}")
                kpi("Income", fmt(rep.total_income.actual, cur), f"budget {fmt(rep.total_income.budget, cur)}")
                kpi(
                    "Expenditure",
                    fmt(rep.total_expenditure.actual, cur),
                    f"budget {fmt(rep.total_expenditure.budget, cur)}",
                )

            if rep.complete:
                accounts_card(rep)
            with ui.row().classes("w-full gap-4 items-start flex-nowrap max-lg:flex-wrap"):
                with ui.column().classes("flex-[3] min-w-0 max-lg:w-full"):
                    expenditure_card(rep)
                with ui.column().classes("flex-[2] min-w-0 max-lg:w-full"):
                    income_card(rep)

        def kpi(title: str, value: str, sub: str) -> None:
            with ui.card().classes("shadow-sm min-w-[13rem] flex-1"):
                ui.label(title).classes("text-xs uppercase tracking-wide text-slate-500")
                ui.label(value).classes("text-2xl font-semibold text-slate-800")
                ui.label(sub).classes("text-xs text-slate-400")

        def accounts_card(rep: reports.MonthReport) -> None:
            with card(f"Accounts · {months.label(rep.month)}"):
                if not rep.accounts:
                    ui.label("No balances recorded yet. Use 'Add budget recording'.").classes("text-slate-500")
                    return
                with ui.grid(columns="minmax(14rem,1fr) 10rem 10rem auto").classes(
                    "w-full items-center gap-x-4 gap-y-1"
                ):
                    for h in ("Account", f"Amount ({rep.currency})", "In account currency", ""):
                        ui.label(h).classes("text-xs uppercase text-slate-500")
                    for r in rep.accounts:
                        with ui.row().classes("items-center gap-1"):
                            ui.label(r.account.name)
                            if r.needs_entry:
                                ui.badge("not recorded", color="amber").props("outline")
                        ui.label(fmt(r.amount_view, rep.currency)).classes("text-right")
                        ui.label(fmt(r.amount, r.account.currency)).classes("text-right text-slate-500")
                        if r.needs_entry or r.has_entry:
                            ui.button(
                                "Edit" if r.has_entry else "Add",
                                icon="edit" if r.has_entry else "add",
                                on_click=lambda a=r.account.id: account_entry_dialog(
                                    ctx, content.refresh, rep.month, a
                                ),
                            ).props("flat dense no-caps")
                        else:
                            ui.label()
                    ui.label("Total wealth").classes("font-semibold")
                    ui.label(fmt(sum(r.amount_view for r in rep.accounts), rep.currency)).classes(
                        "text-right font-semibold"
                    )

        def amount_cells(line: reports.Line, rep: reports.MonthReport, type_: str, bold: bool = False) -> None:
            weight = " font-semibold" if bold else ""
            ui.label(fmt(line.actual)).classes("text-right" + weight)
            editable = not rep.complete and line.editable and line.key not in ("TOTAL", UNACCOUNTED)
            with ui.row().classes("justify-end items-center gap-0 flex-nowrap"):
                ui.label(fmt(line.budget) if line.key != UNACCOUNTED else "").classes("text-right" + weight)
                if editable:
                    ui.button(
                        icon="edit",
                        on_click=lambda: budget_edit_dialog(
                            ctx, rep.month, type_, line.key, line.label, content.refresh
                        ),
                    ).props("flat dense round size=sm").classes("text-slate-400")
            diff = line.diff if line.key != UNACCOUNTED else None
            over = diff is not None and ((diff > 0.005) if type_ == BUDGET_EXPENDITURE else (diff < -0.005))
            ui.label(fmt(diff)).classes("text-right" + weight + (" text-negative" if over else " text-slate-600"))

        def expenditure_card(rep: reports.MonthReport) -> None:
            with card():
                with ui.row().classes("w-full items-center"):
                    ui.label(f"Expenditure ({rep.currency})").classes("text-lg font-semibold text-slate-700")
                    ui.space()
                    ui.switch(
                        "Show empty subcategories",
                        value=state["show_empty"],
                        on_change=lambda e: (state.update(show_empty=e.value), content.refresh()),
                    ).props("dense")
                with ui.grid(columns=GRID).classes("w-full items-center gap-x-3 gap-y-0"):
                    for h in ("Category", "Actual", "Budget", "Difference"):
                        ui.label(h).classes("text-xs uppercase text-slate-500 text-right first:text-left")
                    ui.label(rep.total_expenditure.label).classes("font-semibold py-1")
                    amount_cells(rep.total_expenditure, rep, BUDGET_EXPENDITURE, bold=True)
                    for code in rep.top_expenditure:
                        tree_line(rep, rep.expenditure_tree[code], 0)
                if rep.complete and rep.unaccounted is None:
                    ui.label(
                        "Unaccounted expenditure is shown once balances are recorded for this and the previous month."
                    ).classes("text-xs text-slate-400")

        def tree_line(rep: reports.MonthReport, line: reports.Line, depth: int) -> None:
            expanded = line.key in state["expanded"]
            children = rep.children(line.key, include_empty=state["show_empty"]) if line.key != ADV else []

            def toggle() -> None:
                state["expanded"].symmetric_difference_update({line.key})
                content.refresh()

            with (
                ui.row()
                .classes("items-center gap-1 flex-nowrap py-0.5 cursor-pointer hover:bg-slate-50")
                .style(f"padding-left: {depth * 1.2}rem") as label_cell
            ):
                ui.icon(
                    ("expand_more" if expanded else "chevron_right") if children else "remove", size="18px"
                ).classes("text-slate-400" if children else "text-transparent")
                ui.label(line.label).classes("text-sm" + (" font-medium" if depth == 0 else ""))
            if children:
                label_cell.on("click", toggle)
            amount_cells(line, rep, BUDGET_EXPENDITURE)
            if expanded:
                for child in children:
                    tree_line(rep, child, depth + 1)

        def income_card(rep: reports.MonthReport) -> None:
            with card(f"Income ({rep.currency})"):
                with ui.grid(columns=INCOME_GRID).classes("w-full items-center gap-x-3 gap-y-1"):
                    for h in ("Category", "Actual", "Budget", "Difference"):
                        ui.label(h).classes("text-xs uppercase text-slate-500 text-right first:text-left")
                    ui.label(rep.total_income.label).classes("font-semibold")
                    amount_cells(rep.total_income, rep, BUDGET_INCOME, bold=True)
                    for line in rep.income_lines:
                        ui.label(line.label).classes("text-sm")
                        amount_cells(line, rep, BUDGET_INCOME)
                if not rep.complete:
                    with ui.row().classes("items-center gap-2 pt-2"):
                        opts = {
                            c.id: c.name
                            for c in acc_svc.income_categories(ctx)
                            if c.id not in {line.key for line in rep.income_lines}
                        }
                        if opts:
                            sel = (
                                ui.select(opts, label="Budget another income category")
                                .props("dense outlined")
                                .classes("w-64")
                            )

                            def add_income_budget(sel=sel, opts=opts) -> None:
                                if sel.value:
                                    budget_edit_dialog(
                                        ctx, rep.month, BUDGET_INCOME, sel.value, opts[sel.value], content.refresh
                                    )

                            ui.button(icon="add", on_click=add_income_budget).props("flat round dense")

        content()
