"""The Receipt sheet (F04): drop a till receipt, see it beside the transaction form, which starts with what was read.

The drop zone takes one PDF or photo. Its pictures are made in the event loop (`receipts.prepare`), the engine
reads them in a worker thread (`receipts.read`, a second or two; the sheet says "Reading the receipt…"), then the
sheet shows the receipt on the left and a new expenditure on the right: date, amount, currency and payee from the
receipt, the item lines when they add up to the total, the file under Attachments ("Attached when you save").
Without the engine (not installed, or an Intel Mac) the form starts with today's date and the user types the
amount, date and payee from the picture. Saving is the transaction form's own: one undo step for the
transaction, its lines and the file.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import events, run, ui

from budget_app import i18n
from budget_app.domain import attachments as files
from budget_app.domain.receipts import Receipt
from budget_app.i18n import t
from budget_app.services import ocr
from budget_app.services import receipts as receipts_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, sheet
from budget_app.ui.widgets.transaction_form import TransactionForm

ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp"


def summary(receipt: Receipt | None) -> str:
    """The line above the form: what was read, or what to do when nothing was."""
    if receipt is None and not ocr.available():
        return t("Reading receipts isn’t available on this computer. Type the amount, date and payee from the receipt.")
    if receipt is None or receipt.empty:
        return t("Nothing could be read from this receipt. Type the amount, date and payee from it.")
    parts = [receipt.merchant] if receipt.merchant else []
    if receipt.date:
        parts.append(i18n.date_label(receipt.date))
    if receipt.total:
        parts.append(fmt(receipt.total, receipt.currency))
    return t("Read from the receipt: {found}. Check it before you save.", found=" · ".join(parts))


def open_receipt_sheet(ctx: Context, on_saved: Callable[[], None] | None = None) -> None:
    with sheet(t("Receipt"), width="62rem") as dlg:
        ui.label(
            t(
                "Drop the PDF or a photo of a till receipt. It is read on your computer – nothing leaves it – "
                "and the expenditure is filled in for you to check. The file stays with the transaction."
            )
            if ocr.available()
            else t(
                "Drop the PDF or a photo of a till receipt and type the amount, date and payee from it; the file "
                "stays with the transaction. Reading receipts automatically isn’t available on this computer."
            )
        ).classes("caption -mt-2")

        async def uploaded(e: events.UploadEventArguments) -> None:
            data = await e.file.read()
            try:
                prepared = receipts_svc.prepare(e.file.name, data)
            except ValidationError as exc:
                error(str(exc))
                return
            drop.set_visibility(False)
            busy.set_visibility(True)
            receipt = None
            try:
                receipt = await run.io_bound(
                    receipts_svc.read, prepared, None, date.today(), ctx.store.settings.display_currency
                )
            except ValidationError as exc:
                error(str(exc))
            busy.set_visibility(False)
            show(prepared, receipt)

        def show(prepared: receipts_svc.Prepared, receipt: Receipt | None) -> None:
            inp, items = receipts_svc.prefill(ctx, receipt, date.today())
            with body:
                with ui.column().classes("receipt-side gap-1"):
                    if prepared.preview is not None:
                        ui.image(prepared.preview).props("fit=contain no-spinner").classes("receipt-image")
                    ui.label(prepared.name).classes("caption ellipsis w-full")
                with ui.column().classes("flex-1 min-w-[18rem] gap-3"):
                    ui.label(summary(receipt)).classes("caption")
                    TransactionForm(
                        ctx,
                        prefill=inp,
                        items=items,
                        files=[(prepared.name, prepared.data)],
                        on_saved=saved,
                        on_cancel=dlg.close,
                    )
            body.set_visibility(True)

        def saved(_) -> None:
            dlg.close()
            if on_saved:
                on_saved()

        upload: ui.upload = (
            ui.upload(
                auto_upload=True,
                max_file_size=files.MAX_BYTES,
                on_upload=uploaded,
                on_multi_upload=lambda: upload.reset(),  # noqa: PLW0108 - `upload` is defined below  # so the same file can be dropped again
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
        ) as drop:
            ui.icon("o_document_scanner")
            ui.label(t("Drop a receipt (PDF or photo)"))
            ui.label(t("or click to choose")).classes("caption")
        with ui.row().classes("items-center gap-2 caption") as busy:
            ui.spinner(size="18px").classes("text-primary")
            ui.label(t("Reading the receipt…"))
        busy.set_visibility(False)
        body = ui.row().classes("w-full items-start gap-5 no-wrap max-sm:flex-wrap receipt-body")
        body.set_visibility(False)
