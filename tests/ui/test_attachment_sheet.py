"""Attachments in the transaction sheet and on the Transactions page (F12), driven like a user would."""

import asyncio
from datetime import date

from nicegui import ElementFilter, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified, undo_toasts

from budget_app.services import attachments as att_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets import attachments as att_ui
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

PDF = b"%PDF-1.4\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def expense(ctx, setup, amount: float = 42.0, payee: str = "Coop"):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 10),
            type="expenditure",
            amount=amount,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            payee=payee,
        ),
    )


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


def newest(user: User, kind: type):
    with user:
        return max(user.find(kind=kind).elements, key=lambda e: e.id)


async def upload(user: User, *named: tuple[str, bytes]) -> None:
    element = newest(user, ui.upload)
    await element.handle_uploads([SmallFileUpload(name, "application/octet-stream", data) for name, data in named])
    await settle()


def icon_button(user: User, tooltip: str) -> list[ui.button]:
    """Visible icon buttons by their tooltip text, oldest first."""
    with user:
        targets = {
            tip.props.get("target") for tip in ElementFilter(kind=ui.tooltip, local_scope=False) if tip.text == tooltip
        }
    return sorted((b for b in user.find(kind=ui.button).elements if f"#{b.html_id}" in targets), key=lambda b: b.id)


def label(user: User, text: str) -> ui.label:
    found = [e for e in user.find(kind=ui.label).elements if e.text == text]
    assert found, f"no label {text!r}"
    return max(found, key=lambda e: e.id)


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


async def open_editor(user: User, ctx, tx) -> None:
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=tx)
    await user.should_see("Edit Transaction")


# ------------------------------------------------------------------ edit sheet
async def test_files_are_attached_at_once_in_the_edit_sheet(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    await open_editor(user, ctx, tx)
    await user.should_see("Drop files here or click to choose")
    await upload(user, ("Quittung.pdf", PDF), ("photo.png", PNG))

    names = [a.name for a in att_svc.list_for(ctx, tx.id)]
    assert names == ["Quittung.pdf", "photo.png"]
    assert notified(user, "Attached “Quittung.pdf”") and notified(user, "Attached “photo.png”")
    await user.should_see("2 files")
    await user.should_see("Quittung.pdf")
    caption = label(user, f"15 bytes · {date.today().strftime('%-d %b %Y')}")
    assert caption.visible
    # the photo shows itself as a thumbnail, served by the attachment route
    photo = next(a for a in att_svc.list_for(ctx, tx.id) if a.name == "photo.png")
    assert [i for i in user.find(kind=ui.image).elements if i.source == att_ui.url(photo)]


async def test_remove_and_undo(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    await open_editor(user, ctx, tx)
    [remove] = icon_button(user, "Remove")
    click(user, remove)
    await settle()
    assert ctx.store.attachments.all() == [] and att_svc.path(ctx, att).is_file()
    assert notified(user, "Removed “receipt.pdf”")
    toast = max(undo_toasts(user), key=lambda n: n.id)
    UserInteraction(user, {toast}, None).trigger("undo")
    assert att_svc.list_for(ctx, tx.id) == [att]
    await user.should_see("Undone: Remove an attachment")


async def test_a_refused_file_is_explained(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    await open_editor(user, ctx, tx)
    await upload(user, ("setup.exe", b"MZ\x90"))
    await user.should_see("“setup.exe” can’t be attached")
    assert ctx.store.attachments.all() == []
    UserInteraction(user, {newest(user, ui.upload)}, None).trigger("rejected")
    await user.should_see("Some files were not attached")


async def test_preview_of_a_pdf_and_an_image(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    pdf = att_svc.add(ctx, tx.id, "invoice.pdf", PDF)
    png = att_svc.add(ctx, tx.id, "photo.png", PNG)
    await open_editor(user, ctx, tx)

    def frames() -> list:
        return [e for e in user.find(kind=ui.element).elements if e.tag == "iframe"]

    assert frames() == []
    UserInteraction(user, {label(user, "invoice.pdf")}, None).trigger("click")
    [frame] = frames()
    assert frame.props["src"] == att_ui.url(pdf) and frame.props["title"] == "invoice.pdf"
    UserInteraction(user, {label(user, "photo.png")}, None).trigger("click")  # another file replaces it
    assert frames() == []
    assert len([i for i in user.find(kind=ui.image).elements if i.source == att_ui.url(png)]) == 2
    UserInteraction(user, {label(user, "photo.png")}, None).trigger("click")  # and a second click hides it
    assert len([i for i in user.find(kind=ui.image).elements if i.source == att_ui.url(png)]) == 1


async def test_removing_the_previewed_file_closes_the_preview(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "invoice.pdf", PDF)
    await open_editor(user, ctx, tx)
    UserInteraction(user, {label(user, "invoice.pdf")}, None).trigger("click")
    [remove] = icon_button(user, "Remove")
    click(user, remove)
    await settle()
    assert not [e for e in user.find(kind=ui.element).elements if e.tag == "iframe"]


async def test_open_in_a_browser_follows_a_link_to_the_route(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "invoice.pdf", PDF)
    await open_editor(user, ctx, tx)
    links = [link for link in user.find(kind=ui.link).elements if link.props.get("href") == att_ui.url(att)]
    assert links and links[0].props.get("target") == "_blank"


async def test_open_in_the_app_window_uses_the_system_viewer(user: User, ctx, setup, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(att_ui, "_native", lambda: True)
    monkeypatch.setattr(att_svc, "system_open", opened.append)
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "invoice.pdf", PDF)
    lost = att_svc.add(ctx, tx.id, "lost.pdf", PDF)
    att_svc.path(ctx, lost).unlink()
    await open_editor(user, ctx, tx)
    await user.should_see("The file is missing")
    first, second = icon_button(user, "Open")
    click(user, first)
    assert opened == [att_svc.path(ctx, att)]
    click(user, second)
    await user.should_see("The file of “lost.pdf” is missing from the data folder")
    assert len(opened) == 1


async def test_save_a_copy(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "invoice.pdf", PDF)
    lost = att_svc.add(ctx, tx.id, "lost.pdf", PDF)
    await open_editor(user, ctx, tx)
    att_svc.path(ctx, lost).unlink()  # gone after the sheet opened
    first, second = icon_button(user, "Save a Copy…")
    click(user, first)
    for _ in range(100):
        if user.download.http_responses:
            break
        await asyncio.sleep(0.05)
    assert user.download.http_responses[-1].content == PDF
    click(user, second)
    await user.should_see("The file of “lost.pdf” is missing from the data folder")


async def test_removing_an_attachment_already_gone_is_reported(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "invoice.pdf", PDF)
    await open_editor(user, ctx, tx)
    att_svc.remove(ctx, att.id)  # removed elsewhere meanwhile
    [remove] = icon_button(user, "Remove")
    click(user, remove)
    await user.should_see("This attachment no longer exists")


async def test_the_drop_zone_opens_the_file_picker(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    await open_editor(user, ctx, tx)
    drop = max((e for e in user.find(kind=ui.element).elements if "attach-drop" in e.classes), key=lambda e: e.id)
    UserInteraction(user, {drop}, None).trigger("click")  # runs the uploader's pickFiles in the browser
    listeners = {listener.type: listener for listener in drop._event_listeners.values()}
    assert "addFiles(e.dataTransfer.files)" in listeners["drop.prevent"].js_handler


# ------------------------------------------------------------------ new transaction
async def test_a_new_transaction_stores_its_files_when_saved(user: User, ctx, setup) -> None:
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx)
    await user.should_see("New Transaction")
    await upload(user, ("Beleg.pdf", PDF), ("extra.png", PNG), ("bad.exe", b"MZ"))
    await user.should_see("“bad.exe” can’t be attached")
    await user.should_see("15 bytes · Attached when you save")
    assert ctx.store.attachments.all() == []
    [_, drop_png] = icon_button(user, "Remove")
    click(user, drop_png)
    await settle()
    await user.should_see("1 file")

    with user:
        amount = next(e for e in user.find(kind=ui.number).elements if e.props.get("label") == "Amount")
        amount.value = 12.5
        next(e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Category").value = "01.1"
        acc = next(e for e in user.find(kind=ui.select).elements if e.props.get("label") == "From Account")
        acc.value = setup["chf"].id
    click(user, max((b for b in user.find(kind=ui.button).elements if b.text == "Add Transaction"), key=lambda b: b.id))
    await settle()
    [tx] = ctx.store.transactions.all()
    [att] = att_svc.list_for(ctx, tx.id)
    assert att.name == "Beleg.pdf" and att_svc.read(ctx, att) == PDF
    # one step to undo: the transaction and its file together
    act = undo_svc.latest(ctx)
    assert undo_svc.label(ctx, act) == "Save a transaction" and act.tables == {"transactions", "attachments"}
    undo_svc.undo(ctx, act.id)
    assert ctx.store.transactions.all() == [] and ctx.store.attachments.all() == []


async def test_the_add_page_forgets_waiting_files_after_saving(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    await upload(user, ("Beleg.pdf", PDF))
    await user.should_see("1 file")
    with user:
        next(e for e in user.find(kind=ui.number).elements if e.props.get("label") == "Amount").value = 9
        next(e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Category").value = "01.1"
        acc = next(e for e in user.find(kind=ui.select).elements if e.props.get("label") == "From Account")
        acc.value = setup["chf"].id
    click(user, next(b for b in user.find(kind=ui.button).elements if b.text == "Add Transaction"))
    await settle()
    assert len(ctx.store.attachments.all()) == 1
    await user.should_not_see("Attached when you save")
    await user.should_not_see("1 file")


# ------------------------------------------------------------------ Transactions page
async def test_the_list_counts_attachments_and_finds_them_by_name(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup, payee="Coop")
    other = expense(ctx, setup, amount=7, payee="Migros")
    att_svc.add(ctx, tx.id, "Garantie Waschmaschine.pdf", PDF)
    att_svc.add(ctx, tx.id, "photo.png", PNG)
    await user.open("/transactions")
    grid = newest(user, ui.aggrid)
    rows = {r["id"]: r for r in grid.options["rowData"]}
    assert rows[tx.id]["attachments"] == 2 and rows[other.id]["attachments"] is None
    col = next(c for c in grid.options["columnDefs"] if c["field"] == "attachments")
    assert col["headerName"] == "Attachments" and "attach_file" in col[":cellRenderer"]

    with user:
        search = next(e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "Search")
        search.value = "waschmaschine"
    assert [r["id"] for r in grid.options["rowData"]] == [tx.id]


async def test_attaching_in_the_sheet_refreshes_the_list_behind_it(user: User, ctx, setup) -> None:
    tx = expense(ctx, setup)
    await user.open("/transactions")
    grid = newest(user, ui.aggrid)
    with user:
        open_transaction_sheet(ctx, editing=tx)
    await upload(user, ("receipt.pdf", PDF))
    assert grid.options["rowData"][0]["attachments"] == 1
