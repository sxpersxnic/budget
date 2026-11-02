"""Payees (F09): every payee with its number of transactions and what was spent and received, rename, merge and
delete. Each of them is one undoable action, however many transactions it changes."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.domain.models import Payee
from budget_app.domain.payees import key
from budget_app.i18n import t, tn
from budget_app.services import payees as payee_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.payees import Usage
from budget_app.ui.layout import error, fmt, frame, named, on_data_changed, row_value, sheet, sheet_buttons, undoable
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.sort_cards import logo

ROW_ACTION = "flat dense round size=sm"


def caption(usage: Usage | None) -> str:
    """The small line under a payee's amount: "14 transactions", "· received 120.00" when it also paid you."""
    if usage is None or not usage.count:
        return t("No transactions")
    parts = [tn("{n} transaction", "{n} transactions", usage.count)]
    if abs(usage.spent) >= 0.005 and abs(usage.received) >= 0.005:
        parts.append(t("received {amount}", amount=fmt(usage.received)))
    return " · ".join(parts)


def _value(usage: Usage | None) -> None:
    """What a payee cost, right-aligned so the rows compare (polish before 1.0); what it paid, in green, when it
    only paid. The currency is said once, above the list."""
    if usage is None or not usage.count:
        ui.label(caption(usage)).classes("caption ml-auto")
    elif abs(usage.spent) >= 0.005 or abs(usage.received) < 0.005:
        row_value(usage.spent, sub=caption(usage))
    else:
        row_value(usage.received, sub=caption(usage), tone="positive")


def matches(payee: Payee, query: str) -> bool:
    """Whether the folded `query` occurs in the payee's name or in one of its bank texts."""
    return any(query in text for text in (key(payee.name), *payee.alias_list))


def open_rename_sheet(ctx: Context, payee: Payee, on_saved: Callable[[], None]) -> None:
    with sheet(t("Rename Payee"), width="24rem") as dlg:
        name = ui.input(t("Name"), value=payee.name).props("dense outlined autofocus").classes("w-full")
        ui.label(t("Every transaction with this payee is renamed too. Imports keep finding it.")).classes(
            "caption px-1"
        )

        def save() -> None:
            try:
                renamed = payee_svc.rename(ctx, payee.id, name.value or "")
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Renamed to “{name}”", name=renamed.name))
            on_saved()

        name.on("keydown.enter", save)
        sheet_buttons(dlg, t("Save"), save)


def open_merge_sheet(ctx: Context, payees: list[Payee], usage: dict[str, Usage], on_saved: Callable[[], None]) -> None:
    most_used = max(payees, key=lambda p: (usage[key(p.name)].count if key(p.name) in usage else 0, -len(p.name)))
    with sheet(t("Merge Payees"), width="26rem") as dlg:
        ui.label(t("Keep the name")).classes("text-[13px] font-semibold text-2")
        keep = ui.radio({p.id: p.name for p in payees}, value=most_used.id).props("dense")
        ui.label(
            t("The transactions, templates, recurring transactions and bank texts of the others move to it.")
        ).classes("caption px-1")

        def merge() -> None:
            try:
                kept = payee_svc.merge(ctx, [p.id for p in payees], keep.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            n = len(payees)
            undoable(tn("Merged {n} payee into “{name}”", "Merged {n} payees into “{name}”", n, name=kept.name))
            on_saved()

        sheet_buttons(dlg, t("Merge"), merge)


def page() -> None:
    with frame(t("Payees"), "/payees", narrow=True) as (ctx, actions):
        with actions.classes("max-sm:w-full no-wrap"):  # on a phone too, Select stays beside the search
            search = (
                ui.input(placeholder=t("Search"))
                .props("dense outlined clearable debounce=150")
                .classes("search w-64 max-sm:w-auto max-sm:flex-1 max-sm:min-w-0")
            )
            with search.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
            # the checkboxes are only for Merge…: they show after Select (polish before 1.0)
            select_btn = ui.button(t("Select"), on_click=lambda: set_selecting(not state["selecting"])).props("flat")
            select_btn.classes("text-primary")
        selected: set[str] = set()
        with ui.element("div").classes("selection-bar") as bar:
            selected_label = ui.label().classes("font-medium")
            ui.space()
            merge_btn = ui.button(t("Merge…"), icon="o_call_merge", on_click=lambda: merge_selected()).props(  # noqa: PLW0108 - defined below
                "flat dense"
            )
            merge_btn.classes("text-primary")
            clear_btn = ui.button(t("Clear Selection"), on_click=lambda: clear_selection()).props("flat dense")  # noqa: PLW0108 - defined below
            clear_btn.classes("text-primary")
        bar.set_visibility(False)
        state: dict = {"usage": {}, "selecting": False}

        def update_bar() -> None:
            n = len(selected)
            bar.set_visibility(state["selecting"])
            selected_label.set_text(tn("{n} selected", "{n} selected", n) if n else t("Select the payees to merge."))
            merge_btn.set_enabled(n >= 2)
            clear_btn.set_visibility(n > 0)

        def set_selecting(on: bool) -> None:
            """Select shows the checkboxes and the bar; Done hides them and forgets the selection."""
            state["selecting"] = on
            select_btn.set_text(t("Done") if on else t("Select"))
            selected.clear()
            refresh()

        def toggle(payee_id: str, value: bool) -> None:
            if value:
                selected.add(payee_id)
            else:
                selected.discard(payee_id)
            update_bar()

        @ui.refreshable
        def payee_list() -> None:
            currency = ctx.store.settings.display_currency
            usage = state["usage"] = payee_svc.usage(ctx, currency)
            payees = payee_svc.all_payees(ctx)
            selected.intersection_update(p.id for p in payees)
            select_btn.set_visibility(len(payees) > 1 or state["selecting"])
            query = key(search.value or "")
            shown = [p for p in payees if not query or matches(p, query)]
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    header = (
                        tn("{shown} of {n} payee", "{shown} of {n} payees", len(payees), shown=len(shown))
                        if query
                        else tn("{n} payee", "{n} payees", len(payees))
                    )
                    if payees:  # no "0 payees" above the empty list
                        ui.label(header).classes("group-header")
                    ui.space()
                    if usage:  # the rows' one currency, said once
                        ui.label(t("All time, {currency}", currency=currency)).classes("caption pb-1.5 pr-1")
                with ui.element("div").classes("group"):
                    if payees and not shown:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No payee matches.")).classes("text-2")
                    elif not payees:
                        empty_state(
                            t("No payees yet."),
                            t("Who you pay and who pays you. Add one on a transaction, or import a bank file."),
                        )
                    for payee in shown:
                        _row(payee, usage.get(key(payee.name)))
            update_bar()

        def _row(payee: Payee, use: Usage | None) -> None:
            with ui.element("div").classes("group-row"):
                if state["selecting"]:
                    ui.checkbox(value=payee.id in selected, on_change=lambda e, p=payee: toggle(p.id, e.value)).props(
                        "dense"
                    )
                logo(payee.name, small=True)
                ui.label(payee.name).classes("truncate flex-1 min-w-0")
                _value(use)
                with ui.row().classes("row-actions gap-0 no-wrap"):
                    named(
                        ui.button(icon="o_edit", on_click=lambda p=payee: open_rename_sheet(ctx, p, refresh))
                        .props(ROW_ACTION)
                        .classes("text-primary"),
                        t("Rename"),
                    )
                    if not payee_svc.in_use(ctx, payee):
                        named(
                            ui.button(icon="o_delete", on_click=lambda p=payee: delete(p))
                            .props(ROW_ACTION)
                            .classes("text-negative"),
                            t("Delete"),
                        )

        def refresh() -> None:
            payee_list.refresh()

        def clear_selection() -> None:
            selected.clear()
            refresh()

        def merge_selected() -> None:
            payees = [p for p in payee_svc.all_payees(ctx) if p.id in selected]  # Merge… needs two
            open_merge_sheet(ctx, payees, state["usage"], lambda: set_selecting(False))

        def delete(payee: Payee) -> None:
            def do() -> None:
                try:
                    payee_svc.delete(ctx, [payee.id])
                except ValidationError as exc:
                    error(str(exc))
                    return
                undoable(t("Deleted {name}", name=payee.name))
                refresh()

            confirm(t("Delete “{name}”?", name=payee.name), do)

        payee_list()
        search.on_value_change(refresh)
        on_data_changed(refresh)
