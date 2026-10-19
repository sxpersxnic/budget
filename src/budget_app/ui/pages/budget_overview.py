"""Budget overview of one month: wealth, accounts, expenditure tree, income, exports."""

from __future__ import annotations

import html

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import ADV, UNACCOUNTED
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry
from budget_app.i18n import t, tc
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import export as export_svc
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import (
    data_month_range,
    error,
    fmt,
    fmt_signed,
    frame,
    metric,
    month_options,
    month_stepper,
    on_data_changed,
    save_file,
    sheet,
    sheet_buttons,
    success,
)
from budget_app.ui.widgets.selects import account_options, currency_select

EPS = 0.005


# ------------------------------------------------------------------ dialogs
def account_entry_dialog(ctx: Context, on_saved, month: str | None = None, account_id: str | None = None) -> None:
    """Add or edit the recorded balance of an account at the end of a month."""
    first, _ = data_month_range(ctx, back=60)
    with sheet(t("Record Balance"), width="26rem") as dlg:
        ui.label(t("What did the account hold at the end of the month?")).classes("footnote -mt-2")
        month_sel = (
            ui.select(
                month_options(first, months.current_month()), value=month or months.previous_month(), label=t("Month")
            )
            .props("dense outlined options-dense")
            .classes("w-full")
        )
        acc_sel = ui.select(account_options(ctx), value=account_id, label=t("Account"), with_input=True)
        acc_sel.props("dense outlined").classes("w-full")
        amount = (
            ui.number(t("Balance"), format="%.2f").props("outlined inputmode=decimal").classes("hero-amount w-full")
        )
        hint = ui.label().classes("caption px-1 -mt-2")

        def load() -> None:
            acc = ctx.store.accounts.get(acc_sel.value) if acc_sel.value else None
            amount.props(f"suffix={acc.currency}" if acc else "suffix=")
            entry = wealth_svc.entry_for(ctx, acc_sel.value, month_sel.value) if acc and month_sel.value else None
            amount.value = entry.amount if entry else None
            hint.set_text(t("Already recorded. Saving replaces it.") if entry else "")
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
            success(t("Balance recorded"))
            on_saved()

        save_btn = sheet_buttons(dlg, t("Save"), save)
        month_sel.on_value_change(load)
        acc_sel.on_value_change(load)
        amount.on_value_change(check)
        load()


def budget_edit_dialog(ctx: Context, month: str, type_: str, category: str, label: str, on_saved) -> None:
    """Edit the budget entries of one category in one month (or add one)."""
    entries: list[BudgetEntry] = budget_svc.entries_for(ctx, month, type_, category)
    with sheet(label, width="30rem") as dlg:
        ui.label(t("Budget for {month}", month=months.label(month))).classes("footnote -mt-2")
        fields = []
        for e in entries:
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                num = (
                    ui.number(t("Amount"), value=e.amount, min=0, format="%.2f")
                    .props("dense outlined")
                    .classes("flex-1 min-w-0")
                )
                cur = currency_select(ctx, e.currency)
                remove = ui.checkbox(t("Remove")).props("dense")
            if e.comment:
                ui.label(e.comment).classes("caption px-1 -mt-2")
            fields.append((e, num, cur, remove))
        ui.label(t("Add to this budget") if entries else t("Amount")).classes("caption px-1 pt-1")
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            new_amount = ui.number(t("Amount"), min=0, format="%.2f").props("dense outlined").classes("flex-1 min-w-0")
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
                        raise ValidationError(t("Every kept amount must be a positive number with a currency"))
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

        sheet_buttons(dlg, t("Save"), save)


# ------------------------------------------------------------------ cells
def _is_zero(v: float | None) -> bool:
    return v is None or abs(v) < EPS


def _amount(v: float | None, extra: str = "") -> None:
    """Zero is shown as a quiet dash so the eye lands on the rows that carry money."""
    if _is_zero(v):
        ui.label("—").classes("r text-3" + extra)
    else:
        ui.label(fmt(v)).classes("r" + extra)


def _meter(actual: float, budget: float, lg: bool = False) -> None:
    with ui.element("div").classes("c-bar"):
        if budget > EPS:
            ratio = actual / budget
            with ui.element("div").classes("meter" + (" over" if ratio > 1 + 1e-9 else "") + (" lg" if lg else "")):
                ui.element("div").style(f"width: {min(ratio, 1) * 100:.1f}%")


# --------------------------------------------------------------------- page
def page() -> None:
    with frame(t("Budget"), "/budget") as (ctx, actions):
        first, last = data_month_range(ctx)
        state = {
            "month": months.current_month(),
            "currency": ctx.store.settings.display_currency,
            "expanded": set(),
            "show_empty": False,
        }

        async def export(kind: str) -> None:
            name = f"statement_{state['month']}_{state['currency']}.{kind}"
            try:
                rep = reports.month_report(ctx, state["month"], state["currency"])
                data = export_svc.statement_xlsx(rep) if kind == "xlsx" else export_svc.statement_pdf(rep)
            except Exception as exc:
                error(t("Export failed: {error}", error=exc))
                return
            await save_file(data, name)

        with actions:
            month_stepper(
                month_options(first, last), state["month"], lambda m: (state.update(month=m), content.refresh())
            )
            currency_select(
                ctx,
                state["currency"],
                label=t("Currency"),
                on_change=lambda e: (state.update(currency=e.value), content.refresh()),
            )
            with (
                ui.button(icon="ios_share").props("flat round").classes("text-primary").tooltip(t("Export statement")),
                ui.menu().props("auto-close"),
            ):
                ui.menu_item(t("Export as Excel…"), on_click=lambda: export("xlsx"))
                ui.menu_item(t("Export as PDF…"), on_click=lambda: export("pdf"))
            ui.button(
                t("Record Balance"), icon="add", on_click=lambda: account_entry_dialog(ctx, content.refresh)
            ).props("unelevated")

        @ui.refreshable
        def content() -> None:
            month, cur = state["month"], state["currency"]
            try:
                rep = reports.month_report(ctx, month, cur)
                current = wealth_svc.current_wealth(ctx, cur)
            except Exception as exc:
                error(t("Cannot compute the overview: {error}", error=exc))
                return

            missing = [r for r in rep.accounts if r.needs_entry]
            if rep.complete and missing:
                with ui.element("div").classes("callout"):
                    ui.icon("o_error_outline")
                    names = ", ".join(r.account.name for r in missing)
                    ui.label(
                        t(
                            "No balance recorded for {names} at the end of {month}.",
                            names=names,
                            month=months.label(month),
                        )
                    ).classes("flex-1")
                    ui.button(
                        t("Record"),
                        on_click=lambda a=missing[0].account.id: account_entry_dialog(ctx, content.refresh, month, a),
                    ).props("flat dense").classes("text-primary")

            with ui.element("div").classes("surface"), ui.element("div").classes("metrics").style("--n: 3"):
                metric(
                    t("Current Wealth"),
                    current,
                    cur,
                    t("End of {month}", month=months.label(months.previous_month())),
                    hero=True,
                )
                if rep.complete:
                    end = rep.wealth_end if rep.wealth_end is not None else wealth_svc.wealth(ctx, month, cur)
                    metric(t("Wealth"), end, cur, t("End of {month}", month=months.label(month)))
                else:
                    metric(
                        t("Projected Wealth"), rep.projected_wealth, cur, t("End of {month}", month=months.label(month))
                    )
                net = rep.total_income.actual - rep.total_expenditure.actual
                net_budget = rep.total_income.budget - rep.total_expenditure.budget
                metric(t("Net Income"), net, cur, t("Budgeted {amount}", amount=fmt(net_budget)))

            ledger_card(rep, tc("heading", "Income"), rep.total_income, BUDGET_INCOME, lambda: income_rows(rep))
            ledger_card(
                rep,
                tc("heading", "Expenditure"),
                rep.total_expenditure,
                BUDGET_EXPENDITURE,
                lambda: expenditure_rows(rep),
            )
            if rep.complete:
                accounts_card(rep)

        # ------------------------------------------------------------ ledger
        def ledger_card(rep: reports.MonthReport, title: str, total: reports.Line, type_: str, rows) -> None:
            is_exp = type_ == BUDGET_EXPENDITURE
            with ui.element("div").classes("surface"):
                with ui.row().classes("w-full items-end gap-3 no-wrap pb-1"):
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(title).classes("section-title")
                        ui.label(_summary(total, is_exp, rep.currency)).classes("caption")
                    ui.space()
                    ui.label(fmt(total.actual, rep.currency)).classes("text-[20px] font-semibold num whitespace-nowrap")
                if total.budget > EPS:
                    with ui.element("div").classes("pb-3 pt-1"):
                        ratio = total.actual / total.budget
                        with ui.element("div").classes("meter lg" + (" over" if is_exp and ratio > 1 else "")):
                            ui.element("div").style(f"width: {min(ratio, 1) * 100:.1f}%")
                with ui.element("div").classes("ledger"):
                    with ui.element("div").classes("ledger-row head"):
                        ui.label(t("Category"))
                        ui.element("div").classes("c-bar")
                        ui.label(t("Actual")).classes("r")
                        ui.label(t("Budget")).classes("r")
                        ui.label(t("Difference")).classes("r c-diff")
                        ui.element("div")
                    rows()
                if is_exp:
                    with ui.row().classes("w-full items-center pt-2"):
                        if rep.complete and rep.unaccounted is None:
                            ui.label(
                                t(
                                    "Unaccounted expenditure appears once balances are recorded "
                                    "for this and the previous month."
                                )
                            ).classes("caption")
                        ui.space()
                        ui.switch(
                            t("Show all subcategories"),
                            value=state["show_empty"],
                            on_change=lambda e: (state.update(show_empty=e.value), content.refresh()),
                        ).props("dense").classes("text-[13px] text-2")

        def line_row(rep: reports.MonthReport, line: reports.Line, type_: str, depth: int = 0, children=None) -> None:
            expanded = line.key in state["expanded"]
            empty = _is_zero(line.actual) and _is_zero(line.budget)
            editable = not rep.complete and line.editable and line.key not in ("TOTAL", UNACCOUNTED)
            with ui.element("div").classes("ledger-row" + (" dim" if empty else "")):
                label_cell = ui.element("div").classes("cell-label").style(f"padding-left: {depth * 18}px")
                with label_cell:
                    if children:
                        ui.icon("chevron_right", size="18px").classes("chev" + (" open" if expanded else ""))
                    else:
                        ui.element("span").style("width: 18px; flex: none")
                    code, name = _split_code(line)
                    code_html = f'<span class="code">{html.escape(code)}</span>' if code else ""
                    lbl = ui.html(code_html + html.escape(name), tag="span").classes("name")
                    lbl._props["title"] = line.label  # full name on hover; long COICOP names are truncated
                _meter(line.actual, line.budget)
                _amount(line.actual)
                _amount(None if line.key == UNACCOUNTED else line.budget)
                diff = None if line.key == UNACCOUNTED or empty else line.diff
                over = diff is not None and (diff > EPS if type_ == BUDGET_EXPENDITURE else diff < -EPS)
                ui.label(fmt_signed(diff)).classes("r c-diff " + ("text-neg" if over else "text-2"))
                if editable:
                    ui.button(
                        icon="o_edit",
                        on_click=lambda: budget_edit_dialog(
                            ctx, rep.month, type_, line.key, line.label, content.refresh
                        ),
                    ).props("flat dense round size=sm").classes("edit").tooltip(t("Edit budget"))
                else:
                    ui.element("div")
            if children:

                def toggle() -> None:
                    state["expanded"].symmetric_difference_update({line.key})
                    content.refresh()

                label_cell.classes(add="cursor-pointer").on("click", toggle)

        def expenditure_rows(rep: reports.MonthReport) -> None:
            def walk(line: reports.Line, depth: int) -> None:
                children = rep.children(line.key, include_empty=state["show_empty"]) if line.key != ADV else []
                line_row(rep, line, BUDGET_EXPENDITURE, depth, children)
                if line.key in state["expanded"]:
                    for child in children:
                        walk(child, depth + 1)

            for code in rep.top_expenditure:
                walk(rep.expenditure_tree[code], 0)

        def income_rows(rep: reports.MonthReport) -> None:
            if not rep.income_lines:
                ui.label(t("No income this month.")).classes("caption px-1 py-2")
            for line in rep.income_lines:
                line_row(rep, line, BUDGET_INCOME)
            if not rep.complete:
                opts = {
                    c.id: c.name
                    for c in acc_svc.income_categories(ctx)
                    if c.id not in {line.key for line in rep.income_lines}
                }
                if opts:
                    with (
                        ui.button(t("Budget Another Category"), icon="add")
                        .props("flat dense no-caps")
                        .classes("link-btn mt-1"),
                        ui.menu().props("auto-close"),
                    ):
                        for cid, name in opts.items():
                            ui.menu_item(
                                name,
                                on_click=lambda c=cid, n=name: budget_edit_dialog(
                                    ctx, rep.month, BUDGET_INCOME, c, n, content.refresh
                                ),
                            )

        # ------------------------------------------------------------ accounts
        def accounts_card(rep: reports.MonthReport) -> None:
            with ui.element("div").classes("w-full"):
                ui.label(t("Balances · End of {month}", month=months.label(rep.month))).classes("group-header")
                with ui.element("div").classes("group accounts"):
                    if not rep.accounts:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No balances recorded yet.")).classes("text-2")
                    for r in rep.accounts:
                        foreign = r.account.currency != rep.currency
                        with ui.element("div").classes("group-row acct-row" + (" has-native" if foreign else "")):
                            with ui.column().classes("c-name gap-0"):
                                ui.label(r.account.name).classes("truncate w-full")
                                if r.needs_entry:
                                    ui.label(t("Not recorded, last balance carried over")).classes("caption")
                            if foreign:
                                ui.label(fmt(r.amount, r.account.currency)).classes("c-native num text-2 text-[13px]")
                            ui.label(fmt(r.amount_view, rep.currency)).classes("c-view num font-medium")
                            if r.needs_entry or r.has_entry:
                                ui.button(
                                    t("Record") if r.needs_entry else t("Edit"),
                                    icon="add" if r.needs_entry else "o_edit",
                                    on_click=lambda a=r.account.id: account_entry_dialog(
                                        ctx, content.refresh, rep.month, a
                                    ),
                                ).props("flat dense no-wrap").classes(
                                    "c-action text-primary" + ("" if r.needs_entry else " row-actions")
                                )
                    with ui.element("div").classes("group-row acct-row"):
                        ui.label(t("Total")).classes("c-name font-semibold")
                        ui.label(fmt(sum(r.amount_view for r in rep.accounts), rep.currency)).classes(
                            "c-view num font-semibold"
                        )

        content()
        on_data_changed(content.refresh)


def _summary(total: reports.Line, is_exp: bool, currency: str) -> str:
    if total.budget <= EPS:
        return t("No budget set")
    left = total.budget - total.actual if is_exp else total.actual - total.budget
    if is_exp:
        return (
            t("{amount} left of {budget}", amount=fmt(left), budget=fmt(total.budget, currency))
            if left >= -EPS
            else t("{amount} over the {budget} budget", amount=fmt(-left), budget=fmt(total.budget, currency))
        )
    if left > EPS:
        return t("{amount} above the {budget} budget", amount=fmt(left), budget=fmt(total.budget, currency))
    if left < -EPS:
        return t("{amount} still expected of {budget}", amount=fmt(-left), budget=fmt(total.budget, currency))
    return t("On budget, {budget}", budget=fmt(total.budget, currency))


def _split_code(line: reports.Line) -> tuple[str, str]:
    """'01.1 Food' -> ('01.1', 'Food'); labels without a leading code are returned whole."""
    prefix = f"{line.key} "
    if line.label.startswith(prefix):
        return line.key, line.label[len(prefix) :]
    return "", line.label
