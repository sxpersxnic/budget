"""Tags (F10): every tag with its number of transactions, what was spent and received, and its budget; add, edit
(name and colour), merge, delete and set a budget. A tag's chip opens Transactions filtered by it. Each change is
one undoable action, however many transactions it touches."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.domain.models import TAG_DEFAULT_COLOUR, Tag
from budget_app.domain.tags import key
from budget_app.i18n import N_, t, tn
from budget_app.services import tags as tag_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.tags import BudgetStatus, TagTotal
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    frame,
    named,
    on_data_changed,
    parse_date,
    sheet,
    sheet_buttons,
    undoable,
)
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import currency_select
from budget_app.ui.widgets.tags import ColourPicker, chip

ROW_ACTION = "flat dense round size=sm"


def caption(total: TagTotal | None, currency: str) -> str:
    if total is None or not total.count:
        return t("No transactions")
    parts = [tn("{n} transaction", "{n} transactions", total.count)]
    if abs(total.spent) >= 0.005:
        parts.append(t("spent {amount}", amount=fmt(total.spent, currency)))
    if abs(total.received) >= 0.005:
        parts.append(t("received {amount}", amount=fmt(total.received, currency)))
    return " · ".join(parts)


def period_label(start: date | None, end: date | None) -> str:
    if start and end:
        return t("{start} – {end}", start=i18n.date_label(start), end=i18n.date_label(end))
    if start:
        return t("from {date}", date=i18n.date_label(start))
    if end:
        return t("until {date}", date=i18n.date_label(end))
    return t("All time")


def budget_line(status: BudgetStatus) -> str:
    """ "540.00 of 2'000.00 CHF · 1'460.00 CHF left · 1 Jul 2026 – 31 Aug 2026"."""
    cur = status.currency
    used = t("{actual} of {budget}", actual=fmt(status.actual), budget=fmt(status.budget, cur))
    rest = (
        t("{amount} over budget", amount=fmt(-status.remaining, cur))
        if status.over
        else t("{amount} left", amount=fmt(status.remaining, cur))
    )
    return " · ".join([used, rest, period_label(status.start, status.end)])


def budget_meter(status: BudgetStatus) -> None:
    with ui.element("div").classes("meter" + (" over" if status.over else "")):
        ui.element("div").style(f"width: {min(status.ratio, 1) * 100:.1f}%")


# ------------------------------------------------------------------ sheets
def open_tag_sheet(ctx: Context, on_saved: Callable[[], None], editing: Tag | None = None) -> None:
    """A new tag, or a tag's name and colour."""
    with sheet(t("Edit Tag") if editing else t("New Tag"), width="26rem") as dlg:
        name = (
            ui.input(t("Name"), value=editing.name if editing else "", placeholder=t("e.g. Holiday Italy"))
            .props("dense outlined autofocus")
            .classes("w-full")
        )
        ui.label(t("Colour")).classes("caption px-1")
        picker = ColourPicker(editing.colour if editing else TAG_DEFAULT_COLOUR)
        if editing:
            ui.label(t("Transactions keep the tag; only its name and colour change.")).classes("caption px-1")

        def save() -> None:
            try:
                if editing is None:
                    saved = tag_svc.add(ctx, name.value or "", picker.value)
                else:
                    with ctx.store.history.action(N_("Edit a tag")):  # name and colour: one step to undo
                        tag_svc.rename(ctx, editing.id, name.value or "")
                        saved = tag_svc.recolour(ctx, editing.id, picker.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Saved “{name}”", name=saved.name))
            on_saved()

        name.on("keydown.enter", save)
        sheet_buttons(dlg, t("Save") if editing else t("Add Tag"), save)


def open_budget_sheet(ctx: Context, tag: Tag, on_saved: Callable[[], None]) -> None:
    """A tag's budget: an amount for the tagged spending of a period, whose start and end are optional."""
    with sheet(t("Budget for “{name}”", name=tag.name), width="28rem") as dlg:
        with ui.row().classes("w-full gap-2 items-center no-wrap"):
            amount = (
                ui.number(t("Amount"), value=tag.budget or None, min=0, format="%.2f")
                .props("outlined inputmode=decimal autofocus")
                .classes("flex-1 min-w-0")
            )
            cur = currency_select(ctx, tag.budget_currency or ctx.store.settings.display_currency)
        with ui.row().classes("w-full gap-2 no-wrap"):
            start = date_input(t("From"), tag.budget_start).props("clearable").classes("flex-1 min-w-0")
            end = date_input(t("Until"), tag.budget_end).props("clearable").classes("flex-1 min-w-0")
        ui.label(
            t(
                "The tagged spending between these dates counts against the budget; leave a date empty for an open "
                "end. The part paid for others and income don’t count."
            )
        ).classes("caption px-1")

        def save() -> None:
            try:
                tag_svc.set_budget(
                    ctx, tag.id, amount.value, cur.value or "", parse_date(start.value), parse_date(end.value)
                )
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Budget saved") if amount.value else t("Budget removed"))
            on_saved()

        def remove() -> None:
            tag_svc.set_budget(ctx, tag.id, None)
            dlg.close()
            undoable(t("Budget removed"))
            on_saved()

        with ui.row().classes("w-full items-center gap-2 pt-1"):
            if tag.has_budget:
                ui.button(t("Remove Budget"), on_click=remove).props("flat").classes("text-negative")
            ui.space()
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            ui.button(t("Save"), on_click=save).props("unelevated")


def open_merge_sheet(ctx: Context, tags: list[Tag], totals: dict[str, TagTotal], on_saved: Callable[[], None]) -> None:
    most_used = max(tags, key=lambda g: (totals[g.id].count if g.id in totals else 0, -len(g.name)))
    with sheet(t("Merge Tags"), width="26rem") as dlg:
        ui.label(t("Keep the tag")).classes("text-[13px] font-semibold text-2")
        keep = ui.radio({g.id: g.name for g in tags}, value=most_used.id).props("dense")
        ui.label(t("The transactions, templates and recurring transactions of the others get it instead.")).classes(
            "caption px-1"
        )

        def merge() -> None:
            try:
                kept = tag_svc.merge(ctx, [g.id for g in tags], keep.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            n = len(tags)
            undoable(tn("Merged {n} tag into “{name}”", "Merged {n} tags into “{name}”", n, name=kept.name))
            on_saved()

        sheet_buttons(dlg, t("Merge"), merge)


# ------------------------------------------------------------------ the page
def page() -> None:
    with frame(t("Tags"), "/tags", narrow=True) as (ctx, actions):
        with actions:
            search = (
                ui.input(placeholder=t("Search"))
                .props("dense outlined clearable debounce=150")
                .classes("search w-64 max-sm:w-full")
            )
            with search.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
            new = ui.button(t("New Tag"), icon="add", on_click=lambda: open_tag_sheet(ctx, refresh)).props("unelevated")
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
        state: dict = {"totals": {}}

        def update_bar() -> None:
            n = len(selected)
            bar.set_visibility(n > 0)
            selected_label.set_text(tn("{n} selected", "{n} selected", n))
            merge_btn.set_enabled(n >= 2)

        def toggle(tag_id: str, value: bool) -> None:
            if value:
                selected.add(tag_id)
            else:
                selected.discard(tag_id)
            update_bar()

        @ui.refreshable
        def tag_list() -> None:
            currency = ctx.store.settings.display_currency
            totals = state["totals"] = tag_svc.totals(ctx, currency)
            tags = tag_svc.all_tags(ctx)
            selected.intersection_update(g.id for g in tags)
            query = key(search.value or "")
            shown = [g for g in tags if not query or query in key(g.name)]
            new.set_visibility(bool(tags))  # while there are none, the empty state offers it
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    header = (
                        tn("{shown} of {n} tag", "{shown} of {n} tags", len(tags), shown=len(shown))
                        if query
                        else tn("{n} tag", "{n} tags", len(tags))
                    )
                    ui.label(header).classes("group-header")
                    ui.space()
                    if totals:
                        ui.label(t("All time, {currency}", currency=currency)).classes("caption pb-1.5 pr-1")
                with ui.element("div").classes("group"):
                    if tags and not shown:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No tag matches.")).classes("text-2")
                    elif not tags:
                        empty_state(
                            t("No tags yet."),
                            t(
                                "Labels across categories, such as a holiday or a renovation. A transaction can have "
                                "several; give a tag a budget to follow its spending."
                            ),
                            t("New Tag"),
                            lambda: open_tag_sheet(ctx, refresh),
                        )
                    for tag in shown:
                        _row(tag, totals.get(tag.id), currency)
            update_bar()

        def _row(tag: Tag, total: TagTotal | None, currency: str) -> None:
            status = tag_svc.budget_status(ctx, tag, currency)
            with ui.element("div").classes("group-row items-start py-2.5"):
                ui.checkbox(value=tag.id in selected, on_change=lambda e, g=tag: toggle(g.id, e.value)).props("dense")
                with ui.column().classes("gap-1 flex-1 min-w-0"):
                    chip(tag, on_click=lambda g=tag: ui.navigate.to(f"/transactions?tag={g.id}")).tooltip(
                        t("Show its transactions")
                    )
                    ui.label(caption(total, currency)).classes("caption")
                    if status is not None:
                        budget_meter(status)
                        ui.label(budget_line(status)).classes("caption" + (" text-neg" if status.over else ""))
                with ui.row().classes("row-actions gap-0 no-wrap"):
                    named(
                        ui.button(icon="o_edit", on_click=lambda g=tag: open_tag_sheet(ctx, refresh, g))
                        .props(ROW_ACTION)
                        .classes("text-primary"),
                        t("Edit"),
                    )
                    named(
                        ui.button(icon="o_savings", on_click=lambda g=tag: open_budget_sheet(ctx, g, refresh))
                        .props(ROW_ACTION)
                        .classes("text-primary"),
                        t("Budget"),
                    )
                    named(
                        ui.button(icon="o_delete", on_click=lambda g=tag, n=total: delete(g, n))
                        .props(ROW_ACTION)
                        .classes("text-negative"),
                        t("Delete"),
                    )

        def refresh() -> None:
            tag_list.refresh()

        def clear_selection() -> None:
            selected.clear()
            refresh()

        def merge_selected() -> None:
            tags = [g for g in tag_svc.all_tags(ctx) if g.id in selected]  # Merge… needs two
            open_merge_sheet(ctx, tags, state["totals"], clear_selection)

        def delete(tag: Tag, total: TagTotal | None) -> None:
            n = total.count if total else 0

            def do() -> None:
                try:
                    tag_svc.delete(ctx, [tag.id])
                except ValidationError as exc:
                    error(str(exc))
                    return
                undoable(t("Deleted {name}", name=tag.name))
                refresh()

            detail = (
                tn(
                    "{n} transaction loses this tag; nothing else changes.",
                    "{n} transactions lose this tag; nothing else changes.",
                    n,
                )
                if n
                else ""
            )
            confirm(t("Delete “{name}”?", name=tag.name), do, detail=detail)

        tag_list()
        search.on_value_change(refresh)
        on_data_changed(refresh)
