"""Sync through a cloud folder (F26): moving a profile's folder, the claim files and the change detection."""

from datetime import datetime, timedelta

import pytest

from budget_app.domain.models import Account
from budget_app.services import context as context_module
from budget_app.services import profiles, sync
from budget_app.services.context import Context, ValidationError
from budget_app.storage import claim, encryption, lock, watch
from budget_app.storage import profiles as registry
from budget_app.storage.store import Store

T0 = datetime(2026, 10, 28, 9, 0)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("BUDGET_DATA_DIR", str(h))
    monkeypatch.setattr(profiles, "opener", None)
    return h


@pytest.fixture
def opened(monkeypatch) -> list:
    folders: list = []
    monkeypatch.setattr(profiles, "opener", lambda folder, moved: folders.append((folder, moved)))
    return folders


@pytest.fixture
def running(tmp_path, rates, monkeypatch):
    """An open folder whose claim and changes are watched, as on this computer."""
    folder = tmp_path / "cloud"
    ctx = Context(store=Store(folder), _fx=rates)
    ctx.store.accounts.add(Account(name="Bank", currency="CHF"))
    monkeypatch.setattr(claim, "host_name", lambda: "mac")
    monkeypatch.setattr(context_module, "_ctx", ctx)
    sync.start(folder, T0)
    yield ctx
    sync.stop()


# ------------------------------------------------------------------ moving a folder
def test_move_copies_everything_but_the_running_apps_files(home, tmp_path, opened):
    store = Store(home)
    store.accounts.add(Account(name="Bank", currency="CHF"))
    (home / "attachments" / "t1").mkdir(parents=True)
    (home / "attachments" / "t1" / "a1.pdf").write_bytes(b"%PDF")
    (home / "backups" / "2026-10-01_090000_before-restore").mkdir(parents=True)
    (home / "backups" / "2026-10-01_090000_before-restore" / "accounts.csv").write_text("x")
    (home / "security.json").write_text("{}")
    (home / lock.LOCK_FILE).write_text("123")
    claim.write(home, "mac", T0)
    (home / "accounts.csv.tmp").write_text("half written")
    other = profiles.add("Other")  # writes the registry into the home folder
    target = tmp_path / "iCloud" / "Budget"
    with pytest.raises(ValidationError, match="Type the folder"):
        profiles.move("default", "  ")
    with pytest.raises(ValidationError, match="outside the current one"):
        profiles.move("default", str(home / "deeper" / "x"))
    with pytest.raises(ValidationError, match="of its own"):
        profiles.move("default", str(home))  # the app's own folder
    with pytest.raises(ValidationError, match="belongs to another profile"):
        profiles.move("default", str(other.folder))
    moved = profiles.move("default", str(target))
    assert moved.folder == target.resolve() and opened == [(target.resolve(), True)]
    assert profiles.active().path == str(target.resolve())
    copied = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
    assert "accounts.csv" in copied and "settings.json" in copied and "history.jsonl" in copied
    assert "attachments/t1/a1.pdf" in copied and "security.json" in copied
    assert "backups/2026-10-01_090000_before-restore/accounts.csv" in copied
    assert not [n for n in copied if n in (lock.LOCK_FILE, registry.REGISTRY) or n.endswith(".tmp")]
    assert not [n for n in copied if claim.is_claim_file(n)]
    assert Store(target).accounts.all()[0].name == "Bank"
    assert (home / "accounts.csv").exists()  # the old folder is left as it was
    with pytest.raises(ValidationError, match="belongs to another profile"):
        profiles.move(other.id, str(target))
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(ValidationError, match="not empty"):
        profiles.move(other.id, str(tmp_path / "full"))


def test_moving_another_profile_does_not_reopen(home, tmp_path, opened):
    other = profiles.add("Other")
    profiles.move(other.id, str(tmp_path / "elsewhere"))
    assert opened == [] and profiles.get(profiles.load(), other.id).folder == (tmp_path / "elsewhere").resolve()


def test_a_move_that_cannot_copy_leaves_the_profile_where_it_was(home, tmp_path, monkeypatch):
    Store(home).accounts.add(Account(name="Bank", currency="CHF"))

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(profiles.shutil, "copy2", broken)
    with pytest.raises(ValidationError, match="disk full"):
        profiles.move("default", str(tmp_path / "full"))
    assert profiles.active().folder == home


# ------------------------------------------------------------------ claims
def test_claims_are_written_refreshed_and_released(tmp_path):
    assert claim.others(tmp_path / "missing", "mac") == []
    mac = claim.write(tmp_path, "mac", T0, now=T0)
    assert claim.read(claim.path_for(tmp_path, "mac")) == mac and mac.since_at == T0 and mac.seen_at == T0
    assert claim.others(tmp_path, "mac", now=T0) == []  # our own
    assert claim.others(tmp_path, "pc", now=T0 + timedelta(minutes=5)) == [mac]  # fresh: in use there
    assert claim.others(tmp_path, "pc", now=T0 + timedelta(minutes=11)) == []  # stale: abandoned
    pc = claim.write(tmp_path, "pc", T0, now=T0 + timedelta(minutes=2))
    assert claim.others(tmp_path, "nas", now=T0 + timedelta(minutes=3)) == [pc, mac]  # the latest first
    claim.release(tmp_path, "mac")
    assert claim.others(tmp_path, "pc", now=T0) == []
    (tmp_path / ".in-use.broken.json").write_text("not json")
    (tmp_path / ".in-use.odd.json").write_text('{"host": "odd", "pid": 1, "since": "x", "seen": "x"}')
    assert claim.others(tmp_path, "mac", now=T0) == [pc]  # unreadable claims don't count
    assert claim.path_for(tmp_path, "Anna’s MacBook Pro").name == ".in-use.Anna-s-MacBook-Pro.json"
    assert claim.path_for(tmp_path, "…").name == ".in-use.host.json"
    assert claim.host_name()


def test_the_claim_follows_the_open_folder(running, tmp_path):
    folder = running.store.data_dir
    assert claim.read(claim.path_for(folder, "mac")).since_at == T0
    claim.write(folder, "pc", T0, now=datetime.now())
    assert [c.host for c in sync.others()] == ["pc"]
    sync.tick(T0 + timedelta(seconds=30))  # no heartbeat yet
    assert claim.read(claim.path_for(folder, "mac")).seen_at == T0
    sync.tick(T0 + timedelta(seconds=61))
    assert claim.read(claim.path_for(folder, "mac")).seen_at == T0 + timedelta(seconds=61)
    sync.stop()
    assert not claim.path_for(folder, "mac").exists() and sync.others() == [] and sync.tick() == []


def test_a_read_only_folder_still_opens(running, monkeypatch, caplog):
    def refuse(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(claim, "write", refuse)
    sync.start(running.store.data_dir, T0)
    sync.tick(T0 + timedelta(minutes=2))
    assert caplog.text.count("Can't") == 2


# ------------------------------------------------------------------ changes made elsewhere
def elsewhere(folder, name: str) -> None:
    """The other computer's app adds an account; the sync client copies the files here (this app saw nothing)."""
    seen = dict(watch._seen)
    Store(folder).accounts.add(Account(name=name, currency="CHF"))
    watch._seen.clear()
    watch._seen.update(seen)


def test_own_writes_are_not_changes(running):
    running.store.accounts.add(Account(name="Cash", currency="CHF"))
    running.store.save_settings()
    assert sync.tick() == [] and watch.changed(encryption.record_files(running.store.data_dir)) == []


def test_files_changed_elsewhere_are_reloaded_once_they_settle(running):
    folder = running.store.data_dir
    elsewhere(folder, "Savings")
    old_table = running.store.accounts
    assert sync.tick() == []  # seen once: maybe still arriving
    assert [a.name for a in running.store.accounts.all()] == ["Bank"]
    assert sync.tick() == [folder / "accounts.csv", folder / "history.jsonl"]
    assert [a.name for a in running.store.accounts.all()] == ["Bank", "Savings"]
    assert running.store.accounts is not old_table  # the same Store object, read again
    assert sync.tick() == []


def test_a_change_still_arriving_is_waited_for(running):
    folder = running.store.data_dir
    elsewhere(folder, "One")
    sync.tick()
    elsewhere(folder, "Two")  # more arrived meanwhile
    assert sync.tick() == []
    assert len(sync.tick()) == 2 and len(running.store.accounts.all()) == 3


def test_an_unreadable_change_is_skipped_until_it_changes_again(running, caplog):
    folder = running.store.data_dir
    (folder / "accounts.csv").write_bytes(encryption.MAGIC + b"half a token")
    sync.tick()
    assert sync.tick() == [] and "can't be read yet" in caplog.text
    assert [a.name for a in running.store.accounts.all()] == ["Bank"]
    assert sync.tick() == []  # not tried again


def test_nothing_is_reloaded_while_the_folder_waits_for_its_password(running, monkeypatch):
    elsewhere(running.store.data_dir, "Savings")
    monkeypatch.setattr(context_module, "_ctx", None)
    assert sync.tick() == [] and sync.tick() == []


# ------------------------------------------------------------------ cloud folders
def test_cloud_folders_are_suggested_where_they_exist(tmp_path):
    assert sync.cloud_folders(tmp_path) == []
    icloud = tmp_path / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    icloud.mkdir(parents=True)
    (tmp_path / "Library" / "CloudStorage" / "OneDrive-Personal").mkdir(parents=True)
    (tmp_path / "Library" / "CloudStorage" / "GoogleDrive-anna@example.com").mkdir(parents=True)
    (tmp_path / "Dropbox").mkdir()
    names = [name for name, _ in sync.cloud_folders(tmp_path)]
    assert names == ["iCloud Drive", "Google Drive", "OneDrive", "Dropbox"]


def test_a_file_that_went_away_is_a_change(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_text("id\n")
    watch.note(path)
    assert watch.changed([path]) == []
    path.unlink()
    assert watch.signature(path) is None and watch.changed([path]) == [path]
    watch.note(path)  # seen as gone
    assert watch.changed([path]) == []
