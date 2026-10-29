"""Sync through a cloud folder (F26): Settings → Data → Move…, the warning while another computer has the folder
open, and the notice after records changed elsewhere were read again."""

from datetime import datetime

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from test_lock_page import button, field, set_value, settle

from budget_app.services import profiles, sync
from budget_app.storage import claim
from budget_app.ui import layout


@pytest.fixture
def moved(monkeypatch) -> list:
    folders: list = []
    monkeypatch.setattr(profiles, "opener", lambda folder, moved: folders.append((folder, moved)))
    return folders


async def test_the_records_move_to_a_cloud_folder(user: User, ctx, moved, tmp_path_factory, monkeypatch) -> None:
    icloud = tmp_path_factory.mktemp("iCloud")
    monkeypatch.setattr(sync, "cloud_folders", lambda: [("iCloud Drive", icloud)])
    await user.open("/settings")
    await user.should_see(str(ctx.store.data_dir))
    button(user, "Move…").click()
    await user.should_see("one at a time: there, add the folder under Profiles")
    button(user, "Move").click()
    await settle()
    assert user.notify.contains("Type the folder to move the records to")
    with user:
        [chip] = [e for e in user.client.elements.values() if isinstance(e, ui.chip)]
    UserInteraction(user, {chip}, "iCloud Drive").click()
    assert field(user, "New folder").value == str(icloud / "Budget")
    button(user, "Move").click()
    await settle(20)
    target = (icloud / "Budget").resolve()
    assert moved == [(target, True)] and profiles.active().folder == target
    assert user.notify.contains(f"The records are now in {target}")
    assert (target / "accounts.csv").exists()


async def test_without_cloud_folders_the_path_is_typed(user: User, moved, tmp_path_factory, monkeypatch) -> None:
    monkeypatch.setattr(sync, "cloud_folders", list)
    await user.open("/settings")
    button(user, "Move…").click()
    with user:
        assert not [e for e in user.client.elements.values() if isinstance(e, ui.chip)]
    set_value(user, field(user, "New folder"), str(tmp_path_factory.mktemp("cloud") / "Budget"))
    button(user, "Move").click()
    await settle(20)
    assert moved and moved[0][1] is True


async def test_another_computer_with_the_folder_open_is_shown(user: User, ctx, monkeypatch) -> None:
    monkeypatch.setattr(claim, "host_name", lambda: "mac")
    folder = ctx.store.data_dir
    sync.start(folder)
    try:
        claim.write(folder, "Office PC", datetime(2026, 10, 28, 8, 30), datetime.now())
        await user.open("/")
        await user.should_see("Budget is open on Office PC too, since 28 Oct 2026, 08:30.")
    finally:
        sync.stop()
    await user.open("/")
    await user.should_not_see("Budget is open on")


async def test_open_pages_are_told_when_records_were_read_again(user: User, setup) -> None:
    await user.open("/transactions")
    refreshed = []
    with user:
        layout._data_listeners[user.client] = lambda: refreshed.append(True)
    layout.records_reloaded()
    assert user.notify.contains("Records changed on another computer were read again.")
    assert refreshed == [True]
