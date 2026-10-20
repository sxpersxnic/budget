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
from budget_app.ui.layout import error, fmt, frame, on_data_changed, sheet, sheet_buttons, undoable
from budget_app.ui.widgets.grid import confirm

ROW_ACTION = "flat dense round size=sm"


def caption(usage: Usage | None, currency: str) -> str:
    if usage is None or not usage.count:
        return t("No transactions")
    parts = [tn("{n} transaction", "{n} transactions", usage.count)]
    if abs(usage.spent) >= 0.005:
        parts.append(t("spent {amount}", amount=fmt(usage.spent, currency)))
    if abs(usage.received) >= 0.005:
        parts.append(t("received {amount}", amount=fmt(usage.received, currency)))
    return " · ".join(parts)


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
        with actions:
            search = (
                ui.input(placeholder=t("Search"))
                .props("dense outlined clearable debounce=150")
                .classes("search w-64 max-sm:w-full")
            )
            with search.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
        ui.label(
            t(
                "Who you pay and who pays you. Imports fill in the payee from the bank text; after a rename or "
                "a merge, the next import of the same text uses the payee you kept."
            )
        ).classes("caption -mt-2")
        selected: set[str] = set()
        with ui.element("div").classes("selection-bar") as bar:
            selected_label = ui.label().classes("font-medium")
            ui.space()
            merge_btn = ui.button(t("Merge…"), icon="o_call_merge", on_click=lambda: merge_selected()).props(  # noqa: PLW0108 - defined below
                "flat dense"
            )
            merge_btn.classes("text-primary")
            ui.button(t("Clear Selection"), on_click=lambda: clear_selection()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-primary"
            )
        bar.set_visibility(False)
        state: dict = {"usage": {}}

        def update_bar() -> None:
            n = len(selected)
            bar.set_visibility(n > 0)
            selected_label.set_text(tn("{n} selected", "{n} selected", n))
            merge_btn.set_enabled(n >= 2)

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
            query = key(search.value or "")
            shown = [p for p in payees if not query or matches(p, query)]
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    header = (
                        tn("{shown} of {n} payee", "{shown} of {n} payees", len(payees), shown=len(shown))
                        if query
                        else tn("{n} payee", "{n} payees", len(payees))
                    )
                    ui.label(header).classes("group-header")
                    ui.space()
                    if usage:
                        ui.label(t("All time, {currency}", currency=currency)).classes("caption pb-1.5 pr-1")
                with ui.element("div").classes("group"):
                    if not shown:
                        with ui.element("div").classes("group-row"):
                            ui.label(
                                t("No payee matches.")
                                if payees
                                else t("No payees yet. Add one on a transaction, or import a bank file.")
                            ).classes("text-2")
                    for payee in shown:
                        _row(payee, usage.get(key(payee.name)), currency)
            update_bar()

        def _row(payee: Payee, use: Usage | None, currency: str) -> None:
            with ui.element("div").classes("group-row"):
                ui.checkbox(value=payee.id in selected, on_change=lambda e, p=payee: toggle(p.id, e.value)).props(
                    "dense"
                )
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(payee.name).classes("truncate")
                    ui.label(caption(use, currency)).classes("caption")
                with ui.row().classes("row-actions gap-0 no-wrap"):
                    ui.button(icon="o_edit", on_click=lambda p=payee: open_rename_sheet(ctx, p, refresh)).props(
                        ROW_ACTION
                    ).classes("text-primary").tooltip(t("Rename"))
                    if not payee_svc.in_use(ctx, payee):
                        ui.button(icon="o_delete", on_click=lambda p=payee: delete(p)).props(ROW_ACTION).classes(
                            "text-negative"
                        ).tooltip(t("Delete"))

        def refresh() -> None:
            payee_list.refresh()

        def clear_selection() -> None:
            selected.clear()
            refresh()

        def merge_selected() -> None:
            payees = [p for p in payee_svc.all_payees(ctx) if p.id in selected]  # Merge… needs two
            open_merge_sheet(ctx, payees, state["usage"], clear_selection)

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
