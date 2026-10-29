"""Several households or profiles (F34): the registry, its rules, the folder lock and the switch."""

import json

import pytest

from budget_app import config, i18n
from budget_app import main as main_module
from budget_app.domain.models import Account
from budget_app.services import context as context_module
from budget_app.services import profiles, security
from budget_app.services.context import ValidationError
from budget_app.storage import lock
from budget_app.storage import profiles as registry
from budget_app.storage import vault as vault_module
from budget_app.storage.store import Store
from budget_app.storage.vault import Vault


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("BUDGET_DATA_DIR", str(h))
    monkeypatch.setattr(profiles, "opener", None)
    return h


# ------------------------------------------------------------------ the registry
def test_without_a_registry_the_home_folder_is_the_one_profile(home):
    reg = profiles.load()
    assert [(p.name, p.path, p.id) for p in reg.profiles] == [("Budget", str(home), "default")]
    assert reg.active.id == reg.current == "default" and profiles.active().folder == home
    assert config.home_dir() == home and config.data_dir() == home
    assert not (home / registry.REGISTRY).exists()  # nothing is written until something changes


def test_the_data_folder_is_the_current_profiles(home, tmp_path):
    other = tmp_path / "elsewhere"
    registry.save(
        registry.Registry("b", [registry.Profile("A", str(home), "a"), registry.Profile("B", str(other), "b")]), home
    )
    assert config.data_dir() == other and other.is_dir()  # created when missing
    assert json.loads((home / registry.REGISTRY).read_text())["current"] == "b"


def test_a_damaged_registry_falls_back_to_the_home_folder(home):
    path = home / registry.REGISTRY
    path.write_text("{not json")
    assert profiles.active().path == str(home)
    path.write_text('{"current": "x", "profiles": [{"name": "A", "path": "/a", "id": "a"}]}')
    assert profiles.active().id == "default"  # the current id is unknown
    path.write_text('{"current": "a", "profiles": []}')
    assert profiles.active().id == "default"
    path.write_text('{"current": "a", "profiles": [{"name": "A"}]}')
    assert profiles.active().id == "default"
    reg = registry.default(home)
    reg.current = "gone"
    with pytest.raises(KeyError):
        reg.active  # noqa: B018


def test_add_rename_switch_and_remove(home):
    second = profiles.add("  Joint   household ")
    assert second.name == "Joint household" and second.folder == home.resolve() / "profiles" / "Joint-household"
    assert second.folder.is_dir()
    reg = profiles.load()
    assert [p.name for p in reg.profiles] == ["Budget", "Joint household"] and reg.current == "default"
    with pytest.raises(ValidationError, match="already exists"):
        profiles.add("joint HOUSEHOLD")
    with pytest.raises(ValidationError, match="needs a name"):
        profiles.add("   ")
    with pytest.raises(ValidationError, match="belongs to another profile"):
        profiles.add("Club", str(second.folder))
    with pytest.raises(ValidationError, match="of its own"):
        profiles.add("Club", str(home / "attachments"))
    with pytest.raises(ValidationError, match="belongs to another profile"):
        profiles.add("Club", str(home))  # the first profile's
    with pytest.raises(ValidationError, match="of its own"):
        profiles.add("Club", str(home.parent))
    (home.parent / "a-file").write_text("x")
    with pytest.raises(ValidationError, match="not a folder"):
        profiles.add("Club", str(home.parent / "a-file"))
    assert profiles.add("§§§").folder.name == "profile"  # a name without letters still gets a folder
    renamed = profiles.rename(second.id, "Household")
    assert renamed.name == "Household" and profiles.get(profiles.load(), second.id).name == "Household"
    assert profiles.rename(second.id, "household").name == "household"  # its own name in other letters
    with pytest.raises(ValidationError, match="already exists"):
        profiles.rename(second.id, "budget")
    with pytest.raises(ValidationError, match="no such profile"):
        profiles.rename("nope", "x")
    with pytest.raises(ValidationError, match="Open another profile"):
        profiles.remove("default")
    opened = []
    profiles.opener = lambda folder, moved: opened.append((folder, moved))
    assert profiles.switch(second.id).id == second.id
    assert opened == [(second.folder, False)] and profiles.load().current == second.id
    assert config.data_dir() == second.folder
    profiles.switch(second.id)  # already current: nothing happens
    assert len(opened) == 1
    profiles.remove("default")
    assert profiles.load().profiles[0].id == second.id
    assert home.is_dir()  # the folder and its files stay


def test_a_profile_may_use_an_existing_folder_elsewhere(home, tmp_path):
    elsewhere = tmp_path / "cloud" / "Budget"
    elsewhere.mkdir(parents=True)
    Store(elsewhere).accounts.add(Account(name="Bank", currency="CHF"))
    p = profiles.add("Cloud", f"  {elsewhere}  ")
    assert p.folder == elsewhere.resolve()
    assert Store(p.folder).accounts.all()[0].name == "Bank"


def test_folders_that_cannot_be_created_are_reported(home, tmp_path):
    (tmp_path / "file").write_text("x")
    with pytest.raises(ValidationError, match="can’t be created"):
        profiles.add("Below a file", str(tmp_path / "file" / "inside"))
    second = profiles.add("Other")
    second.folder.rmdir()
    second.folder.write_text("now a file")  # replaced behind the app's back
    with pytest.raises(ValidationError, match="can’t be created"):
        profiles.switch(second.id)


def test_a_refused_switch_leaves_the_registry_as_it_was(home):
    second = profiles.add("Other")

    def refuse(folder, moved):
        raise ValidationError("open in another window")

    profiles.opener = refuse
    with pytest.raises(ValidationError, match="another window"):
        profiles.switch(second.id)
    assert profiles.load().current == "default"


# ------------------------------------------------------------------ the folder lock
def test_the_lock_moves_to_the_other_folder(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(lock, "_handle", None)
    lock.switch(a)  # nothing held yet
    held = lock._handle
    lock.switch(b)
    assert held is not None and held.closed and (b / lock.LOCK_FILE).read_text().isdigit()
    other = lock._take(a)  # a is free again
    other.close()
    monkeypatch.setattr(lock, "_take", lambda folder: (_ for _ in ()).throw(lock.AlreadyRunningError("busy")))
    with pytest.raises(lock.AlreadyRunningError):
        lock.switch(a)
    assert not lock._handle.closed  # b is still held
    lock._handle.close()


# ------------------------------------------------------------------ opening another folder
@pytest.fixture
def opened(monkeypatch, tmp_path):
    """main.open_folder with the folder lock and the refresh replaced by recorders."""
    calls: dict = {"lock": [], "refresh": 0}
    monkeypatch.setattr(main_module.instance_lock, "switch", calls["lock"].append)
    monkeypatch.setattr(
        main_module.background_tasks,
        "create",
        lambda coro, name: (coro.close(), calls.__setitem__("refresh", calls["refresh"] + 1)),
    )
    monkeypatch.setattr(context_module, "_ctx", None)
    yield calls
    context_module._ctx = None
    i18n.set_language("en")


def test_opening_a_plain_folder_opens_its_store(opened, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    s = Store(b)
    s.settings.language = "fr"
    s.save_settings()
    security.configure(a)
    main_module.open_folder(b)
    assert opened["lock"] == [b] and opened["refresh"] == 1
    assert context_module.get_ctx().store.data_dir == b and security.state().data_dir == b


def test_opening_a_locked_folder_waits_for_its_password(opened, tmp_path, monkeypatch):
    monkeypatch.setattr(vault_module, "SCRYPT_N", 2**8)
    a, b = tmp_path / "a", tmp_path / "b"
    b.mkdir()
    vault = Vault.load(b)
    vault.set_password("long enough pw")
    vault.enable_encryption("long enough pw")
    security.configure(a)
    main_module.open_folder(b)
    assert not context_module.is_open() and security.encrypted() and opened["refresh"] == 0


def test_a_folder_open_in_another_window_is_refused(monkeypatch, tmp_path):
    security.configure(tmp_path)
    monkeypatch.setattr(
        main_module.instance_lock, "switch", lambda folder: (_ for _ in ()).throw(lock.AlreadyRunningError("busy"))
    )
    with pytest.raises(ValidationError, match="another Budget window"):
        main_module.open_folder(tmp_path / "b")
    assert security.state().data_dir == tmp_path
