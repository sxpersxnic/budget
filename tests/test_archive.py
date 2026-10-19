"""Full backups as ZIP files and restoring them (F25), and the JSON export of everything (F24)."""

import io
import json
import zipfile
from datetime import date, datetime

import pytest

from budget_app.domain.models import TransactionItem
from budget_app.services import archive
from budget_app.services import export as export_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.storage import backup


def add_expense(ctx, setup, amount=42.0):
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


def zip_of(files: dict[str, bytes | str], manifest: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if manifest is not None:
            zf.writestr(archive.MANIFEST, json.dumps(manifest))
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


MANIFEST_OK = {"format": archive.FORMAT, "version": archive.VERSION}


def test_backup_round_trip_restores_every_record(ctx, setup):
    add_expense(ctx, setup)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 1000)
    data = archive.create(ctx)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read(archive.MANIFEST))
        assert manifest["format"] == archive.FORMAT and "transactions.csv" in manifest["files"]
        assert all("/" not in n for n in zf.namelist())  # plain names only, no ecb/ or backups/

    # change everything after the backup, then restore it
    add_expense(ctx, setup, amount=7)
    ctx.store.settings.display_currency = "EUR"
    ctx.store.save_settings()
    files = archive.read(data)
    safety = archive.restore(ctx, files, now=datetime(2026, 10, 19, 12, 0, 0))

    assert [tx.amount for tx in ctx.store.transactions.all()] == [42.0]
    assert ctx.store.settings.display_currency == "CHF"
    assert len(ctx.store.account_entries.all()) == 1
    # the replaced records are kept, and listed as a backup made before a restore
    assert safety.name == "2026-10-19_120000_before-restore"
    assert "7.0" in (safety / "transactions.csv").read_text()
    saved = archive.saved_backups(ctx)
    assert [(b.path, b.reason) for b in saved] == [(safety, "restore")]


def test_restore_removes_tables_the_backup_does_not_have(ctx, setup):
    data = archive.create(ctx)  # before any item list exists
    tx = add_expense(ctx, setup)
    ctx.store.transaction_items.add(TransactionItem(transaction_id=tx.id, name="Bread", quantity=1, unit_price=3.5))
    files = archive.read(data)
    files.pop("transaction_items.csv", None)  # as from an older version without item lists
    archive.restore(ctx, files)
    assert ctx.store.transaction_items.all() == []
    assert not (ctx.store.data_dir / "transaction_items.csv").read_text().strip().count("\n")


def test_restore_from_a_saved_backup_folder(ctx, setup):
    add_expense(ctx, setup)
    folder = backup.backup_on_version_change(ctx.store.data_dir, "9.9.9", now=datetime(2026, 1, 2, 3, 4, 5))
    assert folder is not None
    add_expense(ctx, setup, amount=1)
    [saved] = archive.saved_backups(ctx)
    assert saved.reason == "update" and saved.created == datetime(2026, 1, 2, 3, 4, 5)
    archive.restore(ctx, archive.files_in(saved.path))
    assert [tx.amount for tx in ctx.store.transactions.all()] == [42.0]


def test_saved_backups_ignore_foreign_folders(ctx):
    root = ctx.store.data_dir / backup.BACKUP_DIR
    (root / "my-own-copy").mkdir(parents=True)
    (root / "2026-13-99_999999_before-restore").mkdir()  # matches the pattern, but isn't a date
    assert archive.saved_backups(ctx) == []
    empty = root / "2026-01-01_000000_before-restore"
    empty.mkdir()
    with pytest.raises(ValidationError, match="no records"):
        archive.files_in(empty)


def test_prune_keeps_the_newest_backups_of_both_kinds(tmp_path):
    for i in range(12):
        (tmp_path / f"2026-01-{i + 1:02d}_000000_before-restore").mkdir()
    (tmp_path / "2026-02-01_000000_0.1.0_to_0.2.0").mkdir()
    (tmp_path / "keep-me").mkdir()
    backup.prune(tmp_path)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) == backup.KEEP + 1 and "keep-me" in left
    assert "2026-02-01_000000_0.1.0_to_0.2.0" in left and "2026-01-01_000000_before-restore" not in left


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not a zip", "not a ZIP file"),
        (zip_of({"accounts.csv": "x"}), "file list is missing"),
        (zip_of({"accounts.csv": "x"}, {"format": "something else"}), "file list is missing"),
        (zip_of({"accounts.csv": "x"}, {"format": archive.FORMAT, "version": 99}), "newer version"),
        (zip_of({"../evil.csv": "x"}, MANIFEST_OK), "unexpected file: ../evil.csv"),
        (zip_of({"sub/accounts.csv": "x"}, MANIFEST_OK), "unexpected file"),
        (zip_of({".lock": "x"}, MANIFEST_OK), "unexpected file"),
        (zip_of({"run.sh": "x"}, MANIFEST_OK), "unexpected file"),
        (zip_of({"settings.json": "{}"}, MANIFEST_OK), "no records"),
    ],
)
def test_read_refuses_what_is_not_a_backup(data, message):
    with pytest.raises(ValidationError, match=message):
        archive.read(data)


def test_read_refuses_a_broken_manifest_and_oversized_archives(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(archive.MANIFEST, "{not json")
    with pytest.raises(ValidationError, match="file list is missing"):
        archive.read(buf.getvalue())
    monkeypatch.setattr(archive, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValidationError, match="too large"):
        archive.read(zip_of({"accounts.csv": "x" * 100}, MANIFEST_OK))


def test_a_damaged_table_is_refused_before_anything_changes(ctx, setup):
    add_expense(ctx, setup)
    files = archive.read(archive.create(ctx))
    files["transactions.csv"] = files["transactions.csv"].replace(b"2026-09-10", b"not-a-date")
    with pytest.raises(ValidationError, match="can’t be read"):
        archive.restore(ctx, files)
    assert len(ctx.store.transactions.all()) == 1
    assert archive.saved_backups(ctx) == []  # nothing was touched, so nothing was copied


def test_backup_file_name():
    assert archive.file_name(date(2026, 10, 19)) == "Budget-backup-2026-10-19.zip"


def test_everything_json_holds_every_table(ctx, setup):
    add_expense(ctx, setup)
    doc = json.loads(export_svc.everything_json(ctx))
    assert (doc["format"], doc["version"], doc["base_currency"]) == ("budget-app/export", 1, "CHF")
    assert {a["name"] for a in doc["accounts"]} == {"Bank", "Euro account"}
    assert doc["transactions"][0]["date"] == "2026-09-10" and doc["transactions"][0]["amount"] == 42.0
    assert doc["income_categories"][0]["name"] == "Salary"
    for key in ("transaction_items", "account_entries", "budget_entries", "settings"):
        assert key in doc


def test_view_frame_follows_the_columns_and_adds_the_currency():
    columns = [
        {"field": "date", "headerName": "Date"},
        {"field": "amount", "headerName": "Amount"},
        {"field": "comment", "headerName": "Note"},
    ]
    rows = [{"id": "x", "_type": "income", "date": "2026-09-10", "amount": 5.0, "currency": "EUR", "comment": "hi"}]
    df = export_svc.view_frame(columns, rows, currency_header="Currency")
    assert list(df.columns) == ["Date", "Amount", "Currency", "Note"]
    assert df.iloc[0].tolist() == ["2026-09-10", 5.0, "EUR", "hi"]
    assert list(export_svc.view_frame(columns, rows).columns) == ["Date", "Amount", "Note"]
