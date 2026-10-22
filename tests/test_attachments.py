"""Attachments (F12): accepted files, storage in the data folder, undo, cleanup, backups and the file route."""

import io
import json
import zipfile
from datetime import date, datetime

import httpx
import pytest
from fastapi import FastAPI

from budget_app.domain import attachments as files
from budget_app.domain.models import Attachment
from budget_app.services import archive
from budget_app.services import attachments as att_svc
from budget_app.services import context as context_module
from budget_app.services import export as export_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.storage import backup
from budget_app.storage.history import History
from budget_app.ui.widgets import attachments as att_ui

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
DOCX = b"PK\x03\x04" + b"\x00" * 26


def expense(ctx, setup, amount=42.0):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 10),
            type="expenditure",
            amount=amount,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
        ),
    )


def file_of(ctx, att):
    return ctx.store.data_dir / "attachments" / att.transaction_id / att.stored


# ------------------------------------------------------------------ accepted files
@pytest.mark.parametrize(
    ("name", "data", "content_type", "kind"),
    [
        ("Rechnung.pdf", PDF, "application/pdf", "pdf"),
        ("IMG_0042.JPG", JPEG, "image/jpeg", "image"),
        ("photo.jpeg", JPEG, "image/jpeg", "image"),
        ("scan.png", PNG, "image/png", "image"),
        ("receipt.webp", WEBP, "image/webp", "image"),
        ("IMG_1234.HEIC", HEIC, "image/heic", "image"),
        ("Vertrag.docx", DOCX, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "office"),
        ("Liste.ods", DOCX, "application/vnd.oasis.opendocument.spreadsheet", "office"),
        ("notes.txt", "Garantie bis 2028 – Händler Müller".encode(), "text/plain; charset=utf-8", "text"),
        ("export.csv", "Datum;Betrag\n01.10.;12,50\n".encode("cp1252"), "text/csv; charset=utf-8", "text"),
    ],
)
def test_accepted_types(name, data, content_type, kind):
    ft = att_svc.check(name, data)
    assert (ft.content_type, ft.kind) == (content_type, kind)


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("empty.pdf", b"", "“empty.pdf” is empty"),
        ("big.pdf", b"%PDF-" + b"x" * files.MAX_BYTES, "larger than 20 MB"),
        ("setup.exe", b"MZ\x90\x00", "can’t be attached"),
        ("page.html", b"<html><script>alert(1)</script>", "can’t be attached"),
        ("logo.svg", b"<svg onload=alert(1)>", "can’t be attached"),
        ("noextension", PDF, "can’t be attached"),
        ("photo.png", b"<html><script>alert(1)</script>", "is not a valid .png file"),  # renamed web page
        ("fake.pdf", PNG, "is not a valid .pdf file"),
        ("binary.txt", b"abc\x00def", "is not a valid .txt file"),
        ("macro.docx", b"\xd0\xcf\x11\xe0", "is not a valid .docx file"),  # an old binary Office file
        ("garbled.csv", b"\x81\x8d\x8f\x90\x9d" * 3, "is not a valid .csv file"),
    ],
)
def test_refused_files(name, data, message):
    with pytest.raises(ValidationError, match=message):
        att_svc.check(name, data)


def test_shown_names_drop_paths_and_control_characters():
    assert files.display_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert files.display_name("C:\\Users\\me\\Beleg Coop.pdf") == "Beleg Coop.pdf"
    assert files.display_name("a\x00b\nc.pdf") == "abc.pdf"
    assert files.display_name("") == "file"
    long = files.display_name("x" * 300 + ".pdf")
    assert len(long) == 200 and long.endswith(".pdf")
    assert len(files.display_name("y" * 300)) == 200
    assert files.extension("archive.tar.GZ") == "gz" and files.extension(".bashrc") == ""
    with pytest.raises(ValueError, match="unsafe"):
        files.stored_name("../x", "pdf")


def test_size_labels():
    assert att_svc.size_label(512) == "512 bytes"
    assert att_svc.size_label(300) == "300 bytes"
    assert att_svc.size_label(2048) == "2 KB"
    assert att_svc.size_label(1100) == "1 KB"
    assert att_svc.size_label(3 * 1024 * 1024 + 200_000) == "3.2 MB"


# ------------------------------------------------------------------ storing
def test_a_file_is_copied_into_the_data_folder_under_a_generated_name(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "../../Quittung Migros.pdf", PDF)
    assert (att.transaction_id, att.name, att.size) == (tx.id, "Quittung Migros.pdf", len(PDF))
    assert att.content_type == "application/pdf"
    assert att.stored == f"{att.id}.pdf" and att.added == date.today()
    assert file_of(ctx, att).read_bytes() == PDF
    assert att_svc.path(ctx, att) == file_of(ctx, att)
    assert att_svc.read(ctx, att) == PDF and att_svc.exists(ctx, att) and att_svc.kind(att) == "pdf"
    second = att_svc.add(ctx, tx.id, "IMG_1.HEIF", HEIC)
    assert second.stored.endswith(".heic") and att_svc.kind(second) == "image"
    assert [a.id for a in att_svc.list_for(ctx, tx.id)] == [att.id, second.id]
    assert att_svc.counts(ctx) == {tx.id: 2}
    assert att_svc.names_by_transaction(ctx) == {tx.id: "Quittung Migros.pdf IMG_1.HEIF"}
    assert att_svc.get(ctx, att.id) == att and att_svc.get(ctx, "") is None
    # nothing but the file and its record: no temporary files left behind
    assert sorted(p.name for p in file_of(ctx, att).parent.iterdir()) == sorted([att.stored, second.stored])
    # undo history names the action
    assert undo_svc.label(ctx, undo_svc.latest(ctx)) == "Attach a file"
    change = undo_svc.latest(ctx).changes[0]
    assert undo_svc.describe_change(ctx, change) == "Attachment added: IMG_1.HEIF"


def test_adding_needs_an_existing_transaction_and_an_accepted_file(ctx, setup):
    with pytest.raises(ValidationError, match="Transaction not found"):
        att_svc.add(ctx, "nope", "a.pdf", PDF)
    tx = expense(ctx, setup)
    with pytest.raises(ValidationError, match="can’t be attached"):
        att_svc.add(ctx, tx.id, "a.exe", b"MZ")
    assert ctx.store.attachments.all() == [] and not (ctx.store.data_dir / "attachments").exists()


def test_adding_refuses_a_folder_that_leads_elsewhere(ctx, setup, tmp_path_factory):
    tx = expense(ctx, setup)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    root = ctx.store.data_dir / "attachments"
    root.mkdir()
    (root / tx.id).symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValidationError):
        att_svc.add(ctx, tx.id, "a.pdf", PDF)
    assert list(elsewhere.iterdir()) == []


def test_records_that_name_impossible_places_give_no_path(ctx, setup):
    tx = expense(ctx, setup)
    for bad in (
        Attachment(transaction_id=tx.id, name="x", stored="../../settings.json"),
        Attachment(transaction_id="../..", name="x", stored="abc.pdf"),
        Attachment(transaction_id=tx.id, name="x", stored="abc"),
    ):
        assert att_svc.path(ctx, bad) is None and not att_svc.exists(ctx, bad)
        with pytest.raises(ValidationError, match="missing from the data folder"):
            att_svc.read(ctx, bad)


# ------------------------------------------------------------------ removing, undo and cleanup
def test_removing_keeps_the_file_until_the_removal_can_no_longer_be_undone(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    att_svc.remove(ctx, att.id)
    assert ctx.store.attachments.all() == [] and file_of(ctx, att).is_file()
    assert att_svc.cleanup(ctx) == 0 and file_of(ctx, att).is_file()  # the history can still bring it back
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert att_svc.list_for(ctx, tx.id) == [att] and att_svc.read(ctx, att) == PDF
    with pytest.raises(ValidationError, match="no longer exists"):
        att_svc.remove(ctx, "unknown")

    att_svc.remove(ctx, att.id)
    ctx.store.history = History(ctx.store.data_dir / "other-history.jsonl")  # the old actions have dropped out
    assert att_svc.cleanup(ctx) == 1
    assert not file_of(ctx, att).exists() and not file_of(ctx, att).parent.exists()


def test_cleanup_keeps_what_records_name_and_what_is_not_ours(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    ctx.store.history = History(ctx.store.data_dir / "empty-history.jsonl")
    folder = file_of(ctx, att).parent
    (folder / "orphan1234.pdf").write_bytes(PDF)  # no record: goes
    (folder / f".{att.id}.pdf.tmp").write_bytes(b"half")  # an interrupted write: goes
    (folder / "My Notes.txt").write_text("mine")  # not a name the app writes: stays
    (folder / "sub").mkdir()  # stays
    (ctx.store.data_dir / "attachments" / "README").write_text("x")  # stays
    (ctx.store.data_dir / "attachments" / "empty").mkdir()  # an empty folder of ours: goes
    assert att_svc.cleanup(ctx) == 2
    assert sorted(p.name for p in folder.iterdir()) == sorted([att.stored, "My Notes.txt", "sub"])
    assert not (ctx.store.data_dir / "attachments" / "empty").exists()
    assert (ctx.store.data_dir / "attachments" / "README").exists()


def test_cleanup_without_attachments_does_nothing(ctx):
    assert att_svc.cleanup(ctx) == 0


def test_cleanup_reports_files_it_cannot_delete(ctx, setup, monkeypatch, caplog):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    ctx.store.attachments.delete([att.id])
    ctx.store.history = History(ctx.store.data_dir / "empty-history.jsonl")

    def refuse(self, missing_ok=False):
        raise PermissionError("locked")

    monkeypatch.setattr(type(file_of(ctx, att)), "unlink", refuse)
    assert att_svc.cleanup(ctx) == 0
    assert "Could not delete" in caplog.text and file_of(ctx, att).exists()


def test_deleting_a_transaction_removes_its_attachments_in_the_same_step(ctx, setup):
    tx, other = expense(ctx, setup), expense(ctx, setup, amount=7)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    kept = att_svc.add(ctx, other.id, "other.png", PNG)
    tx_svc.delete(ctx, [tx.id])
    assert ctx.store.attachments.all() == [kept] and file_of(ctx, att).is_file()
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)  # one undo brings back the transaction and its file
    assert ctx.store.transactions.get(tx.id) is not None
    assert att_svc.list_for(ctx, tx.id) == [att] and att_svc.exists(ctx, att)


def test_undoing_an_attach_and_redoing_it(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    undone = undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.attachments.all() == []
    assert att_svc.cleanup(ctx) == 0  # the undo can itself be undone, so the file stays
    undo_svc.undo(ctx, undone.id)
    assert att_svc.list_for(ctx, tx.id) == [att] and att_svc.exists(ctx, att)


def test_system_open_uses_the_platforms_viewer(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(att_svc.subprocess, "Popen", calls.append)
    monkeypatch.setattr(att_svc.sys, "platform", "darwin")
    att_svc.system_open(tmp_path / "a.pdf")
    monkeypatch.setattr(att_svc.sys, "platform", "linux")
    att_svc.system_open(tmp_path / "a.pdf")
    monkeypatch.setattr(att_svc.sys, "platform", "win32")
    monkeypatch.setattr(att_svc.os, "startfile", lambda p: calls.append(["startfile", str(p)]), raising=False)
    att_svc.system_open(tmp_path / "a.pdf")
    path = str(tmp_path / "a.pdf")
    assert calls == [["open", path], ["xdg-open", path], ["startfile", path]]


def test_json_export_lists_the_attachment_records(ctx, setup):
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    doc = json.loads(export_svc.everything_json(ctx))
    [row] = doc["attachments"]
    assert (row["transaction_id"], row["name"], row["added"]) == (tx.id, "receipt.pdf", date.today().isoformat())


# ------------------------------------------------------------------ backups (F25)
def test_backup_round_trip_brings_the_files_back(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "Quittung.pdf", PDF)
    photo = att_svc.add(ctx, tx.id, "photo.png", PNG)
    data = archive.create(ctx)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read(archive.MANIFEST))
        assert manifest["version"] == 2 and manifest["attachments"] == 2
        inside = f"attachments/{tx.id}/{att.stored}"
        assert zf.read(inside) == PDF and zf.getinfo(inside).compress_type == zipfile.ZIP_STORED

    # afterwards: one attachment removed for good, another one added
    att_svc.remove(ctx, att.id)
    file_of(ctx, att).unlink()
    later = att_svc.add(ctx, tx.id, "later.pdf", PDF)
    safety = archive.restore(ctx, archive.read(data), now=datetime(2026, 10, 22, 9, 0, 0))

    assert {a.id for a in ctx.store.attachments.all()} == {att.id, photo.id}
    assert att_svc.read(ctx, att) == PDF and att_svc.read(ctx, photo) == PNG
    assert not file_of(ctx, later).exists()  # the newer file went with the records it belonged to
    assert (safety / "attachments" / tx.id / later.stored).read_bytes() == PDF
    assert "later.pdf" in (safety / "attachments.csv").read_text()

    # restoring the copy made before the restore brings the newer state back, files included
    [saved] = archive.saved_backups(ctx)
    archive.restore(ctx, archive.files_in(saved.path))
    assert {a.id for a in ctx.store.attachments.all()} == {photo.id, later.id}
    assert att_svc.read(ctx, later) == PDF


def test_a_backup_without_attachments_stays_version_1(ctx, setup):
    expense(ctx, setup)
    with zipfile.ZipFile(io.BytesIO(archive.create(ctx))) as zf:
        manifest = json.loads(zf.read(archive.MANIFEST))
    assert manifest["version"] == 1 and manifest["attachments"] == 0
    assert all("/" not in n for n in zf.namelist())


def test_a_backup_skips_records_whose_file_is_missing(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    file_of(ctx, att).unlink()
    files_ = archive.read(archive.create(ctx))
    assert not [n for n in files_ if n.startswith("attachments/")]
    archive.restore(ctx, files_)
    assert ctx.store.attachments.all() == [att] and not att_svc.exists(ctx, att)


def test_update_backups_hard_link_the_attachments(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    folder = backup.backup_on_version_change(ctx.store.data_dir, "9.9.9", now=datetime(2026, 1, 2, 3, 4, 5))
    copy = folder / "attachments" / tx.id / att.stored
    assert copy.read_bytes() == PDF and copy.stat().st_ino == file_of(ctx, att).stat().st_ino
    att_svc.remove(ctx, att.id)
    [saved] = archive.saved_backups(ctx)
    archive.restore(ctx, archive.files_in(saved.path))
    assert att_svc.read(ctx, att) == PDF


def test_update_backups_copy_where_links_fail(ctx, setup, monkeypatch):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)

    def no_links(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(backup.os, "link", no_links)
    target = ctx.store.data_dir / "copy"
    assert backup.link_attachments(ctx.store.data_dir, target) == 1
    assert (target / "attachments" / tx.id / att.stored).read_bytes() == PDF


def test_restore_writes_only_the_files_its_records_name(ctx, setup):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    files_ = archive.read(archive.create(ctx))
    files_[f"attachments/{tx.id}/stray123.pdf"] = PDF
    archive.restore(ctx, files_)
    assert sorted(p.name for p in file_of(ctx, att).parent.iterdir()) == [att.stored]


def zip_with(entries: dict[str, bytes], version: int = 2) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(archive.MANIFEST, json.dumps({"format": archive.FORMAT, "version": version}))
        zf.writestr("transactions.csv", "date,id\n")
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.parametrize(
    "name",
    [
        "attachments/../settings.json",
        "attachments/abc/../../evil.pdf",
        "attachments/abc/def/ghi.pdf",
        "attachments/abc/evil name.pdf",
        "attachments/abc/noext",
        "attachments/abc/.hidden.pdf",
        "attachments/ab\\c/x.pdf",
        "attachments/x.pdf",
    ],
)
def test_read_refuses_attachment_paths_of_another_shape(name):
    with pytest.raises(ValidationError, match="unexpected file"):
        archive.read(zip_with({name: PDF}))


def test_read_refuses_oversized_and_too_many_attachments(monkeypatch):
    big = b"\x00" * (files.MAX_BYTES + 1)  # compresses to almost nothing: the declared size counts
    with pytest.raises(ValidationError, match="unexpected file"):
        archive.read(zip_with({"attachments/abc/def.pdf": big}))
    monkeypatch.setattr(archive, "MAX_ATTACHMENTS", 1)
    with pytest.raises(ValidationError, match="too large"):
        archive.read(zip_with({"attachments/abc/a.pdf": PDF, "attachments/abc/b.pdf": PDF}))
    assert archive.read(zip_with({"attachments/abc/a.pdf": PDF}))["attachments/abc/a.pdf"] == PDF


def test_read_refuses_a_damaged_attachment():
    data = bytearray(zip_with({"attachments/abc/def.pdf": PDF * 20}))
    at = data.find(b"attachments/abc/def.pdf") + len("attachments/abc/def.pdf") + 5  # inside the data
    data[at] ^= 0xFF
    with pytest.raises(ValidationError, match="can’t be read"):
        archive.read(bytes(data))


def test_check_refuses_attachment_records_with_unsafe_names(ctx, setup):
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    files_ = archive.read(archive.create(ctx))
    files_["attachments.csv"] = files_["attachments.csv"].replace(
        f",{ctx.store.attachments.all()[0].stored},".encode(), b",../../settings.json,"
    )
    with pytest.raises(ValidationError, match="can’t be read"):
        archive.check(files_)


def test_create_refuses_a_backup_restore_would_refuse(ctx, setup, monkeypatch):
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValidationError, match="too large for one backup"):
        archive.create(ctx)


# ------------------------------------------------------------------ the file route
@pytest.fixture
async def client(ctx):
    api = FastAPI()
    att_ui.register(api)
    context_module._ctx = ctx
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://budget") as c:
            yield c
    finally:
        context_module._ctx = None


async def test_the_route_serves_files_of_known_records(ctx, setup, client):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "Quittung März.pdf", PDF)
    r = await client.get(att_ui.url(att))
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert (
        r.headers["content-disposition"].startswith("inline;")
        and "Quittung%20M%C3%A4rz.pdf" in r.headers["content-disposition"]
    )
    r = await client.get(att_ui.url(att, download=True))
    assert r.headers["content-disposition"].startswith("attachment;")


async def test_the_route_serves_the_type_of_the_extension_not_of_the_record(ctx, setup, client):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "page.png", PNG)
    att.content_type = "text/html"  # as if attachments.csv was edited by hand
    ctx.store.attachments.update(att)
    assert (await client.get(att_ui.url(att))).headers["content-type"] == "image/png"


@pytest.mark.parametrize(
    "path",
    [
        "/attachment/unknown",
        "/attachment/..%2F..%2Fsettings.json",
        "/attachment/%2E%2E",
        "/attachment/..",
        "/attachment/",
        "/attachment/../settings.json",
        "/attachment/settings.json",
    ],
)
async def test_the_route_serves_nothing_else(ctx, setup, client, path):
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    (ctx.store.data_dir / "settings.json").write_text('{"secret": 1}')
    r = await client.get(path)
    assert r.status_code == 404 and b"secret" not in r.content


async def test_the_route_refuses_records_that_point_elsewhere_or_lost_their_file(ctx, setup, client):
    tx = expense(ctx, setup)
    sneaky = Attachment(transaction_id="..", name="s.json", stored="settings.json", id="sneaky")
    odd = Attachment(transaction_id=tx.id, name="x.exe", stored="abc.exe", id="odd")
    ctx.store.attachments.add_many([sneaky, odd])
    (ctx.store.data_dir / "attachments" / tx.id).mkdir(parents=True)
    (ctx.store.data_dir / "attachments" / tx.id / "abc.exe").write_bytes(b"MZ")
    lost = att_svc.add(ctx, tx.id, "lost.pdf", PDF)
    file_of(ctx, lost).unlink()
    for att in (sneaky, odd, lost):
        assert (await client.get(att_ui.url(att))).status_code == 404


def test_an_update_backup_that_fails_is_tried_again(ctx, setup, monkeypatch, caplog):
    expense(ctx, setup)

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "link_attachments", broken)
    assert backup.backup_on_version_change(ctx.store.data_dir, "9.9.9") is None
    assert "Could not back up" in caplog.text
    assert not (ctx.store.data_dir / backup.MARKER).exists()  # so the next start tries again
