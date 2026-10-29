"""Recurring (Transactions · Recurring): what repeats, on one page.

*Due* (F05: the occurrences to book or skip), *Your Series* (F05: the series the user made, with the sheet to add or
edit one) and *Found in Your Transactions* (F17: charges that repeat at a rhythm, each with *Make Recurring…*, which
opens the series sheet prefilled from the last charge, and *Ignore*; the ignored ones folded below with *Watch
Again*). `/subscriptions` leads here. Nothing is booked without the user, except series that book automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.domain import subscriptions as rules
from budget_app.domain.fx import FxError
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, TX_TYPES, Recurring, SubscriptionIgnore
from budget_app.i18n import N_, t, tn
from budget_app.services import recurring as rec_svc
from budget_app.services import subscriptions as sub_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.recurring import Booking, Occurrence, RecurringInput
from budget_app.services.subscriptions import Subscription
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    frame,
    named,
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
    payee_input,
    set_options,
)
from budget_app.ui.widgets.tags import tag_select

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
        payee = payee_input(ctx, init.payee)
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
        tags = tag_select(ctx, init.tags)  # F10
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
            payee=(payee.value or "") if kind in (TX_EXPENDITURE, TX_INCOME) else "",
            tags=list(tags.value or []),
        )

    def changed() -> None:
        kind = typ.value
        exp_cat.set_visibility(kind == TX_EXPENDITURE)
        inc_cat.set_visibility(kind == TX_INCOME)
        payee.set_visibility(kind in (TX_EXPENDITURE, TX_INCOME))
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


def caption(sub: Subscription) -> str:
    """Two facts on the row: '49.90 CHF · Every month · next around 15 Nov 2026'."""
    return " · ".join(
        [
            fmt(sub.amount, sub.currency),
            recurrence_label(sub.period),
            t("next around {date}", date=i18n.date_label(sub.next)),
        ]
    )


def details(sub: Subscription) -> str:
    """The rest, in the tooltip: '8 charges since 15 Mar 2026 · last on 15 Oct 2026'."""
    return " · ".join(
        [
            tn("{n} charge since {date}", "{n} charges since {date}", sub.count, date=i18n.date_label(sub.first)),
            t("last on {date}", date=i18n.date_label(sub.last)),
        ]
    )


def yearly_label(amount: float, currency: str) -> str:
    return t("{amount} a year", amount=fmt(amount, currency))


def _header(title: str, note: str = "") -> None:
    with ui.row().classes("w-full items-end gap-2"):
        ui.label(title).classes("group-header")
        ui.space()
        if note:
            ui.label(note).classes("caption pb-1.5 pr-1")


def _empty(text: str) -> None:
    with ui.element("div").classes("group-row py-3"):
        ui.label(text).classes("text-2")


def page() -> None:
    with frame(t("Recurring"), "/recurring", narrow=True) as (ctx, actions):
        currency = ctx.store.settings.display_currency
        with actions:
            ui.button(
                t("New Recurring Transaction"), icon="add", on_click=lambda: open_recurring_sheet(ctx, refresh)
            ).props("unelevated")
        inputs: dict[tuple[str, date], tuple[ui.number, ui.input]] = {}

        def changes() -> rec_svc.Changes:
            return {key: (a.value, parse_date(d.value)) for key, (a, d) in inputs.items()}

        @ui.refreshable
        def due_list() -> None:
            inputs.clear()
            if not rec_svc.series(ctx):
                return  # nothing can be due yet; Your Series says how to start
            occurrences = rec_svc.due(ctx, date.today())
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    ui.label(t("Due")).classes("group-header")
                    ui.space()
                    if len(occurrences) > 1:
                        ui.button(t("Book All"), on_click=book_all).props("flat dense no-caps").classes("link-btn")
                with ui.element("div").classes("group"):
                    if not occurrences:
                        _empty(t("Nothing is due."))
                    earlier: dict[str, date] = {}
                    for occ in occurrences:
                        _due_row(ctx, occ, earlier.get(occ.series.id))
                        earlier.setdefault(occ.series.id, occ.date)

        def _due_row(ctx: Context, occ: Occurrence, waits_for: date | None) -> None:
            series = occ.series
            with ui.element("div").classes("group-row flex-wrap"):
                # the name gets the full width: next to the inputs a long COICOP label was cut off
                with ui.column().classes("gap-0 flex-1 " + ("min-w-[10rem]" if waits_for else "w-full")):
                    ui.label(rec_svc.title(ctx, series))
                    caption = f"{i18n.date_label(occ.date)} · {t(TX_TYPES[series.type])}"
                    if waits_for:
                        caption += " · " + t("after the one on {date}", date=i18n.date_label(waits_for))
                    ui.label(caption).classes("caption")
                if waits_for:
                    ui.label(fmt(series.amount, series.currency)).classes("text-2")
                    return
                with ui.row().classes("w-full items-center gap-2"):
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
            spend = sub_svc.recurring_spend(ctx, currency)  # F17: what the running expenditure series cost a year
            note = t("spending about {amount} a year", amount=fmt(sum(r.yearly_view for r in spend), currency))
            with ui.column().classes("w-full gap-0"):
                _header(t("Your Series"), note if spend else "")
                with ui.element("div").classes("group"):
                    if not rows:
                        _empty(
                            t(
                                "Rent, premiums, a salary: add what repeats with New Recurring Transaction, or make "
                                "one of the charges found in your transactions recurring. When one is due, book it "
                                "here or on the Overview; one that books automatically needs no confirmation."
                            )
                        )
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
                                named(
                                    ui.button(
                                        icon="o_edit",
                                        on_click=lambda s=series: open_recurring_sheet(ctx, refresh, editing=s),
                                    )
                                    .props(ROW_ACTION)
                                    .classes("text-primary"),
                                    t("Edit"),
                                )
                                named(
                                    ui.button(icon="o_delete", on_click=lambda s=series: delete(s))
                                    .props(ROW_ACTION)
                                    .classes("text-negative"),
                                    t("Delete"),
                                )

        @ui.refreshable
        def found_list() -> None:
            subs = sub_svc.detect(ctx, currency)
            note = ""
            if subs:
                total = sum(s.yearly_view for s in subs)
                note = tn(
                    "{n} subscription · about {amount} a year",
                    "{n} subscriptions · about {amount} a year",
                    len(subs),
                    amount=fmt(total, currency),
                )
            with ui.column().classes("w-full gap-0"):
                _header(t("Found in Your Transactions"), note)
                with ui.element("div").classes("group"):
                    if not subs:
                        _empty(
                            t(
                                "Streaming, phone, gym, insurance: a charge that comes back at the same rhythm for "
                                "about the same amount shows here once it was charged {n} times ({m} times for "
                                "half-yearly and yearly ones). Nothing is booked for you.",
                                n=rules.MIN_CHARGES,
                                m=rules.MIN_CHARGES_LONG,
                            )
                        )
                    for sub in subs:
                        with ui.element("div").classes("group-row flex-wrap"):
                            with ui.column().classes("gap-0 flex-1 min-w-[12rem]") as text:
                                ui.label(sub.name).classes("truncate")
                                ui.label(caption(sub)).classes("caption")
                            text.tooltip(details(sub))
                            ui.label(yearly_label(sub.yearly, sub.currency)).classes("num whitespace-nowrap")
                            with ui.row().classes("items-center gap-0 no-wrap"):
                                ui.button(t("Make Recurring…"), on_click=lambda s=sub: make_recurring(s)).props(
                                    "flat dense no-caps"
                                ).classes("text-primary")
                                named(
                                    ui.button(icon="o_visibility_off", on_click=lambda s=sub: ignore(s))
                                    .props(ROW_ACTION)
                                    .classes("text-2"),
                                    t("Ignore"),
                                )

        @ui.refreshable
        def ignored_list() -> None:
            items = sub_svc.ignored(ctx)
            if not items:
                return
            title = tn("Ignored · {n} subscription", "Ignored · {n} subscriptions", len(items))
            with ui.expansion(title).classes("w-full group closed-bills").props("dense"):
                for item in items:
                    with ui.element("div").classes("group-row"):
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(item.name or item.key).classes("truncate")
                            ui.label(item.currency).classes("caption")
                        ui.button(t("Watch Again"), on_click=lambda i=item: unignore(i)).props(
                            "flat dense no-caps"
                        ).classes("text-primary")

        def refresh() -> None:
            due_list.refresh()
            series_list.refresh()
            found_list.refresh()
            ignored_list.refresh()
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

        def make_recurring(sub: Subscription) -> None:
            try:
                prefill = sub_svc.to_recurring(ctx, sub)
            except ValidationError as exc:
                error(str(exc))
                return
            open_recurring_sheet(ctx, refresh, prefill=prefill, first_booked=sub.last_id)

        def ignore(sub: Subscription) -> None:
            sub_svc.ignore(ctx, sub)
            undoable(t("Ignored {name}", name=sub.name))
            refresh()

        def unignore(item: SubscriptionIgnore) -> None:
            try:
                sub_svc.unignore(ctx, item.id)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(t("Watching {name} again", name=item.name))
            refresh()

        due_list()
        series_list()
        found_list()
        ignored_list()
        on_data_changed(refresh)
