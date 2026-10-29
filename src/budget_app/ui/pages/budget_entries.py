"""Budget Plan: the plan as series (one row per budget, the default), and the months grid for editing one month."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import fold, get_coicop
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME, BudgetEntry
from budget_app.i18n import N_, t, tn
from budget_app.services import budget as budget_svc
from budget_app.services.budget import BudgetInput, Series
from budget_app.services.context import Context, ValidationError
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
from budget_app.ui.pages.add_budget import (
    FOREVER,
    open_budget_sheet,
    plan_summary,
    recurrence_label,
    recurrence_options,
    until_options,
)
from budget_app.ui.pages.budget_proposal import open_proposal_sheet
from budget_app.ui.widgets.grid import confirm, export_items, make_grid, money_col, month_col, num_col
from budget_app.ui.widgets.selects import currency_select, expenditure_category_select, income_category_select

TYPES = {BUDGET_EXPENDITURE: N_("Expenditure"), BUDGET_INCOME: N_("Income")}
SERIES, MONTHS = "series", "months"
ROW_ACTION = "flat dense round size=sm"


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


def _series_columns() -> list[dict]:
    """The Series view's export: one row per budget, as the list shows it."""
    return [
        {"field": "category", "headerName": t("Category")},
        {"field": "type", "headerName": t("Type")},
        {"field": "amount", "headerName": t("Amount")},
        {"field": "recurrence", "headerName": t("Repeats")},
        {"field": "first", "headerName": t("From")},
        {"field": "until", "headerName": t("Until")},
        {"field": "comment", "headerName": t("Note")},
    ]


def category_label(ctx: Context, b: BudgetEntry) -> str:
    if b.type == BUDGET_INCOME:
        cat = ctx.store.income_categories.get(b.category)
        return cat.name if cat else b.category
    return get_coicop().label(b.category)


def rhythm(s: Series) -> str:
    """'Every month', 'Every quarter' … or 'Once' for a budget of one month."""
    return t("Once") if s.once else recurrence_label(s.current.recur_months)


def period(s: Series) -> str:
    """'from Oct 2025, forever', 'Oct 2025 – Mar 2026', or the one month."""
    if s.forever:
        return t("from {month}, forever", month=months.label(s.first))
    if s.once:
        return months.label(s.first)
    return f"{months.label(s.first)} – {months.label(s.last)}"


def signed(amount: float, currency: str, type_: str) -> str:
    """Income signed, the way a statement reads (as in the grids)."""
    return ("+" if type_ == BUDGET_INCOME else "") + fmt(amount, currency)


def edit_dialog(ctx: Context, entry: BudgetEntry, on_saved) -> None:
    later = budget_svc.later_in_series(ctx, entry)
    sched = budget_svc.schedule_of(ctx, entry)
    until_value = FOREVER if sched.forever else sched.end_month
    until_opts = until_options(entry.month, ctx.horizon(), extra=sched.end_month)
    with sheet(t("Budget for {month}", month=months.label(entry.month)), width="30rem") as dlg:
        if entry.type == BUDGET_EXPENDITURE:
            cat = expenditure_category_select(entry.category, ctx=ctx)
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
                with ctx.store.history.action(N_("Change a budget entry")):
                    _apply()
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Budget updated"))
            on_saved()

        def _apply() -> None:
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

        sheet_buttons(dlg, t("Save"), save)


def series_dialog(ctx: Context, s: Series, on_saved, on_delete) -> None:
    """Change a budget from a month on: amount, currency, category, until (or forever), repeat and note. The months
    before keep their values (budget.replan_series). *Delete…* hands over to `on_delete` (the row's action, which
    phones don't show)."""
    horizon = ctx.horizon()
    start_range, _ = data_month_range(ctx)
    cur_entry = s.current
    label = category_label(ctx, cur_entry)

    def until_choices(start_month: str) -> dict[str, str]:
        """'Forever' and the months from `start_month` to the horizon, with the series' own last month."""
        opts = until_options(start_month, horizon, extra="" if s.forever else s.last)
        return {k: v for k, v in opts.items() if k == FOREVER or k >= start_month}

    with sheet(label, width="34rem") as dlg:
        ui.label(f"{rhythm(s)} · {period(s)}").classes("footnote -mt-2")
        if s.type == BUDGET_EXPENDITURE:
            cat = expenditure_category_select(s.category, ctx=ctx)
        else:
            cat = income_category_select(ctx, s.category)
        with ui.row().classes("w-full gap-2 no-wrap items-center"):
            amount = (
                ui.number(t("Amount"), value=cur_entry.amount, min=0, format="%.2f")
                .props("dense outlined inputmode=decimal")
                .classes("flex-1 min-w-0")
            )
            cur = currency_select(ctx, cur_entry.currency)
        with ui.row().classes("w-full gap-2 no-wrap"):
            start = (
                ui.select(
                    month_options(min(s.first, start_range), max(horizon, s.first)),
                    value=s.first if s.ended(months.current_month()) else cur_entry.month,
                    label=t("From"),
                )
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
            until = (
                ui.select(until_choices(start.value), value=FOREVER if s.forever else s.last, label=t("Until"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
        recur = (
            ui.select(recurrence_options(), value=cur_entry.recur_months, label=t("Repeat"))
            .props("dense outlined options-dense")
            .classes("w-full")
        )
        kept = ui.label().classes("caption px-1 -mt-1")
        comment = (
            ui.textarea(t("Note"), value=cur_entry.comment, placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )
        status = ui.label().classes("caption px-1")

        def read() -> BudgetInput:
            return BudgetInput(
                start_month=start.value or "",
                end_month="" if until.value == FOREVER else until.value or "",
                forever=until.value == FOREVER,
                recur_months=int(recur.value or 1),
                type=s.type,
                currency=cur.value or "",
                amount=amount.value,
                category=cat.value or "",
                comment=comment.value or "",
            )

        def changed() -> None:
            if start.value:
                opts = until_choices(start.value)
                if until.value not in opts:
                    until.value = None
                until.set_options(opts, value=until.value)
            earlier = bool(start.value) and start.value > s.first
            kept.set_text(
                t("The months before {month} keep their values.", month=months.label(start.value)) if earlier else ""
            )
            try:
                status.set_text(plan_summary(budget_svc.validate(ctx, read()), until.value == FOREVER))
            except ValidationError as exc:
                status.set_text(str(exc))

        for el in (start, until, recur, cur, amount, cat):
            el.on_value_change(changed)
        changed()

        def save() -> None:
            try:
                budget_svc.replan_series(ctx, s.key, read())
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Budget updated"))
            on_saved()

        save_btn = sheet_buttons(dlg, t("Save"), save)
        with save_btn.parent_slot.parent:  # type: ignore[union-attr]
            remove = (
                ui.button(t("Delete…"), on_click=lambda: (dlg.close(), on_delete(s.key)))
                .props("flat")
                .classes("text-negative mr-auto")
            )
        remove.move(target_index=0)


def page() -> None:
    with frame(t("Budget Plan"), "/budget-entries") as (ctx, actions):
        first, last = data_month_range(ctx)
        shown: list[dict] = []  # the months the filters let through, for the export
        shown_series: list[dict] = []  # the budgets the search lets through, for the export
        with actions:
            text = (
                ui.input(placeholder=t("Search")).props("dense outlined clearable").classes("search w-64 max-sm:w-full")
            )
            with text.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
            with (
                named(ui.button(icon="more_horiz").props("flat round").classes("text-primary"), t("More")),
                ui.menu().props("auto-close"),
            ):
                ui.menu_item(t("Propose from History…"), on_click=lambda: open_proposal_sheet(ctx, refresh))
                ui.separator()
                export_items(
                    "budget-plan",
                    t("Budget Plan"),
                    lambda: _series_columns() if view.value == SERIES else _columns(),
                    lambda: shown_series if view.value == SERIES else shown,
                )
            ui.button(t("New Budget Entry"), icon="add", on_click=lambda: open_budget_sheet(ctx, refresh)).props(
                "unelevated"
            )
        with ui.row().classes("w-full items-center gap-2") as toolbar:
            view = segmented({SERIES: t("Series"), MONTHS: t("Months")}, SERIES)
            ui.space()
            filter_btn = ui.button(t("Filters"), icon="o_filter_list").props("flat").classes("text-primary")
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

        # ------------------------------------------------------------ nothing planned yet
        with ui.element("div").classes("group") as empty, ui.column().classes("gap-3 p-5 items-start"):
            ui.label(t("Nothing planned yet")).classes("section-title")
            ui.label(
                t(
                    "A budget entry is an amount you plan to earn or spend in a category: once, every month or "
                    "every few months, until a month or for as long as the plan runs. The Budget page compares it "
                    "with what happened."
                )
            ).classes("text-2 max-w-prose")
            with ui.row().classes("gap-2 items-center -ml-3"):  # the header's button stays the one primary action
                ui.button(t("New Budget Entry"), icon="add", on_click=lambda: open_budget_sheet(ctx, refresh)).props(
                    "flat"
                ).classes("text-primary")
                ui.button(
                    t("Propose from History…"),
                    icon="o_auto_awesome",
                    on_click=lambda: open_proposal_sheet(ctx, refresh),
                ).props("flat").classes("text-primary")

        # ------------------------------------------------------------ Series view
        series_box = ui.column().classes("w-full gap-5")

        def matches(s: Series, q: str) -> bool:
            return not q or q in fold(f"{category_label(ctx, s.current)} {' '.join(b.comment for b in s.entries)}")

        def series_list() -> None:
            """Render the series anew (synchronously: a refreshable would render in a task after the change)."""
            series_box.clear()
            with series_box:
                render_series()

        def render_series() -> None:
            q = fold((text.value or "").strip())
            cm = months.current_month()
            view_cur = ctx.store.settings.display_currency
            plan = [s for s in budget_svc.series(ctx) if matches(s, q)]
            shown_series[:] = [
                {
                    "category": category_label(ctx, s.current),
                    "type": t(TYPES[s.type]),
                    "amount": s.current.amount,
                    "currency": s.current.currency,
                    "recurrence": rhythm(s),
                    "first": s.first,
                    "until": t("Forever") if s.forever else s.last,
                    "comment": s.current.comment,
                }
                for s in plan
            ]
            if not plan:
                with ui.element("div").classes("group"), ui.element("div").classes("group-row"):
                    ui.label(t("Nothing matches these filters.")).classes("text-2")
                return
            totals = budget_svc.planned(ctx, plan, cm, view_cur)
            for type_ in (BUDGET_INCOME, BUDGET_EXPENDITURE):
                group = [s for s in plan if s.type == type_]
                if not group:
                    continue
                if type_ == BUDGET_INCOME:  # expenditure stays in COICOP order
                    group.sort(key=lambda s: fold(category_label(ctx, s.current)))
                group.sort(key=lambda s: s.ended(cm))  # ended budgets last
                with ui.column().classes("w-full gap-0"):
                    with ui.row().classes("w-full items-end justify-between gap-x-2 gap-y-0"):
                        ui.label(t(TYPES[type_])).classes("group-header")
                        ui.label(
                            t(
                                "{amount} planned for {month}",
                                amount=signed(totals[type_], view_cur, type_),
                                month=months.label(cm),
                            )
                        ).classes("caption tabular-nums whitespace-nowrap px-1 pb-1.5")
                    with ui.element("div").classes("group"):
                        for s in group:
                            series_row(s, cm)

        def series_row(s: Series, cm: str) -> None:
            ended = s.ended(cm)
            with ui.element("div").classes("group-row series-row"):
                with (
                    ui.row()
                    .classes("flex-1 min-w-0 items-center gap-3 no-wrap cursor-pointer")
                    .on("click", lambda s=s: open_series(s.key))
                ):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(category_label(ctx, s.current)).classes(
                            "truncate max-sm:whitespace-normal" + (" text-2" if ended else "")
                        )
                        caption = f"{rhythm(s)} · {period(s)}"
                        if s.differing:
                            caption += " · " + tn("{n} month differs", "{n} months differ", s.differing)
                        ui.label(caption).classes("caption")
                        if s.current.comment:
                            ui.label(s.current.comment).classes("caption truncate")
                    tone = " text-2" if ended else " text-pos" if s.type == BUDGET_INCOME else ""
                    ui.label(signed(s.current.amount, s.current.currency, s.type)).classes(
                        "tabular-nums whitespace-nowrap shrink-0" + tone
                    )
                with ui.row().classes("row-actions gap-0 no-wrap max-sm:hidden"):  # phones: tap the row
                    named(
                        ui.button(icon="o_edit", on_click=lambda s=s: open_series(s.key))
                        .props(ROW_ACTION)
                        .classes("text-primary"),
                        t("Edit"),
                    )
                    named(
                        ui.button(icon="o_delete", on_click=lambda s=s: delete_series(s.key))
                        .props(ROW_ACTION)
                        .classes("text-negative"),
                        t("Delete"),
                    )

        def open_series(key: str) -> None:
            found = budget_svc.find_series(ctx, key)
            if found:
                series_dialog(ctx, found, refresh, delete_series)

        def delete_series(key: str) -> None:
            found = budget_svc.find_series(ctx, key)
            if found is None:
                return
            name = category_label(ctx, found.current)

            def do() -> None:
                budget_svc.delete_series(ctx, key)
                undoable(t("Deleted the budget for {category}", category=name))
                refresh()

            confirm(
                t("Delete the budget for {category}?", category=name),
                do,
                detail=plan_summary([b.month for b in found.entries], found.forever),
            )

        # ------------------------------------------------------------ Months view
        with ui.column().classes("w-full gap-2") as months_box:
            with ui.element("div").classes("selection-bar") as bar:
                selected_label = ui.label().classes("font-medium")
                ui.space()
                ui.button(t("Edit"), icon="o_edit", on_click=lambda: edit_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                    "text-primary"
                )
                ui.button(t("Delete"), icon="o_delete", on_click=lambda: delete_selected()).props(  # noqa: PLW0108 - defined below
                    "flat dense"
                ).classes("text-negative")
            bar.set_visibility(False)
            grid = make_grid(_columns())
            count = ui.label().classes("caption px-1")

        def apply_filters() -> None:
            q = fold((text.value or "").strip())
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
                if q and q not in fold(f"{label} {b.comment}"):
                    continue
                rec = recurrence_label(b.recur_months)
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

        def show() -> None:
            """The chosen view, or what a budget entry is while nothing is planned."""
            has = bool(ctx.store.budget.all())
            empty.set_visibility(not has)
            toolbar.set_visibility(has)
            text.set_visibility(has)
            series_box.set_visibility(has and view.value == SERIES)
            months_box.set_visibility(has and view.value == MONTHS)
            filter_btn.set_visibility(view.value == MONTHS)
            if view.value != MONTHS:
                filters.set_visibility(False)

        def refresh() -> None:
            series_list()
            apply_filters()
            show()

        def searched() -> None:
            series_list()
            apply_filters()

        view.on_value_change(show)
        text.on_value_change(searched)
        for el in (m_from, m_to, a_min, a_max, typ):
            el.on_value_change(apply_filters)
        refresh()
        on_data_changed(refresh)

        def open_editor(entry_id: str) -> None:
            entry = ctx.store.budget.get(entry_id)
            if entry:
                edit_dialog(ctx, entry, refresh)

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
                undoable(tn("Deleted {n} budget entry", "Deleted {n} budget entries", n))
                refresh()

            confirm(tn("Delete {n} budget entry?", "Delete {n} budget entries?", len(rows)), do)

        grid.on("selectionChanged", selection_changed)
        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
