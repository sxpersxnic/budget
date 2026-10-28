"""The app lock (F27): the lock page, the session cookie, and the Security group in Settings."""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app.services import security
from budget_app.storage import encryption, vault

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(vault, "SCRYPT_N", 2**8)


async def settle(rounds: int = 10) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0.02)


def top_dialog(user: User) -> ui.dialog:
    with user:
        dialogs = [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]
    assert dialogs, "expected an open sheet"
    return max(dialogs, key=lambda d: d.id)


def field(user: User, label: str, visible_only: bool = True) -> ui.input:
    """An input by label, in the open sheet if there is one, else on the page."""
    with user:
        scope = (
            top_dialog(user).descendants()
            if any(isinstance(e, ui.dialog) and e.value for e in user.client.elements.values())
            else user.client.elements.values()
        )
        found = [
            e
            for e in scope
            if isinstance(e, ui.input) and e.props.get("label") == label and (e.visible or not visible_only)
        ]
    assert found, f"no input {label!r}"
    return found[-1]


def set_value(user: User, element: ui.input, value: str) -> None:
    with user:
        element.set_value(value)  # typing would append to what is there


def button(user: User, text: str) -> UserInteraction:
    with user:
        found = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == text and e.visible]
    assert found, f"no button {text!r}"
    return UserInteraction(user, {found[-1]}, text)


async def open_unlocked(user: User, path: str = "/") -> None:
    """Pass the lock the way the lock page does, then open `path`."""
    nonce = await security.unlock(PW, path)
    await user.open(f"/unlock/{nonce}")


# ------------------------------------------------------------------ the lock page
async def test_without_a_password_the_lock_page_sends_home(user: User) -> None:
    await user.open("/lock")
    await user.should_see("Overview")


async def test_pages_are_locked_until_the_password_is_typed(user: User, ctx) -> None:
    security.set_password(PW, PW)
    await user.open("/budget?month=2026-09")
    await user.should_see("Enter your password to open your records.")
    with user:
        assert user.client.page.path == "/lock"
    set_value(user, field(user, "Password"), "wrong one")
    button(user, "Unlock").click()
    await settle()
    assert user.notify.contains("Wrong password")
    set_value(user, field(user, "Password"), PW)
    button(user, "Unlock").click()
    await settle(30)
    await user.should_see("Budget")  # the page asked for, with the session cookie set on the way
    with user:
        assert user.client.page.path == "/budget"
        assert user.client.request.query_params["month"] == "2026-09"  # the query came along
    await user.open("/transactions")  # the cookie holds for every page
    await user.should_see("Transactions")
    assert len(security.state().sessions) == 1


async def test_lock_now_locks_every_page_again(user: User, ctx) -> None:
    security.set_password(PW, PW)
    await open_unlocked(user, "/settings")
    await user.should_see("On. The app asks for the password before it opens.")
    button(user, "Lock Now").click()
    await settle(20)
    await user.should_see("Enter your password to open your records.")
    assert security.state().sessions == {}
    await user.open("/")
    await user.should_see("Enter your password to open your records.")


async def test_forgotten_password_without_encryption_explains_the_way_out(user: User, ctx) -> None:
    security.set_password(PW, PW)
    await user.open("/")
    button(user, "Forgot your password?").click()
    await user.should_see("delete the file security.json from the data folder")


async def test_the_recovery_key_sets_a_new_password(user: User, ctx) -> None:
    security.set_password(PW, PW)
    recovery = security.enable_encryption(PW)
    assert encryption.is_encrypted(ctx.store.data_dir / "settings.json")
    await user.open("/")
    button(user, "Forgot your password?").click()
    await user.should_see("Type the recovery key you were given")
    set_value(user, field(user, "Recovery key"), "AAAA-" * 8)
    set_value(user, field(user, "New password"), "brand new password")
    set_value(user, field(user, "Repeat the new password"), "brand new password")
    button(user, "Set New Password").click()
    await settle()
    assert user.notify.contains("This is not the recovery key of these records")
    set_value(user, field(user, "Repeat the new password"), "other")
    button(user, "Set New Password").click()
    await settle()
    assert user.notify.contains("The passwords differ")
    set_value(user, field(user, "Recovery key"), recovery.lower())
    set_value(user, field(user, "Repeat the new password"), "brand new password")
    button(user, "Set New Password").click()
    await settle(30)
    await user.should_see("Overview")
    assert vault.Vault.load(ctx.store.data_dir).unlock("brand new password") == ctx.store.codec.key


# ------------------------------------------------------------------ settings
async def test_setting_a_password_turns_the_lock_on_for_this_browser(user: User, ctx) -> None:
    await user.open("/settings")
    await user.should_see("Asks for a password before the app opens.")
    button(user, "Set Password…").click()
    await user.should_see("At least 8 characters")
    set_value(user, field(user, "New password"), PW)
    set_value(user, field(user, "Repeat the password"), "something else")
    button(user, "Set Password").click()
    await settle()
    assert user.notify.contains("The passwords differ")
    set_value(user, field(user, "Repeat the password"), PW)
    button(user, "Set Password").click()
    await settle(30)
    assert security.required()
    await user.should_see("On. The app asks for the password before it opens.")  # back in Settings, with a session
    with user:
        assert user.client.page.path == "/settings"
    assert (ctx.store.data_dir / vault.FILE_NAME).exists()
    found = [b.text for b in user.find(kind=ui.button).elements if b.text in ("Change…", "Turn Off…", "Turn On…")]
    assert sorted(found) == ["Change…", "Turn Off…", "Turn On…"]


async def test_changing_and_turning_the_lock_off(user: User, ctx) -> None:
    security.set_password(PW, PW)
    await open_unlocked(user, "/settings")
    button(user, "Change…").click()
    set_value(user, field(user, "Current password"), "wrong")
    set_value(user, field(user, "New password"), "another password")
    set_value(user, field(user, "Repeat the password"), "another password")
    button(user, "Change Password").click()
    await settle()
    assert user.notify.contains("Wrong password")
    set_value(user, field(user, "Current password"), PW)
    button(user, "Change Password").click()
    await settle()
    assert user.notify.contains("Password changed")
    vault.Vault.load(ctx.store.data_dir).verify("another password")
    button(user, "Turn Off…").click()
    await user.should_see("The app opens without a password again.")
    set_value(user, field(user, "Password"), "another password")
    button(user, "Turn the Lock Off").click()
    await settle()
    assert user.notify.contains("The lock is off")
    assert not security.required()
    await user.should_see("Asks for a password before the app opens.")


async def test_encryption_shows_the_recovery_key_once_and_can_be_turned_off(user: User, ctx) -> None:
    security.set_password(PW, PW)
    await open_unlocked(user, "/settings")
    await user.should_see("Attachment files stay as they are.")
    button(user, "Turn On…").click()
    await user.should_see("The record files are rewritten")
    set_value(user, field(user, "Password"), PW)
    button(user, "Encrypt Records").click()
    await settle()
    await user.should_see("Write this recovery key down")
    assert security.encrypted() and encryption.is_encrypted(ctx.store.data_dir / "accounts.csv")
    with user:
        [key_label] = [
            e for e in top_dialog(user).descendants() if isinstance(e, ui.label) and "recovery-key" in e.classes
        ]
        [done] = [e for e in top_dialog(user).descendants() if isinstance(e, ui.button) and e.text == "Done"]
        [noted] = [e for e in top_dialog(user).descendants() if isinstance(e, ui.checkbox)]
    assert vault.Vault.load(ctx.store.data_dir).unlock_with_recovery(key_label.text) == ctx.store.codec.key
    assert not done.enabled
    noted.value = True
    await settle()
    assert done.enabled
    button(user, "Done").click()
    await settle()
    await user.should_see("a backup from Back Up Now is written in plain text.")
    # a new recovery key retires the old one
    button(user, "New Recovery Key…").click()
    set_value(user, field(user, "Password"), PW)
    button(user, "New Recovery Key").click()
    await settle()
    await user.should_see("Your New Recovery Key")
    with user:
        assert len([e for e in top_dialog(user).descendants() if isinstance(e, ui.checkbox)]) == 1
    # off again
    with user:
        [done] = [e for e in top_dialog(user).descendants() if isinstance(e, ui.button) and e.text == "Done"]
        [noted] = [e for e in top_dialog(user).descendants() if isinstance(e, ui.checkbox)]
    noted.value = True
    await settle()
    button(user, "Done").click()
    await settle()
    with user:
        turn_off = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Turn Off…"]
    assert len(turn_off) == 2  # the lock's and the encryption's
    UserInteraction(user, {turn_off[-1]}, "Turn Off…").click()
    await user.should_see("The record files are written as plain CSV again.")
    set_value(user, field(user, "Password"), PW)
    button(user, "Turn Encryption Off").click()
    await settle()
    assert user.notify.contains("The records are plain files again")
    assert not security.encrypted() and not encryption.is_encrypted(ctx.store.data_dir / "accounts.csv")
