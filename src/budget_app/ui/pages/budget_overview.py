"""Budget overview of one month: wealth, accounts, expenditure tree, income, exports."""

from __future__ import annotations

import html

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import ADV
from budget_app.domain.models import (
    BUDGET_EXPENDITURE,
    BUDGET_INCOME,
    REVAL_FX,
    REVAL_INVEST,
    TX_REVALUATION,
    BudgetEntry,
)
from budget_app.i18n import t, tc, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import export as export_svc
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.ui.layout import (
    data_month_range,
    error,
    fmt,
    fmt_signed,
    frame,
    metric,
    money_html,
    month_options,
    month_stepper,
    on_data_changed,
    save_file,
    sheet,
    sheet_buttons,
    success,
)
from budget_app.ui.pages.add_budget import FOREVER, recurrence_options, until_options
from budget_app.ui.widgets.selects import account_options, currency_select
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

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
    """Edit the budget entries of one category in one month, including how each continues, or add one."""
    entries: list[BudgetEntry] = budget_svc.entries_for(ctx, month, type_, category)
    horizon = ctx.horizon()
    replan_note = t("Changing Until or Repeat replans this budget from {month} on.", month=months.label(month))

    def schedule_row(until_value: str, recur_value: int, extra: str = "") -> tuple[ui.select, ui.select]:
        with ui.row().classes("w-full gap-2 no-wrap"):
            until = (
                ui.select(until_options(month, horizon, extra), value=until_value, label=t("Until"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
            recur = (
                ui.select(recurrence_options(), value=recur_value, label=t("Repeat"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
        return until, recur

    def note_field(value: str) -> ui.textarea:
        return (
            ui.textarea(t("Note"), value=value, placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )

    def schedule_input(until: ui.select, recur: ui.select, amount, currency, note) -> BudgetInput:
        return BudgetInput(
            start_month=month,
            end_month="" if until.value == FOREVER else until.value or "",
            forever=until.value == FOREVER,
            recur_months=int(recur.value or 1),
            type=type_,
            currency=currency or "",
            amount=amount,
            category=category,
            comment=note or "",
        )

    with sheet(label, width="34rem") as dlg:
        ui.label(t("Budget for {month}", month=months.label(month))).classes("footnote -mt-2")
        fields = []
        for e in entries:
            sched = budget_svc.schedule_of(ctx, e)
            later = budget_svc.later_in_series(ctx, e)
            with ui.column().classes("w-full gap-2 subform"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    num = (
                        ui.number(t("Amount"), value=e.amount, min=0, format="%.2f")
                        .props("dense outlined")
                        .classes("flex-1 min-w-0")
                    )
                    cur = currency_select(ctx, e.currency)
                    remove = ui.checkbox(t("Remove")).props("dense")
                start_value = FOREVER if sched.forever else sched.end_month
                until, recur = schedule_row(start_value, sched.recur_months, extra=sched.end_month)
                hint = ui.label(replan_note).classes("caption px-1 -mt-1")
                note = note_field(e.comment)
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

            def show_replan(until=until, recur=recur, hint=hint, following=following, start=(start_value, sched)):
                changed = until.value != start[0] or int(recur.value or 1) != start[1].recur_months
                hint.set_visibility(changed)
                if following:
                    following.set_visibility(not changed)  # a replan always covers the later months

            until.on_value_change(show_replan)
            recur.on_value_change(show_replan)
            show_replan()
            fields.append((e, start_value, sched, num, cur, remove, until, recur, note, following, later))

        ui.label(t("Add to this budget") if entries else t("Amount")).classes("caption px-1 pt-1")
        with ui.column().classes("w-full gap-2" + (" subform" if entries else "")):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                new_amount = (
                    ui.number(t("Amount"), min=0, format="%.2f").props("dense outlined").classes("flex-1 min-w-0")
                )
                new_cur = currency_select(ctx, ctx.store.settings.display_currency)
            new_until, new_recur = schedule_row(month, 1, extra=month)
            new_note = note_field("")

        def save() -> None:
            new_entry = None
            if new_amount.value:
                new_entry = schedule_input(new_until, new_recur, new_amount.value, new_cur.value, new_note.value)
            replans = {}
            # validate everything first so that a bad row cannot leave a half-applied change
            try:
                for e, start_value, sched, num, cur, remove, until, recur, note, _, _ in fields:
                    if remove.value:
                        continue
                    if num.value is None or num.value <= 0 or not cur.value:
                        raise ValidationError(t("Every kept amount must be a positive number with a currency"))
                    if until.value != start_value or int(recur.value or 1) != sched.recur_months:
                        replans[e.id] = schedule_input(until, recur, num.value, cur.value, note.value)
                        budget_svc.validate(ctx, replans[e.id])
                if new_entry is not None:
                    budget_svc.validate(ctx, new_entry)
            except ValidationError as exc:
                error(str(exc))
                return
            budget_svc.delete(ctx, [f[0].id for f in fields if f[5].value])
            for e, _, _, num, cur, remove, _, _, note, following, later in fields:
                if remove.value:
                    continue
                if e.id in replans:
                    budget_svc.reschedule(ctx, e.id, replans[e.id])
                    continue
                for target in [e] + (later if following is not None and following.value else []):
                    if (num.value, cur.value, (note.value or "").strip()) != (
                        target.amount,
                        target.currency,
                        target.comment,
                    ):
                        budget_svc.update_entry(ctx, target.id, num.value, cur.value, note.value or "")
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

            if rep.complete:
                reconciliation_card(rep)
            ledger_card(rep, tc("heading", "Income"), rep.total_income, BUDGET_INCOME, lambda: income_rows(rep))
            ledger_card(
                rep,
                tc("heading", "Expenditure"),
                rep.total_expenditure,
                BUDGET_EXPENDITURE,
                lambda: expenditure_rows(rep),
            )
            if rep.complete or any(abs(line.actual) > EPS for line in rep.revaluation_lines):
                revaluations_card(rep)
            if rep.complete:
                accounts_card(rep)

        # ------------------------------------------------------------ reconciliation (F42)
        def reconciliation_card(rep: reports.MonthReport) -> None:
            """What the records say against what the balances say; the gap is what nobody recorded."""
            with ui.element("div").classes("surface"):
                ui.label(t("Reconciliation")).classes("section-title")
                if rep.unexplained is None or rep.balance_change is None:
                    ui.label(t("Appears once balances are recorded for this and the previous month.")).classes(
                        "caption"
                    )
                    return
                recorded = rep.total_income.actual - rep.total_expenditure.actual
                with ui.element("div").classes("metrics pt-2").style("--n: 4"):
                    metric(t("Recorded Net"), recorded, rep.currency, t("Income minus expenditure"))
                    revaluations = rep.total_revaluation.actual
                    metric(t("Revaluations"), revaluations, rep.currency, t("Gains and losses in value"))
                    metric(t("Change in Balances"), rep.balance_change, rep.currency, t("All accounts, month end"))
                    with ui.element("div").classes("metric"):
                        ui.label(t("Unexplained")).classes("metric-label")
                        ui.html(money_html(rep.unexplained, rep.currency)).classes(
                            "metric-value" + (" text-neg" if rep.unexplained < -EPS else "")
                        )
                        ui.label(
                            t("Money that left without a record")
                            if rep.unexplained < -EPS
                            else t("Money that arrived without a record")
                            if rep.unexplained > EPS
                            else t("Records and balances agree")
                        ).classes("metric-sub")

        # ------------------------------------------------------------ revaluations (F45)
        def revaluations_card(rep: reports.MonthReport) -> None:
            """Changes in value that are neither income nor spending; not part of net income (decision 3)."""
            with ui.element("div").classes("surface"):
                with ui.row().classes("w-full items-end gap-3 no-wrap pb-1"):
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(t("Revaluations")).classes("section-title")
                        ui.label(t("Gains and losses in value, outside net income")).classes("caption")
                    ui.space()
                    ui.label((fmt_signed(rep.total_revaluation.actual) or fmt(0)) + f" {rep.currency}").classes(
                        "text-[20px] font-semibold num whitespace-nowrap"
                    )
                with ui.element("div").classes("ledger"):
                    for line in rep.revaluation_lines:
                        with ui.element("div").classes("ledger-row reval-row"):
                            with ui.column().classes("gap-0 min-w-0"):
                                ui.label(line.label).classes("truncate")
                                if line.key == REVAL_FX and abs(rep.fx_translation) > EPS:
                                    ui.label(
                                        t(
                                            "including {amount} from exchange-rate changes",
                                            amount=fmt_signed(rep.fx_translation),
                                        )
                                    ).classes("caption")
                            ui.label(fmt_signed(line.actual) or "–").classes(
                                "r " + ("text-neg" if line.actual < -EPS else "")
                            )

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
                        ui.space()
                        ui.switch(
                            t("Show all subcategories"),
                            value=state["show_empty"],
                            on_change=lambda e: (state.update(show_empty=e.value), content.refresh()),
                        ).props("dense").classes("text-[13px] text-2")

        def line_row(rep: reports.MonthReport, line: reports.Line, type_: str, depth: int = 0, children=None) -> None:
            expanded = line.key in state["expanded"]
            empty = _is_zero(line.actual) and _is_zero(line.budget)
            editable = not rep.complete and line.editable and line.key != "TOTAL"
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
                _amount(line.budget)
                diff = None if empty else line.diff
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
        def book_revaluation(rep: reports.MonthReport, row) -> None:
            """F46: book an account's unexplained difference as a revaluation, prefilled."""
            prefill = TransactionInput(
                date=months.last_day(rep.month),
                type=TX_REVALUATION,
                currency=row.account.currency,
                category=REVAL_INVEST,
                reval_account=row.account.id,
                reval_change=row.unexplained,
            )

            def booked() -> None:
                content.refresh()

            open_transaction_sheet(ctx, booked, prefill=prefill)

        def accounts_card(rep: reports.MonthReport) -> None:
            """Opening, closing and change per account in its own currency (F41), and the value shown in totals."""
            cur = rep.currency
            with ui.element("div").classes("w-full"):
                ui.label(t("Balances · End of {month}", month=months.label(rep.month))).classes("group-header")
                with ui.element("div").classes("group accounts"):
                    if not rep.accounts:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No balances recorded yet.")).classes("text-2")
                        return
                    with ui.element("div").classes("group-row acct-row head"):
                        ui.label(t("Account")).classes("c-name")
                        ui.label(t("Opening")).classes("c-open")
                        ui.label(t("Closing")).classes("c-close")
                        ui.label(t("Change")).classes("c-change")
                        ui.label(t("Unexplained")).classes("c-unexpl")
                        ui.label(t("In {currency}", currency=cur)).classes("c-view")
                    for r in rep.accounts:
                        acc_cur = r.account.currency
                        with ui.element("div").classes("group-row acct-row"):
                            with ui.column().classes("c-name gap-0"):
                                ui.label(r.account.name).classes("truncate w-full")
                                if r.needs_entry:
                                    ui.label(t("Not recorded, last balance carried over")).classes("caption")
                            ui.label(fmt(r.opening, acc_cur) if r.opening is not None else "–").classes(
                                "c-open num text-2"
                            )
                            ui.label(fmt(r.amount, acc_cur)).classes("c-close num font-medium")
                            change = r.change
                            ui.label(
                                (fmt_signed(change) or fmt(0)) + f" {acc_cur}" if change is not None else "–"
                            ).classes("c-change num " + ("text-neg" if (change or 0) < -EPS else "text-2"))
                            with ui.row().classes("c-unexpl gap-0 items-center no-wrap justify-end"):
                                gap = r.unexplained
                                ui.label("–" if gap is None else (fmt_signed(gap) or fmt(0)) + f" {acc_cur}").classes(
                                    "num " + ("text-neg" if (gap or 0) < -EPS else "text-2")
                                )
                                if gap is not None and abs(gap) > EPS:
                                    ui.button(
                                        icon="o_post_add",
                                        on_click=lambda r=r: book_revaluation(rep, r),
                                    ).props("flat dense round size=sm").classes("text-primary").tooltip(
                                        t("Book as revaluation…")
                                    )
                            ui.label(fmt(r.amount_view, cur)).classes("c-view num text-2")
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
                    with ui.element("div").classes("group-row acct-row total"):
                        ui.label(t("Total")).classes("c-name font-semibold")
                        ui.label(fmt(rep.wealth_start, cur) if rep.wealth_start is not None else "–").classes(
                            "c-open num text-2"
                        )
                        ui.label(fmt(sum(r.amount_view for r in rep.accounts), cur)).classes(
                            "c-close num font-semibold"
                        )
                        ui.label(
                            (fmt_signed(rep.balance_change) or fmt(0)) + f" {cur}"
                            if rep.balance_change is not None
                            else "–"
                        ).classes("c-change num")
                        ui.label(
                            (fmt_signed(rep.unexplained) or fmt(0)) + f" {cur}" if rep.unexplained is not None else "–"
                        ).classes("c-unexpl num " + ("text-neg" if (rep.unexplained or 0) < -EPS else ""))

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
