"""Update check (F33) in the pages: the switch and status in Settings, the sidebar link, nothing on by default."""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User
from notifications import notified

from budget_app import i18n
from budget_app.services import updates as updates_svc
from budget_app.services.updates import UpdateError
from budget_app.storage.store import Store


@pytest.fixture(autouse=True)
def english():
    yield
    i18n.set_language("en")


@pytest.fixture
def fake_github(monkeypatch):
    """`fetch_latest` answers v0.2.0 without a network, and the running app is 0.1.0; records the calls."""
    calls: list[str] = []

    def fetch(url=None, transport=None):
        calls.append(url or "latest")
        if calls[-1] == "fail":
            raise UpdateError("Could not reach the releases page (no network)")
        return "v0.2.0"

    monkeypatch.setattr(updates_svc, "fetch_latest", fetch)
    monkeypatch.setattr(updates_svc, "app_version", lambda: "0.1.0")
    return calls


def switch(user: User) -> ui.switch:
    found = user.find(marker="update-check").elements
    assert len(found) == 1
    return found.pop()


def links(user: User) -> list[ui.link]:
    with user:
        return [e for e in user.client.elements.values() if isinstance(e, ui.link)]


async def until(check, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def test_off_by_default_and_the_privacy_note(user: User, ctx, fake_github) -> None:
    await user.open("/settings")
    await user.should_see("Check for Updates")
    await user.should_see("Nothing is downloaded or installed")
    await user.should_see("Off: the app never asks on its own. The releases page lists the installers.")
    assert switch(user).value is False
    assert fake_github == []
    await user.should_not_see("Check Now")
    assert any(lk.text == "Releases page" for lk in links(user))
    assert not any("is available" in lk.text for lk in links(user))


async def test_turning_it_on_checks_at_once_and_the_sidebar_links_the_release(
    user: User, ctx, tmp_path, fake_github
) -> None:
    await user.open("/settings")
    with user:
        switch(user).value = True
    await until(lambda: fake_github == ["latest"])
    await user.should_see("the newest release is 0.2.0")
    await user.should_see("Budget 0.2.0 is available")
    assert Store(tmp_path).settings.update_check is True
    assert ctx.store.settings.update_latest == "v0.2.0"
    link = next(lk for lk in links(user) if lk.text == "Budget 0.2.0 is available")
    assert link.props["href"].endswith("/releases/tag/v0.2.0") and link.props.get("target") == "_blank"
    # the sidebar of every page shows it
    await user.open("/transactions")
    await user.should_see("Budget 0.2.0 is available")
    # Check Now asks again, the same day
    await user.open("/settings")
    user.find("Check Now").click()
    await until(lambda: len(fake_github) == 2)
    # off again: the result is forgotten, and the sidebar is quiet
    with user:
        switch(user).value = False
    await user.should_see("Off: the app never asks on its own. The releases page lists the installers.")
    assert (ctx.store.settings.update_check, ctx.store.settings.update_latest) == (False, "")
    await user.open("/")
    await user.should_not_see("is available")


async def test_a_current_app_shows_the_version_but_no_link(user: User, ctx, fake_github, monkeypatch) -> None:
    monkeypatch.setattr(updates_svc, "app_version", lambda: "0.2.0")
    ctx.store.settings.update_check = True
    await user.open("/settings")
    await user.should_see("Not checked yet.")
    user.find("Check Now").click()
    await user.should_see("the newest release is 0.2.0")
    await user.should_not_see("is available")


async def test_a_failed_check_now_is_reported(user: User, ctx, fake_github, monkeypatch) -> None:
    ctx.store.settings.update_check = True

    def fail(url=None, transport=None):
        raise UpdateError("Could not reach the releases page (no network)")

    monkeypatch.setattr(updates_svc, "fetch_latest", fail)
    await user.open("/settings")
    user.find("Check Now").click()
    await until(lambda: notified(user, "Update check failed: Could not reach the releases page (no network)"))
    await user.should_see("Not checked yet.")


async def test_a_failed_check_when_turning_on_is_reported_and_stays_on(user: User, ctx, monkeypatch) -> None:
    def fail(url=None, transport=None):
        raise UpdateError("Could not reach the releases page (no network)")

    monkeypatch.setattr(updates_svc, "fetch_latest", fail)
    await user.open("/settings")
    with user:
        switch(user).value = True
    await until(lambda: notified(user, "Update check failed: Could not reach the releases page (no network)"))
    assert ctx.store.settings.update_check is True  # the choice stays; the next refresh tries again
    await user.should_see("Not checked yet.")
