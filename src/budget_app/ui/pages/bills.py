"""Bills (F03, decision 15): drop QR bills, see the open ones by due date, pay them through the transaction sheet.

The drop zone takes PDFs and photos; each QR bill in them becomes an open bill with the file as its attachment.
*Mark Paid…* opens the transaction sheet prefilled from the bill (payee, amount, currency, note; the category
is suggested with its reason, F07); saving books the expenditure and marks the bill paid in one undo step.
Paid and dismissed bills are kept below, collapsed.
"""

from __future__ import annotations

from datetime import date

from nicegui import app, events, ui

from budget_app import i18n
from budget_app.domain import attachments as files
from budget_app.domain import qrbill
from budget_app.domain.models import BILL_PAID, Bill
from budget_app.i18n import t, tc, tn
from budget_app.services import attachments as attachments_svc
from budget_app.services import bills as bills_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, frame, on_data_changed, refresh_due_badge, undoable
from budget_app.ui.widgets import attachments as att_ui
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

ROW_ACTION = "flat dense round size=sm"
ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp"


def due_text(bill: Bill, today: date) -> tuple[str, str]:
    """The due date as shown, and its class: overdue in red, due within a week in orange."""
    due = bill.due_date
    if due is None:
        return t("No due date"), "text-2"
    days = (due - today).days
    if days < 0:
        return t("Overdue since {date}", date=i18n.date_label(due)), "due-late"
    if days == 0:
        return t("Due today"), "due-soon"
    return t("Due on {date}", date=i18n.date_label(due)), "due-soon" if days <= bills_svc.SOON else "text-2"


def amount_text(bill: Bill) -> str:
    return (
        fmt(bill.amount, bill.currency) if bill.amount else t("Amount to fill in ({currency})", currency=bill.currency)
    )


def _file_link(ctx: Context, bill: Bill) -> None:
    """The bill's file: opened by the system viewer in the app window, in a new tab in a browser."""
    for att in attachments_svc.list_for_bill(ctx, bill.id):
        icon = att_ui.ICONS.get(attachments_svc.kind(att), "o_draft")
        if app.native.main_window is not None:
            ui.button(att.name, icon=icon, on_click=lambda att=att: _open(ctx, att)).props(
                "flat dense no-caps size=12px"
            ).classes("link-btn -ml-1 self-start")
            continue
        with ui.link(target=att_ui.url(att), new_tab=True).classes("caption link-btn flex items-center gap-1"):
            ui.icon(icon, size="16px")
            ui.label(att.name).classes("ellipsis")


def _open(ctx: Context, att) -> None:
    try:
        attachments_svc.open_file(ctx, att)
    except ValidationError as exc:
        error(str(exc))


def page() -> None:
    with frame(t("Bills"), "/bills", narrow=True) as (ctx, _actions):
        ui.label(
            t(
                "Drop the PDF or a photo of a Swiss QR bill. It waits here until you pay it; "
                "the file stays with the payment. Nothing leaves your computer."
            )
        ).classes("caption -mt-2")

        async def uploaded(e: events.UploadEventArguments) -> None:
            data = await e.file.read()
            scan(e.file.name, data)

        def scan(name: str, data: bytes) -> None:
            try:
                result = bills_svc.scan(ctx, name, data)
            except ValidationError as exc:
                error(f"{files.display_name(name)}: {exc}")
                return
            added = result.added
            message = (
                t("Added the bill of {creditor}", creditor=added[0].creditor)
                if len(added) == 1
                else tn("Added {n} bill", "Added {n} bills", len(added))
            )
            if result.duplicates:
                n = len(result.duplicates)
                message += " · " + tn("{n} was already in the list", "{n} were already in the list", n)
            undoable(message)
            if result.refused:
                error(result.refused[0])
            refresh()

        def queue_done() -> None:
            upload.reset()  # empty the queue, so the same file can be dropped again

        upload: ui.upload = (
            ui.upload(
                multiple=True,
                auto_upload=True,
                max_file_size=files.MAX_BYTES,
                on_upload=uploaded,
                on_multi_upload=queue_done,
                on_rejected=lambda: error(t("Only PDF files and photos up to 20 MB")),
            )
            .props(f'accept="{ACCEPT}"')
            .classes("hidden")
        )
        add_files = f"getElement({upload.id}).$refs.qRef.addFiles(e.dataTransfer.files)"
        with (
            ui.element("div")
            .classes("attach-drop bill-drop")
            .on("click", lambda: upload.run_method("pickFiles"))
            .on("dragover.prevent", js_handler="() => {}")
            .on("drop.prevent", js_handler=f"(e) => {add_files}")
        ):
            ui.icon("o_qr_code_scanner")
            ui.label(t("Drop QR bills (PDF or photo)"))
            ui.label(t("or click to choose")).classes("caption")

        @ui.refreshable
        def open_list() -> None:
            today = date.today()
            rows = bills_svc.open_bills(ctx)
            with ui.column().classes("w-full gap-0"):
                ui.label(tc("bill", "Open")).classes("group-header")
                with ui.element("div").classes("group"):
                    if not rows:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No open bills.")).classes("text-2")
                    for bill in rows:
                        _open_row(bill, today)

        def _open_row(bill: Bill, today: date) -> None:
            due, due_class = due_text(bill, today)
            with ui.element("div").classes("group-row flex-wrap py-2"):
                with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                    with ui.row().classes("w-full items-baseline gap-2 no-wrap"):
                        ui.label(bill.creditor).classes("flex-1 min-w-0 truncate font-medium")
                        ui.label(amount_text(bill)).classes("bill-amount")
                    ui.label(due).classes(f"caption {due_class}")
                    details = [bill.message]
                    if bill.reference:
                        details.append(qrbill.format_reference(bill.reference_type, bill.reference))
                    ui.label(" · ".join(x for x in details if x)).classes("caption truncate")
                    _file_link(ctx, bill)
                with ui.row().classes("items-center gap-1 no-wrap ml-auto"):
                    ui.button(t("Mark Paid…"), on_click=lambda: pay(bill)).props("flat dense no-caps").classes(
                        "text-primary"
                    )
                    with ui.button(icon="o_event").props(ROW_ACTION).classes("text-primary") as when:
                        when.tooltip(t("Set the due date"))
                        with ui.menu() as menu:
                            picker = ui.date(value=bill.due or None).props("flat minimal first-day-of-week=1")
                            with ui.row().classes("w-full justify-end gap-1 px-2 pb-2"):
                                ui.button(t("No Due Date"), on_click=lambda: set_due(bill, None, menu)).props(
                                    "flat dense no-caps"
                                ).classes("text-2")
                                ui.button(t("Set"), on_click=lambda: set_due(bill, picker.value or None, menu)).props(
                                    "flat dense no-caps"
                                ).classes("text-primary")
                    ui.button(icon="o_do_not_disturb_on", on_click=lambda: dismiss(bill)).props(ROW_ACTION).classes(
                        "text-2"
                    ).tooltip(t("Dismiss"))
                    ui.button(icon="o_delete", on_click=lambda: delete(bill)).props(ROW_ACTION).classes(
                        "text-negative"
                    ).tooltip(t("Delete"))

        @ui.refreshable
        def closed_list() -> None:
            rows = bills_svc.closed_bills(ctx)
            if not rows:
                return
            title = tn("Paid and Dismissed · {n} bill", "Paid and Dismissed · {n} bills", len(rows))
            with ui.expansion(title).classes("w-full group closed-bills").props("dense"):
                for bill in rows:
                    _closed_row(bill)

        def _closed_row(bill: Bill) -> None:
            with ui.element("div").classes("group-row flex-wrap"):
                with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                    with ui.row().classes("w-full items-baseline gap-2 no-wrap"):
                        ui.label(bill.creditor).classes("flex-1 min-w-0 truncate")
                        ui.label(amount_text(bill)).classes("bill-amount text-2")
                    tx = bills_svc.payment(ctx, bill)
                    if bill.status != BILL_PAID:
                        status = t("Dismissed")
                    elif tx is None:
                        status = t("Paid")
                    else:
                        status = f"{t('Paid on {date}', date=i18n.date_label(tx.date))} · {fmt(tx.amount, tx.currency)}"
                    ui.label(status).classes("caption")
                    _file_link(ctx, bill)
                with ui.row().classes("items-center gap-1 no-wrap ml-auto"):
                    if bill.status == BILL_PAID and tx is not None:
                        ui.button(
                            t("Show Payment"), on_click=lambda tx=tx: open_transaction_sheet(ctx, refresh, editing=tx)
                        ).props("flat dense no-caps").classes("text-primary")
                    elif bill.status != BILL_PAID:
                        ui.button(t("Reopen"), on_click=lambda: reopen(bill)).props("flat dense no-caps").classes(
                            "text-primary"
                        )
                    ui.button(icon="o_delete", on_click=lambda: delete(bill)).props(ROW_ACTION).classes(
                        "text-negative"
                    ).tooltip(t("Delete"))

        def refresh() -> None:
            open_list.refresh()
            closed_list.refresh()
            refresh_due_badge()

        def pay(bill: Bill) -> None:
            if bills_svc.get(ctx, bill.id) is None:
                error(t("This bill is no longer in the list"))
                refresh()
                return
            open_transaction_sheet(ctx, refresh, prefill=bills_svc.payment_input(ctx, bill), bill_id=bill.id)

        def act(fn, bill: Bill, message: str, *args) -> None:
            try:
                fn(ctx, bill.id, *args)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(message)
            refresh()

        def set_due(bill: Bill, value: str | None, menu: ui.menu) -> None:
            menu.close()
            on = date.fromisoformat(value) if value else None
            message = t("Due on {date}", date=i18n.date_label(on)) if on else t("No due date")
            act(bills_svc.set_due, bill, f"{bill.creditor}: {message}", on)

        def dismiss(bill: Bill) -> None:
            act(bills_svc.dismiss, bill, t("Dismissed the bill of {creditor}", creditor=bill.creditor))

        def reopen(bill: Bill) -> None:
            act(bills_svc.reopen, bill, t("Reopened the bill of {creditor}", creditor=bill.creditor))

        def delete(bill: Bill) -> None:
            def do() -> None:
                bills_svc.delete(ctx, [bill.id])
                undoable(t("Deleted the bill of {creditor}", creditor=bill.creditor))
                refresh()

            detail = (
                t("Its payment stays, with the file.")
                if bill.status == BILL_PAID
                else t("Its file is removed too. Nothing is booked.")
            )
            confirm(t("Delete this bill?"), do, detail=detail)

        open_list()
        closed_list()
        on_data_changed(refresh)
