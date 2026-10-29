"""Profiles (F34): the Profiles group in Settings, the profile menu at the top of the sidebar and on the lock page."""

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from test_lock_page import PW, button, field, set_value, settle

from budget_app.services import context as context_module
from budget_app.services import profiles, security
from budget_app.services.context import Context, ValidationError
from budget_app.storage import vault
from budget_app.storage.store import Store


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(vault, "SCRYPT_N", 2**8)


@pytest.fixture
def opened(rates, monkeypatch) -> list:
    """The profile switch as main does it, without the folder lock: the other folder's security and store."""
    folders: list = []

    def opener(folder, moved):
        folders.append(folder)
        security.configure(folder)
        context_module._ctx = Context(store=Store(folder), _fx=rates)

    monkeypatch.setattr(profiles, "opener", opener)
    return folders


def labelled(user: User, label: str) -> UserInteraction:
    """An icon-only button by its accessible name (the last one on the page)."""
    with user:
        found = [
            e for e in user.client.elements.values() if isinstance(e, ui.button) and e.props.get("aria-label") == label
        ]
    assert found, f"no button labelled {label!r}"
    return UserInteraction(user, {found[-1]}, label)


def menu_item(user: User, text: str) -> UserInteraction:
    with user:
        found = [
            e
            for e in user.client.elements.values()
            if isinstance(e, ui.menu_item)
            and (getattr(e, "text", None) == text or any(getattr(d, "text", None) == text for d in e.descendants()))
        ]
    assert found, f"no menu item {text!r}"
    return UserInteraction(user, {found[-1]}, text)


async def test_one_profile_shows_the_apps_name(user: User) -> None:
    await user.open("/settings")
    await user.should_see("Profiles")
    await user.should_see("Separate budgets in separate folders")
    with user:
        assert not [
            e for e in user.client.elements.values() if isinstance(e, ui.button) and "profile-button" in e.classes
        ]


async def test_profiles_are_added_renamed_opened_and_removed(user: User, ctx, opened) -> None:
    await user.open("/settings")
    button(user, "Add Profile…").click()
    await user.should_see("A profile is a budget of its own")
    button(user, "Add").click()
    await settle()
    assert user.notify.contains("The profile needs a name")
    set_value(user, field(user, "Name"), "Club")
    button(user, "Add").click()
    await settle()
    assert user.notify.contains("Added Club")
    club = profiles.load().profiles[1]
    await user.should_see(club.path)
    labelled(user, "Rename").click()
    set_value(user, field(user, "Name"), "Sports club")
    button(user, "Save").click()
    await settle()
    await user.should_see("Sports club")
    button(user, "Open").click()
    await settle(20)
    assert opened == [club.folder] and profiles.active().id == club.id
    assert user.notify.contains("Opened Sports club")
    await user.should_see("Overview")
    with user:
        [brand] = [
            e for e in user.client.elements.values() if isinstance(e, ui.button) and "profile-button" in e.classes
        ]
    assert brand.text == "Sports club"
    # back to the first profile from the sidebar's menu
    menu_item(user, "Budget").click()
    await settle(20)
    assert opened == [club.folder, ctx.store.data_dir] and profiles.active().id == "default"
    await user.open("/settings")
    labelled(user, "Remove").click()
    await user.should_see("its folder and records stay where they are")
    button(user, "Remove").click()
    await settle()
    assert user.notify.contains("Removed Sports club")
    assert [p.id for p in profiles.load().profiles] == ["default"] and club.folder.is_dir()


async def test_a_refused_switch_says_why(user: User, monkeypatch) -> None:
    profiles.add("Club")

    def refuse(folder, moved):
        raise ValidationError("The folder is open in another Budget window")

    monkeypatch.setattr(profiles, "opener", refuse)
    await user.open("/settings")
    button(user, "Open").click()
    await settle()
    assert user.notify.contains("open in another Budget window")
    assert profiles.active().id == "default"


async def test_a_removal_that_fails_is_reported(user: User, monkeypatch) -> None:
    profiles.add("Club")
    await user.open("/settings")
    monkeypatch.setattr(profiles, "remove", lambda pid: (_ for _ in ()).throw(ValidationError("Not now")))
    labelled(user, "Remove").click()
    button(user, "Remove").click()
    await settle()
    assert user.notify.contains("Not now")


async def test_the_lock_page_names_the_profile_and_opens_another(user: User, ctx, opened) -> None:
    club = profiles.add("Club")
    security.set_password(PW, PW)
    await user.open("/")
    await user.should_see("Enter your password to open your records.")
    await user.should_see("Budget")
    menu_item(user, "Club").click()
    await settle(20)
    assert opened == [club.folder]
    await user.should_see("Overview")  # the club has no password
