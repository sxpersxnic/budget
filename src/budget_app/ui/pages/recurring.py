"""Recurring transactions (F05): the Due list on top, the series below, and the sheet to edit one."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.domain.fx import FxError
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, TX_TYPES, Recurring
from budget_app.i18n import N_, t, tn
from budget_app.services import recurring as rec_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.recurring import Booking, Occurrence, RecurringInput
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    frame,
    on_data_changed,
    parse_date,
    refresh_due_badge,
    segmented,
    sheet,
    undoable,
)
from budget_app.ui.pages.add_budget import recurrence_label, recurrence_options
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import (
    account_options,
    currency_select,
    expenditure_category_select,
    income_category_options,
    income_category_select,
    set_options,
)

ROW_ACTION = "flat dense round size=sm"


# ------------------------------------------------------------------ the series sheet
def open_recurring_sheet(
    ctx: Context,
    on_saved: Callable[[], None] | None = None,
    editing: Recurring | None = None,
    prefill: RecurringInput | None = None,
    first_booked: str = "",
) -> None:
    """Create (or edit) a series. `first_booked` is the transaction a new series continues."""
    init = (
        rec_svc.to_input(editing)
        if editing
        else prefill or RecurringInput(start=date.today(), currency=ctx.store.settings.display_currency)
    )
    touched = {"value": False}
    title = t("Edit Recurring Transaction") if editing else t("New Recurring Transaction")
    with sheet(title, width="34rem") as dlg, ui.column().classes("w-full gap-4"):
        typ = segmented({k: t(TX_TYPES[k]) for k in rec_svc.RECURRING_TYPES}, init.type, full=True)
        with ui.row().classes("w-full gap-2 items-center no-wrap"):
            amount = (
                ui.number(t("Amount"), value=init.amount, min=0, format="%.2f")
                .props("outlined inputmode=decimal")
                .classes("hero-amount flex-1 min-w-0")
            )
            cur = currency_select(ctx, init.currency)
        exp_cat = expenditure_category_select(init.category if init.type == TX_EXPENDITURE else None)
        inc_cat = income_category_select(ctx, init.category if init.type == TX_INCOME else None)
        with ui.row().classes("w-full gap-2 no-wrap max-sm:flex-wrap"):
            from_acc = _account_select(t("From Account"), init.from_account)
            to_acc = _account_select(t("To Account"), init.to_account)
        with ui.row().classes("w-full gap-2 no-wrap"):
            start = date_input(t("First Date"), init.start.isoformat() if init.start else "").classes("flex-1 min-w-0")
            recur = (
                ui.select(recurrence_options(), value=init.recur_months, label=t("Repeat"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
        end = date_input(t("Until"), init.end.isoformat() if init.end else "").props("clearable").classes("w-full")
        ui.label(t("Leave Until empty to repeat without an end.")).classes("caption -mt-3 px-1")
        auto = ui.switch(t("Book automatically"), value=init.auto).props("dense")
        ui.label(
            t("Due transactions are booked without asking when the app starts. Best for fixed amounts such as rent.")
        ).classes("caption -mt-3 px-1")
        comment = (
            ui.textarea(t("Note"), value=init.comment, placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )
        if editing:
            ui.label(
                t("Changes apply from the next occurrence on. Transactions already booked stay as they are.")
            ).classes("caption px-1")
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Save Changes") if editing else t("Add Recurring Transaction")).props("unelevated")

    def read() -> RecurringInput:
        kind = typ.value
        return RecurringInput(
            type=kind,
            amount=amount.value,
            currency=cur.value or "",
            category={TX_EXPENDITURE: exp_cat.value, TX_INCOME: inc_cat.value}.get(kind) or "",
            from_account=(from_acc.value or "") if kind != TX_INCOME else "",
            to_account=(to_acc.value or "") if kind != TX_EXPENDITURE else "",
            comment=comment.value or "",
            start=parse_date(start.value),
            recur_months=int(recur.value or 1),
            end=parse_date(end.value),
            auto=bool(auto.value),
        )

    def changed() -> None:
        kind = typ.value
        exp_cat.set_visibility(kind == TX_EXPENDITURE)
        inc_cat.set_visibility(kind == TX_INCOME)
        from_acc.set_visibility(kind != TX_INCOME)
        to_acc.set_visibility(kind != TX_EXPENDITURE)
        set_options(inc_cat, income_category_options(ctx))
        for sel in (from_acc, to_acc):
            set_options(sel, account_options(ctx, currency=cur.value))
        inp = read()
        try:
            rec_svc.validate(ctx, inp)
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if touched["value"] or editing else "")
            return
        save_btn.enable()
        nxt = rec_svc.planned_next_due(ctx, inp, editing.id if editing else "", first_booked)
        if inp.end and nxt > inp.end:
            status.set_text(t("This series has ended"))
        else:
            status.set_text(t("Next on {date}", date=i18n.date_label(nxt)))

    def save() -> None:
        inp = read()
        try:
            with ctx.store.history.action(N_("Save a recurring transaction")):  # booking included, one undo
                series = rec_svc.save(ctx, inp, editing.id if editing else "", first_booked)
                booking = rec_svc.book_automatic(ctx, date.today(), series.id) if series.auto else Booking()
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        message = t("Changes saved") if editing else t("Recurring transaction added")
        if booking.booked:
            n = len(booking.booked)
            message += " · " + tn("{n} transaction booked", "{n} transactions booked", n)
        undoable(message)
        dlg.close()
        if on_saved:
            on_saved()

    save_btn.on_click(save)
    for el in (typ, amount, cur, exp_cat, inc_cat, from_acc, to_acc, start, recur, end, auto):
        el.on_value_change(changed)
    changed()
    touched["value"] = True


def _account_select(label: str, value: str) -> ui.select:
    sel = ui.select({value: value} if value else {}, label=label, value=value or None, with_input=True)
    return sel.props("dense outlined").classes("w-full flex-1 min-w-0")


# ------------------------------------------------------------------ the page
def _caption(ctx: Context, series: Recurring, last: date | None) -> str:
    parts = [fmt(series.amount, series.currency), recurrence_label(series.recur_months)]
    if rec_svc.ended(series):
        parts.append(t("ended"))
    else:
        parts.append(t("next on {date}", date=i18n.date_label(series.next_due)))
        if series.end_date:
            parts.append(t("until {date}", date=i18n.date_label(series.end_date)))
    if series.auto:
        parts.append(t("books automatically"))
    if last:
        parts.append(t("last booked on {date}", date=i18n.date_label(last)))
    return " · ".join(parts)


def page() -> None:
    with frame(t("Recurring"), "/recurring", narrow=True) as (ctx, actions):
        with actions:
            ui.button(
                t("New Recurring Transaction"), icon="add", on_click=lambda: open_recurring_sheet(ctx, refresh)
            ).props("unelevated")
        ui.label(
            t(
                "Rent, premiums, subscriptions: transactions that repeat. When one is due, book it here; "
                "a series that books automatically needs no confirmation."
            )
        ).classes("caption -mt-2")
        inputs: dict[tuple[str, date], tuple[ui.number, ui.input]] = {}

        def changes() -> rec_svc.Changes:
            return {key: (a.value, parse_date(d.value)) for key, (a, d) in inputs.items()}

        @ui.refreshable
        def due_list() -> None:
            today = date.today()
            occurrences = rec_svc.due(ctx, today)
            inputs.clear()
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    ui.label(t("Due")).classes("group-header")
                    ui.space()
                    if len(occurrences) > 1:
                        ui.button(t("Book All"), on_click=book_all).props("flat dense no-caps").classes("link-btn")
                with ui.element("div").classes("group"):
                    if not occurrences:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("Nothing is due.")).classes("text-2")
                    earlier: dict[str, date] = {}
                    for occ in occurrences:
                        _due_row(ctx, occ, earlier.get(occ.series.id))
                        earlier.setdefault(occ.series.id, occ.date)

        def _due_row(ctx: Context, occ: Occurrence, waits_for: date | None) -> None:
            series = occ.series
            with ui.element("div").classes("group-row flex-wrap"):
                with ui.column().classes("gap-0 flex-1 min-w-[10rem]"):
                    ui.label(rec_svc.title(ctx, series)).classes("truncate")
                    caption = f"{i18n.date_label(occ.date)} · {t(TX_TYPES[series.type])}"
                    if waits_for:
                        caption += " · " + t("after the one on {date}", date=i18n.date_label(waits_for))
                    ui.label(caption).classes("caption")
                if waits_for:
                    ui.label(fmt(series.amount, series.currency)).classes("text-2")
                    return
                amount = (
                    ui.number(value=series.amount, min=0, format="%.2f")
                    .props(f'dense outlined inputmode=decimal suffix="{series.currency}"')
                    .classes("w-36")
                )
                when = date_input(t("Date"), occ.date.isoformat()).classes("w-40")
                inputs[(series.id, occ.date)] = (amount, when)
                with ui.row().classes("gap-0 no-wrap"):
                    ui.button(t("Book"), on_click=lambda: book(occ, amount.value, parse_date(when.value))).props(
                        "flat dense no-caps"
                    ).classes("text-primary")
                    ui.button(t("Skip"), on_click=lambda: skip(occ)).props("flat dense no-caps").classes("text-2")

        @ui.refreshable
        def series_list() -> None:
            rows = rec_svc.series(ctx)
            last = rec_svc.last_booked(ctx)
            with ui.column().classes("w-full gap-0"):
                ui.label(t("Series")).classes("group-header")
                with ui.element("div").classes("group"):
                    if not rows:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No recurring transactions yet.")).classes("text-2")
                    for series in rows:
                        with ui.element("div").classes("group-row"):
                            with ui.column().classes("gap-0 flex-1 min-w-0"):
                                ui.label(rec_svc.title(ctx, series)).classes(
                                    "truncate" + (" text-2" if rec_svc.ended(series) else "")
                                )
                                ui.label(_caption(ctx, series, last.get(series.id))).classes("caption")
                                if series.comment:
                                    ui.label(series.comment).classes("caption truncate")
                            with ui.row().classes("row-actions gap-0 no-wrap"):
                                ui.button(
                                    icon="o_edit",
                                    on_click=lambda s=series: open_recurring_sheet(ctx, refresh, editing=s),
                                ).props(ROW_ACTION).classes("text-primary").tooltip(t("Edit"))
                                ui.button(icon="o_delete", on_click=lambda s=series: delete(s)).props(
                                    ROW_ACTION
                                ).classes("text-negative").tooltip(t("Delete"))
                    with ui.element("div").classes("group-row"):
                        ui.button(
                            t("Add Recurring Transaction…"), on_click=lambda: open_recurring_sheet(ctx, refresh)
                        ).props("flat dense no-caps").classes("link-btn -ml-2")

        def refresh() -> None:
            due_list.refresh()
            series_list.refresh()
            refresh_due_badge()

        def book(occ: Occurrence, amount: float | None, booking_date: date | None) -> None:
            try:
                rec_svc.book(ctx, occ.series.id, occ.date, amount, booking_date)
            except (ValidationError, FxError) as exc:
                error(str(exc))
                return
            undoable(t("Booked {name}", name=rec_svc.title(ctx, occ.series)))
            refresh()

        def skip(occ: Occurrence) -> None:
            try:
                rec_svc.skip(ctx, occ.series.id, occ.date)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(t("Skipped {name} on {date}", name=rec_svc.title(ctx, occ.series), date=i18n.date_label(occ.date)))
            refresh()

        def book_all() -> None:
            booking = rec_svc.book_all(ctx, date.today(), changes())
            if booking.booked:
                n = len(booking.booked)
                undoable(tn("Booked {n} transaction", "Booked {n} transactions", n))
            if booking.failed:
                n, (_, reason) = len(booking.failed), booking.failed[0]
                error(tn("{n} could not be booked: {reason}", "{n} could not be booked: {reason}", n, reason=reason))
            refresh()

        def delete(series: Recurring) -> None:
            def do() -> None:
                rec_svc.delete(ctx, [series.id])
                undoable(t("Deleted {name}", name=rec_svc.title(ctx, series)))
                refresh()

            confirm(
                t("Delete this recurring transaction?"),
                do,
                detail=t("Transactions it has already booked stay."),
            )

        due_list()
        series_list()
        on_data_changed(refresh)
