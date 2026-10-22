"""Updating the app must never lose data: files survive, unknown fields survive, a backup exists."""

import json
from datetime import date, datetime

from budget_app.domain.models import Account
from budget_app.storage import backup
from budget_app.storage.csv_store import CsvTable
from budget_app.storage.store import Store


def test_older_version_keeps_columns_written_by_a_newer_one(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_text(
        "name,currency,kind,created,id,iban\n"
        "Bank,CHF,regular,2026-01-02,a1,CH93 0076 2011 6238 5295 7\n"
        "Cash,CHF,regular,2026-01-02,a2,\n",
        encoding="utf-8",
    )
    table = CsvTable(path, Account)
    bank = table.get("a1")
    bank.name = "Bank (renamed)"
    table.update(bank)  # rewrites the file
    table.add(Account(name="New", currency="EUR", created=date(2026, 3, 1), id="a3"))
    table.delete(["a2"])

    text = path.read_text(encoding="utf-8")
    # columns this version adds (the asset class and conversion month, F21) get their defaults; the newer one is kept
    assert text.splitlines()[0] == "name,currency,kind,created,asset_class,invest_from,id,iban"
    assert "Bank (renamed),CHF,regular,2026-01-02,cash,,a1,CH93 0076 2011 6238 5295 7" in text
    assert "New,EUR,regular,2026-03-01,cash,,a3," in text
    assert "a2" not in text


def test_settings_keep_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"display_currency": "EUR", "theme": "dark"}), encoding="utf-8")
    store = Store(tmp_path)
    assert store.settings.display_currency == "EUR"
    store.settings.budget_horizon = "2027-12"
    store.save_settings()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["theme"] == "dark"
    assert saved["budget_horizon"] == "2027-12"


def test_first_start_of_a_new_version_backs_up_the_data(tmp_path):
    store = Store(tmp_path)
    store.accounts.add(Account(name="Bank", currency="CHF"))
    (tmp_path / "ecb").mkdir()
    (tmp_path / "ecb" / "eurofxref-hist.csv").write_text("Date,USD\n")

    # an install from before the marker existed counts as "earlier"
    first = backup.backup_on_version_change(tmp_path, "0.2.0", now=datetime(2026, 10, 18, 9, 30))
    assert first is not None
    assert first.name == "2026-10-18_093000_earlier_to_0.2.0"
    names = sorted(p.name for p in first.iterdir())
    assert "accounts.csv" in names and "settings.json" in names
    assert "eurofxref-hist.csv" not in names  # the ECB file can be downloaded again
    assert (first / "accounts.csv").read_text() == (tmp_path / "accounts.csv").read_text()

    # the same version starting again copies nothing; the next update does
    assert backup.backup_on_version_change(tmp_path, "0.2.0") is None
    second = backup.backup_on_version_change(tmp_path, "0.3.0", now=datetime(2026, 11, 1))
    assert second is not None
    assert second.name.endswith("_0.2.0_to_0.3.0")


def test_fresh_install_has_nothing_to_back_up(tmp_path):
    assert backup.backup_on_version_change(tmp_path, "0.2.0") is None
    assert (tmp_path / backup.MARKER).read_text() == "0.2.0"
    assert not (tmp_path / backup.BACKUP_DIR).exists()


def test_only_the_newest_backups_are_kept(tmp_path):
    (tmp_path / "accounts.csv").write_text("name\n")
    for i in range(backup.KEEP + 3):
        backup.backup_on_version_change(tmp_path, f"0.{i}.0", now=datetime(2026, 1, 1 + i))
    kept = sorted(p.name for p in (tmp_path / backup.BACKUP_DIR).iterdir())
    assert len(kept) == backup.KEEP
    assert kept[-1].endswith(f"_to_0.{backup.KEEP + 2}.0")
    assert (tmp_path / "accounts.csv").exists()  # pruning never touches the live data
