"""F31. The Overview's *Due* card: everything that wants doing, one row each, with its action in the row.

The items come from `services.reminders` (the kinds turned on in Settings); the sidebar's badge beside Overview
counts the same list. A row says what it is and when (red once overdue), links to the page where it is dealt with,
and offers the one action that settles it: *Record…* a balance, *Book* a recurring transaction or a loan payment
(the earliest one due, at the series' amount or the schedule's; another amount or date on the Recurring or Loans
tab), *Pay…* a bill, *Open* a goal. A series or loan with several due is one row ("3 occurrences due since …").
"""

from __future__ import annotations

from datetime import date

from nicegui import ui

from budget_app.domain.fx import FxError
from budget_app.domain.models import BILL_OPEN
from budget_app.i18n import N_, t, tc, tn
from budget_app.services import bills as bills_svc
from budget_app.services import loans as loan_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import reminders as rem_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.reminders import Reminder
from budget_app.ui.layout import data_changed, error, fmt, undoable
from budget_app.ui.pages.budget_overview import account_entry_dialog
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

ICONS = {
    rem_svc.BALANCES: "o_account_balance_wallet",
    rem_svc.RECURRING: "o_event_repeat",
    rem_svc.BILLS: "o_request_quote",
    rem_svc.LOANS: "o_credit_score",
    rem_svc.GOALS: "o_flag",
}
ACTIONS = {
    rem_svc.BALANCES: N_("Record…"),
    rem_svc.RECURRING: N_("Book"),
    rem_svc.BILLS: N_("Pay…"),
    rem_svc.LOANS: N_("Book"),
    rem_svc.GOALS: N_("Open"),
}
TONES = {rem_svc.LATE: "text-neg", rem_svc.WARN: "notable-warn"}
SHOWN = 6  # rows before "Show n more"


# ------------------------------------------------------------------ the actions
def _book(ctx: Context, item: Reminder) -> None:
    """Book a recurring occurrence or a loan payment as planned: the series' amount, the schedule's interest and
    repayment, on the day it was due."""
    book = rec_svc.book if item.kind == rem_svc.RECURRING else loan_svc.book
    try:
        book(ctx, item.ref, item.on or date.today())  # every recurring and loan item has its date
    except (ValidationError, FxError) as exc:
        error(str(exc))
    else:
        undoable(t("Booked {name}", name=item.title))
    data_changed()


def _pay_bill(ctx: Context, item: Reminder) -> None:
    bill = bills_svc.get(ctx, item.ref)
    if bill is None or bill.status != BILL_OPEN:
        error(t("This bill is no longer in the list"))
        data_changed()
        return
    open_transaction_sheet(ctx, data_changed, prefill=bills_svc.payment_input(ctx, bill), bill_id=bill.id)


def _record_balance(ctx: Context, item: Reminder) -> None:
    account_entry_dialog(ctx, data_changed, month=item.month, account_id=item.ref)


def act(ctx: Context, item: Reminder) -> None:
    """The row's action."""
    if item.kind == rem_svc.BALANCES:
        _record_balance(ctx, item)
    elif item.kind in (rem_svc.RECURRING, rem_svc.LOANS):
        _book(ctx, item)
    elif item.kind == rem_svc.BILLS:
        _pay_bill(ctx, item)
    else:
        ui.navigate.to(item.link)


# ------------------------------------------------------------------ the card
def row(ctx: Context, item: Reminder) -> None:
    with ui.element("div").classes("due-row"):
        ui.icon(ICONS[item.kind], size="20px").classes("due-icon " + TONES[item.tone])
        with ui.link(target=item.link).classes("due-text"):
            ui.label(item.title).classes("n-title")
            ui.label(item.sub).classes("n-sub" + (" text-neg" if item.tone == rem_svc.LATE else ""))
        ui.label(fmt(item.amount, item.currency) if item.amount is not None else "").classes("n-amount")
        ui.button(t(ACTIONS[item.kind]), on_click=lambda: act(ctx, item)).props("flat dense no-caps").classes(
            "due-action text-primary"
        ).mark(f"due-{item.kind}")


def due_card(ctx: Context, currency: str, today: date | None = None) -> None:
    """The card; one quiet line when nothing is due, nothing at all when every kind is turned off in Settings."""
    if not any(rem_svc.enabled(ctx, kind) for kind in rem_svc.KINDS):
        return
    items = rem_svc.due_list(ctx, currency, today)
    if not items:
        with ui.row().classes("due-empty items-center gap-2 no-wrap"):
            ui.icon("o_task_alt", size="18px").classes("text-3")
            ui.label(t("Nothing due. Balances to record, bookings and bills show here when they need you."))
        return
    state = {"all": len(items) <= SHOWN}
    with ui.element("div").classes("surface flex flex-col gap-1 due"):
        with ui.row().classes("w-full items-baseline gap-2"):
            ui.label(tc("todo", "Due")).classes("section-title")
            ui.label(tn("{n} thing to do", "{n} things to do", len(items))).classes("caption")

        @ui.refreshable
        def rows() -> None:
            for item in items if state["all"] else items[:SHOWN]:
                row(ctx, item)
            if not state["all"]:
                more = len(items) - SHOWN
                ui.button(tn("Show {n} more", "Show {n} more", more), on_click=show_all).props(
                    "flat dense no-caps"
                ).classes("link-btn self-start -ml-2")

        def show_all() -> None:
            state["all"] = True
            rows.refresh()

        rows()
