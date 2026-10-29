"""Who owes what (F23): one row per person you paid for, with what is open, the oldest item and what came back this
year; the open items below each (a click opens the transaction); *Settle Up…* records the repayment as one undoable
step, *Summary…* gives the open items as text to copy or as a PDF or text file to save."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from functools import partial

from nicegui import ui

from budget_app import i18n
from budget_app.domain.models import Transaction
from budget_app.i18n import t, tn
from budget_app.services import shared as shared_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.shared import Person
from budget_app.services.transactions import OpenAdvance
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    frame,
    on_data_changed,
    parse_date,
    save_file,
    segmented,
    sheet,
    sheet_buttons,
    success,
    undoable,
)
from budget_app.ui.widgets.selects import account_options

ROW_ACTION = "flat dense round size=sm"


# ------------------------------------------------------------------ texts
def amounts(values: dict[str, float]) -> str:
    """ "120.00 CHF + 30.00 EUR"."""
    return " + ".join(fmt(v, cur) for cur, v in values.items())


def open_line(p: Person) -> str:
    """ "120.00 CHF open · 2 items · oldest 12 Sep 2026"."""
    if not p.items:
        return t("Nothing open")
    parts = [t("{amount} open", amount=amounts(p.open)), shared_svc.count_label(len(p.items))]
    if p.oldest is not None:
        parts.append(t("oldest {date}", date=i18n.date_label(p.oldest)))
    return " · ".join(parts)


def settled_line(p: Person, today: date | None = None) -> str:
    year = (today or date.today()).year
    if not p.settled_year:
        return t("nothing settled in {year}", year=str(year))
    return t("settled in {year}: {amount}", year=str(year), amount=amounts(p.settled_year))


def item_line(item: OpenAdvance) -> str:
    """ "12 Sep 2026 · Restaurant X · 45.00 of 60.00 CHF open"."""
    tx = item.tx
    share = fmt(item.outstanding, tx.currency)
    if abs(item.outstanding - tx.advance_amount) >= shared_svc.EPS:
        share = t("{open} of {share}", open=fmt(item.outstanding), share=fmt(tx.advance_amount, tx.currency))
    return f"{i18n.date_label(tx.date)} · {shared_svc.item_label(tx)} · {t('{amount} open', amount=share)}"


# ------------------------------------------------------------------ the settle-up sheet
def open_settle_sheet(ctx: Context, key: str, on_saved: Callable[[], None] | None = None) -> None:
    try:
        p = shared_svc.person(ctx, key)
    except ValidationError as exc:
        error(str(exc))
        return
    currencies = list(p.open)
    if not currencies:
        error(t("{name} owes nothing", name=p.name))
        return
    with sheet(t("Settle Up with {name}", name=p.name), width="30rem") as dlg:
        cur = segmented({c: c for c in currencies}, currencies[0], full=True)
        cur.set_visibility(len(currencies) > 1)
        amount = (
            ui.number(t("Amount Received"), value=p.open[currencies[0]], min=0, format="%.2f")
            .props("outlined inputmode=decimal")
            .classes("hero-amount w-full")
        )
        when = date_input(t("Date"), date.today().isoformat()).classes("w-full")
        account = ui.select({}, label=t("To Account"), with_input=True).props("dense outlined").classes("w-full")
        comment = (
            ui.textarea(t("Note"), value="", placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )
        preview = ui.label().classes("caption px-1")

        def changed() -> None:
            currency = cur.value
            amount.props(f'suffix="{currency}"')
            options = account_options(ctx, currency=currency, loans=False)
            if account.value not in options:
                account.value = None
            account.set_options(options, value=account.value or (next(iter(options)) if len(options) == 1 else None))
            try:
                settlement = shared_svc.plan(p, currency, amount.value)
            except ValidationError as exc:
                preview.set_text(str(exc))
                save_btn.disable()
                return
            parts = [
                t("{date} · {amount}", date=i18n.date_label(item.tx.date), amount=fmt(part, currency))
                for item, part in settlement.parts
            ]
            preview.set_text(
                tn("Settles {n} item: {items}", "Settles {n} items: {items}", len(parts), items=", ".join(parts))
            )
            save_btn.set_enabled(bool(account.value))

        def save() -> None:
            try:
                booked = shared_svc.settle(
                    ctx, key, cur.value, amount.value, account.value or "", parse_date(when.value), comment.value or ""
                )
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            total = sum(tx.amount for tx in booked)
            undoable(t("Recorded {amount} from {name}", amount=fmt(total, cur.value), name=p.name))
            if on_saved:
                on_saved()

        save_btn = sheet_buttons(dlg, t("Record Repayment"), save)

        def currency_changed() -> None:
            amount.set_value(p.open[cur.value])
            changed()

        cur.on_value_change(currency_changed)
        amount.on_value_change(changed)
        account.on_value_change(changed)
        changed()


# ------------------------------------------------------------------ the summary sheet
def open_summary_sheet(ctx: Context, key: str) -> None:
    try:
        p = shared_svc.person(ctx, key)
    except ValidationError as exc:
        error(str(exc))
        return
    text = shared_svc.summary_text(p)
    with sheet(t("Summary for {name}", name=p.name), width="34rem") as dlg:
        ui.label(t("To send yourself; the app sends nothing.")).classes("caption -mt-2")
        ui.textarea(value=text).props("outlined readonly autogrow").classes("w-full font-mono text-[13px]")

        def copy() -> None:
            ui.clipboard.write(text)
            success(t("Copied"))

        async def save_pdf() -> None:
            await save_file(shared_svc.summary_pdf(p), shared_svc.file_name(p, "pdf"))

        async def save_text() -> None:
            await save_file(text.encode("utf-8"), shared_svc.file_name(p, "txt"))

        with ui.row().classes("w-full justify-end gap-2 pt-1 flex-wrap"):
            ui.button(t("Save as Text…"), on_click=save_text).props("flat").classes("text-primary")
            ui.button(t("Save as PDF…"), on_click=save_pdf).props("flat").classes("text-primary")
            ui.button(t("Copy"), icon="o_content_copy", on_click=copy).props("unelevated")
            ui.button(t("Close"), on_click=dlg.close).props("flat").classes("text-primary")


# ------------------------------------------------------------------ the page
def _icon_button(icon: str, tip: str, on_click: Callable[[], object]) -> ui.button:
    """A row's icon-only button: the tooltip doubles as its accessible name."""
    button = ui.button(icon=icon, on_click=on_click).props(f'{ROW_ACTION} aria-label="{tip}"').classes("text-primary")
    button.tooltip(tip)
    return button


def page() -> None:
    from budget_app.ui.widgets.transaction_form import open_transaction_sheet  # noqa: PLC0415 - avoids an import cycle

    with frame(t("Shared Expenses"), "/shared", narrow=True) as (ctx, _actions):
        ui.label(
            t(
                "Who owes you what: the share of your payments made for others (advance payments), what came "
                "back, and a settle-up that records the repayment. Name the person as debtor when you record a "
                "payment partly made for others."
            )
        ).classes("caption -mt-2")
        opened: set[str] = set()

        @ui.refreshable
        def people_list() -> None:
            rows = shared_svc.people(ctx)
            owing = [p for p in rows if p.items]
            settled = [p for p in rows if not p.items]
            with ui.column().classes("w-full gap-0"):
                ui.label(tn("{n} person owes you", "{n} people owe you", len(owing))).classes("group-header")
                with ui.element("div").classes("group"):
                    if not owing:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("Nobody owes you anything.")).classes("text-2")
                    for p in owing:
                        _row(p)
            if settled:
                title = tn("Settled · {n} person", "Settled · {n} people", len(settled))
                with ui.expansion(title).classes("w-full group closed-bills").props("dense"):
                    for p in settled:
                        with ui.element("div").classes("group-row flex-wrap"):
                            with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                                ui.label(p.name).classes("truncate")
                                ui.label(settled_line(p)).classes("caption")
                            _icon_button("o_description", t("Summary…"), partial(open_summary_sheet, ctx, p.key))

        def _row(p: Person) -> None:
            with ui.element("div").classes("group-row items-start py-2.5 flex-wrap"):
                with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                    ui.label(p.name).classes("font-medium truncate")
                    ui.label(open_line(p)).classes("caption")
                    ui.label(settled_line(p)).classes("caption")
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.button(t("Settle Up…"), on_click=lambda: open_settle_sheet(ctx, p.key, refresh)).props(
                        "flat dense no-caps"
                    ).classes("text-primary")
                    _icon_button("o_description", t("Summary…"), lambda: open_summary_sheet(ctx, p.key))
                    toggle = _icon_button(
                        "expand_less" if p.key in opened else "expand_more", t("Open items"), lambda: show(p.key)
                    )
                    toggle.classes(remove="text-primary", add="text-2")
                items = ui.column().classes("w-full gap-0 pl-2 pb-1")
                items.set_visibility(p.key in opened)
                with items:
                    for item in p.items:

                        def edit(tx: Transaction = item.tx) -> None:
                            with ui.context.client.content:  # outside the list, which refreshes on save
                                open_transaction_sheet(ctx, refresh, editing=tx)

                        with (
                            ui.row()
                            .classes("w-full items-center gap-2 no-wrap cursor-pointer")
                            .props("role=button tabindex=0") as row
                        ):
                            row.on("click", edit).on("keydown.enter", edit)
                            ui.label(item_line(item)).classes("caption truncate")

                def show(key: str, items: ui.column = items, toggle: ui.button = toggle) -> None:
                    if key in opened:
                        opened.discard(key)
                    else:
                        opened.add(key)
                    items.set_visibility(key in opened)
                    toggle.props(f"icon={'expand_less' if key in opened else 'expand_more'}")

        def refresh() -> None:
            people_list.refresh()

        people_list()
        on_data_changed(refresh)
