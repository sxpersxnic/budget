"""Attachments in the transaction form (F12), and the route that serves their files to the page.

Editing a transaction, files are attached and removed at once, each with its own Undo. A new
transaction keeps the chosen files in memory and stores them together with it, in the same undoable
step; closing the form without saving drops them. Paying a QR bill (F03), the bill's file is listed as
well: it is the bill's already and goes with the transaction when it is saved.

Files reach the page only through `/attachment/<id>`: it serves the file of an existing attachment
record, by that record's id, from the path the service builds out of plain ids, with the content
type of the file's (checked) extension. In the app window, *Open* hands the file to the system's
viewer instead.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from nicegui import app, events, ui

from budget_app import i18n
from budget_app.domain import attachments as files
from budget_app.domain.models import Attachment
from budget_app.i18n import t, tn
from budget_app.services import attachments as attachments_svc
from budget_app.services.context import Context, ValidationError, get_ctx
from budget_app.ui.layout import error, save_file, undoable

ROUTE = "/attachment/{attachment_id}"
ICONS = {"pdf": "o_picture_as_pdf", "image": "o_image", "office": "o_description", "text": "o_article"}


def url(att: Attachment, download: bool = False) -> str:
    return f"/attachment/{att.id}" + ("?download=1" if download else "")


def serve(attachment_id: str, download: bool = False) -> Response:
    """The file of an attachment record; 404 for anything else."""
    ctx = get_ctx()
    att = attachments_svc.get(ctx, attachment_id)
    if att is None:
        return Response(status_code=404)
    try:
        target, _, ft = attachments_svc.verified(ctx, att)  # an accepted type, by name and by content
    except ValidationError:
        return Response(status_code=404)
    return FileResponse(
        target,
        media_type=ft.content_type,  # from the checked extension, never from the record's text
        filename=att.name,
        content_disposition_type="attachment" if download else "inline",
        # an id always names the same content (files are never rewritten), so the page may keep it a while
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


def register(api: FastAPI) -> None:
    """Add the file route to the app (once, at startup)."""
    api.add_api_route(ROUTE, serve, methods=["GET"], include_in_schema=False)


def _native() -> bool:
    return app.native.main_window is not None


class AttachmentList:
    """The *Attachments* section: drop zone and file picker, the files with preview, Open, Save a Copy, Remove.

    `tx_id` is the transaction being edited, or "" for a new one (files wait for `store`).
    `on_change` runs after a file was attached or removed at once (to refresh the list behind the sheet).
    `bill_id`: a new transaction that pays this QR bill shows the bill's files too (F03).
    """

    def __init__(self, ctx: Context, tx_id: str = "", on_change: Callable[[], object] | None = None, bill_id: str = ""):
        self.ctx, self.tx_id, self._on_change = ctx, tx_id, on_change
        self.bill_id = "" if tx_id else bill_id
        self._pending: list[tuple[str, bytes]] = []  # new transaction: (name, content) until it is saved
        self._preview_id = ""
        with ui.column().classes("w-full gap-1") as self._root:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(t("Attachments")).classes("text-[13px] font-semibold text-2")
                ui.space()
                self._summary = ui.label().classes("caption")
            self.upload = (
                ui.upload(
                    multiple=True,
                    auto_upload=True,
                    max_file_size=files.MAX_BYTES,
                    on_upload=self._uploaded,
                    on_multi_upload=self._uploads_done,  # also sends the files of one choice in one request
                    on_rejected=lambda: error(
                        t("Some files were not attached: only PDF files, photos, office and text files up to 20 MB")
                    ),
                )
                .props('accept="' + ",".join(f".{e}" for e in files.EXTENSIONS) + '"')
                .classes("hidden")
            )
            add_files = f"getElement({self.upload.id}).$refs.qRef.addFiles(e.dataTransfer.files)"
            with (
                ui.element("div")
                .classes("attach-drop")
                .on("click", lambda: self.upload.run_method("pickFiles"))
                .on("dragover.prevent", js_handler="() => {}")
                .on("drop.prevent", js_handler=f"(e) => {add_files}")
            ):
                ui.icon("o_attach_file", size="18px")
                ui.label(t("Drop files here or click to choose"))
            self._list = ui.column().classes("w-full gap-1")
            self._preview = ui.element("div").classes("attach-preview w-full")
        self.refresh()

    # ------------------------------------------------------------------ data
    def store(self, tx_id: str) -> None:
        """Attach the waiting files to the transaction just saved (inside its action)."""
        for name, data in self._pending:
            attachments_svc.add(self.ctx, tx_id, name, data)
        self._pending.clear()

    def clear(self) -> None:
        self._pending.clear()
        self._preview_id = ""
        self.refresh()

    def add_pending(self, name: str, data: bytes) -> None:
        """A file a new transaction starts with (a scanned receipt, F04), stored when it is saved."""
        attachments_svc.check(name, data)
        self._pending.append((files.display_name(name), data))
        self.refresh()

    async def _uploaded(self, e: events.UploadEventArguments) -> None:
        data = await e.file.read()
        try:
            if not self.tx_id:
                attachments_svc.check(e.file.name, data)
                self._pending.append((files.display_name(e.file.name), data))
                self.refresh()
                return
            att = attachments_svc.add(self.ctx, self.tx_id, e.file.name, data)
        except ValidationError as exc:
            error(str(exc))
            return
        self._changed(t("Attached “{name}”", name=att.name))

    def _uploads_done(self) -> None:
        self.upload.reset()  # empty the queue, so the same file can be chosen again

    def _remove(self, att: Attachment) -> None:
        try:
            attachments_svc.remove(self.ctx, att.id)
        except ValidationError as exc:
            error(str(exc))
            return
        if self._preview_id == att.id:
            self._preview_id = ""
        self._changed(t("Removed “{name}”", name=att.name))

    def _drop_pending(self, index: int) -> None:
        del self._pending[index]
        self.refresh()

    def _changed(self, message: str) -> None:
        self.refresh()
        with self._root:  # not in the row that was clicked: the refresh has just replaced it
            undoable(message)
            if self._on_change:
                self._on_change()

    async def _save_copy(self, att: Attachment) -> None:
        try:
            data = attachments_svc.read(self.ctx, att)
        except ValidationError as exc:
            error(str(exc))
            return
        await save_file(data, att.name)

    def _open(self, att: Attachment) -> None:
        """In the app window: the system's viewer (a browser session follows a link to the route instead)."""
        try:
            attachments_svc.open_file(self.ctx, att)
        except ValidationError as exc:
            error(str(exc))

    def _toggle_preview(self, att: Attachment) -> None:
        self._preview_id = "" if self._preview_id == att.id else att.id
        self._show_preview(att if self._preview_id else None)  # the list stays as it is

    # ------------------------------------------------------------------ view
    def refresh(self) -> None:
        stored = attachments_svc.list_for(self.ctx, self.tx_id) if self.tx_id else []
        from_bill = attachments_svc.list_for_bill(self.ctx, self.bill_id) if self.bill_id else []
        stored += [a for a in from_bill if not a.transaction_id]
        n = len(stored) + len(self._pending)
        self._summary.set_text(tn("{n} file", "{n} files", n) if n else "")
        self._list.clear()
        with self._list:
            for att in stored:
                self._row(att)
            for i, (name, data) in enumerate(self._pending):
                self._pending_row(i, name, data)
        self._show_preview(next((a for a in stored if a.id == self._preview_id), None))

    def _row(self, att: Attachment) -> None:
        kind, present = attachments_svc.kind(att), attachments_svc.exists(self.ctx, att)
        with ui.element("div").classes("attach-row"):
            with ui.element("div").classes("attach-thumb"):
                if kind == "image" and present:
                    ui.image(url(att)).props("no-spinner fit=cover").classes("w-full h-full")
                else:
                    ui.icon(ICONS.get(kind, "o_draft"), size="22px")
            with ui.column().classes("gap-0 min-w-0 attach-name"):
                name = ui.label(att.name).classes("ellipsis")
                when = t("Attached when you save") if self.bill_id else i18n.date_label(att.added)
                caption = f"{attachments_svc.size_label(att.size)} · {when}"
                ui.label(caption if present else t("The file is missing")).classes("caption")
            if kind in ("image", "pdf") and present:
                name.classes("cursor-pointer").on("click", lambda att=att: self._toggle_preview(att))
                name.tooltip(t("Show or hide the preview"))
            with ui.row().classes("gap-0 no-wrap"):
                if _native():
                    ui.button(icon="o_open_in_new", on_click=lambda att=att: self._open(att)).props(
                        "flat round dense size=sm"
                    ).classes("text-primary").tooltip(t("Open"))
                else:
                    with ui.link(target=url(att), new_tab=True).classes("attach-link"):
                        ui.icon("o_open_in_new", size="18px").tooltip(t("Open"))
                ui.button(icon="o_download", on_click=lambda att=att: self._save_copy(att)).props(
                    "flat round dense size=sm"
                ).classes("text-primary").tooltip(t("Save a Copy…"))
                if not self.bill_id:  # the bill's file stays the bill's until it is paid
                    ui.button(icon="o_delete", on_click=lambda att=att: self._remove(att)).props(
                        "flat round dense size=sm"
                    ).classes("text-3").tooltip(t("Remove"))

    def _pending_row(self, index: int, name: str, data: bytes) -> None:
        ft = files.file_type(name, data)
        with ui.element("div").classes("attach-row"):
            with ui.element("div").classes("attach-thumb"):
                ui.icon(ICONS.get(ft.kind if ft else "", "o_draft"), size="22px")
            with ui.column().classes("gap-0 min-w-0 attach-name"):
                ui.label(name).classes("ellipsis")
                ui.label(f"{attachments_svc.size_label(len(data))} · {t('Attached when you save')}").classes("caption")
            ui.button(icon="close", on_click=lambda i=index: self._drop_pending(i)).props(
                "flat round dense size=sm"
            ).classes("text-3").tooltip(t("Remove"))

    def _show_preview(self, att: Attachment | None) -> None:
        self._preview.clear()
        self._preview.set_visibility(att is not None)
        if att is None:
            return
        with self._preview:
            if attachments_svc.kind(att) == "image":
                ui.image(url(att)).props("fit=contain no-spinner").classes("attach-preview-image")
            else:  # a PDF: the browser's own viewer
                frame = ui.element("iframe").classes("attach-preview-pdf")
                frame.props["src"] = url(att)
                frame.props["title"] = att.name
