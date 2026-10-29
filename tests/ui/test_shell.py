"""The window's shell (47): one sidebar per window, the pages swapped in its content area, behind the lock."""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from test_faster_entry_ui import key
from test_keyboard_ui import page_keys, sheet_open
from test_lock_page import PW, fast_kdf, open_unlocked  # noqa: F401 - fast_kdf: the fixture, for the password

from budget_app.domain import months
from budget_app.services import security
from budget_app.ui import layout

PREV = months.previous_month()


def drawers(user: User) -> list[ui.left_drawer]:
    with user:
        return [e for e in user.client.elements.values() if isinstance(e, ui.left_drawer)]


def nav(user: User, target: str) -> ui.link:
    [link] = [e for e in user.find(kind=ui.link).elements if "nav-item" in e.classes and e.props["href"] == target]
    return link


def active(user: User) -> list[str]:
    """The places marked open: highlighted and `aria-current` for screen readers, always together."""
    links = [e for e in user.find(kind=ui.link).elements if "nav-item" in e.classes]
    marked = [e.props["href"] for e in links if "active" in e.classes]
    assert marked == [e.props["href"] for e in links if e.props.get("aria-current") == "page"]
    return marked


def header_title(user: User) -> str:
    return layout._shells[user.client].title.text  # the phone header's


async def arrives(user: User, path: str) -> None:
    for _ in range(100):
        if user.client.sub_pages_router.current_path == path and user.back_history[-1] == path:
            await asyncio.sleep(0.02)  # the page is built in the same step
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"still on {user.client.sub_pages_router.current_path}, not {path}")


async def go(user: User, path: str) -> None:
    with user:
        ui.navigate.to(path)
    await arrives(user, path)


def month(user: User) -> str:
    [select] = [s for s in user.find(kind=ui.select).elements if s.props.get("aria-label") == "Month"]
    return select.value


# ------------------------------------------------------------------ one shell
async def test_the_shell_stays_and_follows_the_page(user: User) -> None:
    await user.open("/")
    [drawer] = drawers(user)
    client = user.client
    assert active(user) == ["/"] and client.title == "Overview – Budget" and header_title(user) == "Overview"

    UserInteraction(user, {nav(user, "/budget")}, None).click()  # a link in the sidebar
    await arrives(user, "/budget")
    await user.should_see("Compare with")
    assert user.client is client and drawers(user) == [drawer] and not drawer.is_deleted  # no page load
    assert active(user) == ["/budget"] and client.title == "Budget – Budget" and header_title(user) == "Budget"

    await go(user, "/goals")  # a tab of Plan: the place in the sidebar is Plan
    await user.should_see("New Goal")
    assert active(user) == ["/budget-entries"] and header_title(user) == "Goals"
    [tab] = [e for e in user.find(kind=ui.link).elements if "section-tab" in e.classes and "active" in e.classes]
    assert tab.props["href"] == "/goals" and tab.props["aria-current"] == "page"
    assert drawers(user) == [drawer]


async def test_a_page_leaves_nothing_behind(user: User) -> None:
    await user.open("/budget")
    ticks: list[int] = []
    with user, layout.page_area():
        timer = ui.timer(0.01, lambda: ticks.append(1))  # as a page's own timer
        before = {e.id for e in layout.page_area().descendants()}
    await asyncio.sleep(0.05)
    assert ticks
    assert user.client in layout._month_steps  # its month stepper, for ← and →
    refresh = layout._data_listeners[user.client]  # it refreshes on saves from the sidebar's sheets
    await go(user, "/history")
    await user.should_see("Change History")
    assert not before & set(user.client.elements)  # the elements of the page before (its timers too) are gone
    assert timer.is_deleted
    ticked = len(ticks)
    await asyncio.sleep(0.05)
    assert len(ticks) == ticked  # the timer stopped with its page
    assert user.client not in layout._month_steps
    assert user.client not in layout._data_listeners  # the Change History doesn't listen; the Budget page is gone
    await go(user, "/payees")
    assert layout._data_listeners[user.client] is not refresh  # the Payees page's own
    key(user, page_keys(user), "ArrowLeft")  # the Budget page's month stepper is not this page's
    assert user.client.sub_pages_router.current_path == "/payees"


async def test_a_change_of_page_closes_an_open_sheet(user: User) -> None:
    await user.open("/")
    key(user, page_keys(user), "n")  # the sidebar's New Transaction, outside the page
    assert sheet_open(user)
    await go(user, "/budget")  # e.g. the browser's back
    with user:
        assert not any(isinstance(e, ui.dialog) for e in user.client.elements.values())
    key(user, page_keys(user), "?")  # the page keys work again
    await user.should_see("Search this page")


async def test_the_badges_of_the_page_before_are_let_go(user: User) -> None:
    await user.open("/recurring")
    assert set(layout._due_badges[user.client]) == {"/", "/recurring", "/bills"}
    await go(user, "/budget")
    assert set(layout._due_badges[user.client]) == {"/"}  # the sidebar's stays
    await go(user, "/loans")
    assert set(layout._due_badges[user.client]) == {"/", "/loans"}


async def test_a_change_of_page_closes_the_phone_drawer(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    await user.open("/")
    ran: list[str] = []
    run = user.client.run_javascript

    def record(code: str, **kwargs):
        ran.append(code)
        return run(code, **kwargs)

    monkeypatch.setattr(user.client, "run_javascript", record)
    await go(user, "/tags")
    drawer = layout._shells[user.client].drawer
    assert layout.HIDE_MOBILE_DRAWER.format(id=drawer.id) in ran


# ------------------------------------------------------------------ query parameters
async def test_query_parameters_reach_the_page(user: User, demo) -> None:
    await user.open("/")
    await go(user, f"/budget?month={PREV}")
    assert month(user) == PREV
    earlier = months.add_months(PREV, -1)
    await go(user, f"/budget?month={earlier}")  # the same page with another month: built anew
    assert month(user) == earlier
    await go(user, f"/transactions?month={PREV}&category=01")
    await user.should_see("Filters")
    assert active(user) == ["/transactions"]


# ------------------------------------------------------------------ history and reload
async def test_back_forward_and_reload_open_the_page(user: User) -> None:
    await user.open("/")
    [drawer] = drawers(user)
    await go(user, "/budget")
    await go(user, "/payees")
    user.navigate.back()
    await arrives(user, "/budget")
    assert active(user) == ["/budget"] and drawers(user) == [drawer]
    user.navigate.forward()
    await arrives(user, "/payees")
    assert active(user) == ["/settings"]
    user.navigate.reload()  # a new document, on the page of the URL
    for _ in range(50):
        if user.client.id != drawer.client.id:
            break
        await asyncio.sleep(0.02)
    await user.should_see("Rules…")
    assert drawers(user) != [drawer] and active(user) == ["/settings"]


@pytest.mark.parametrize("path", ["/goals", "/tax", f"/budget?month={PREV}"])
async def test_a_url_opens_its_page_directly(user: User, path: str) -> None:
    await user.open(path)
    assert user.client.sub_pages_router.current_path == path
    route = path.split("?", maxsplit=1)[0]
    assert len(drawers(user)) == 1 and active(user) == [layout.NAV_PARENT.get(route, route)]


# ------------------------------------------------------------------ the lock (F27)
@pytest.mark.parametrize("path", ["/budget", "/welcome"])  # a page of the shell, and one of its own
async def test_a_locked_app_shows_no_shell(user: User, path: str) -> None:
    security.set_password(PW, PW)
    await user.open(path)
    await user.should_see("Enter your password to open your records.")
    assert drawers(user) == []


async def test_a_lock_from_elsewhere_holds_on_the_next_change_of_page(user: User) -> None:
    security.set_password(PW, PW)
    await open_unlocked(user, "/settings")
    await user.should_see("On. The app asks for the password before it opens.")
    security.lock()  # another window's Lock Now
    UserInteraction(user, {nav(user, "/transactions")}, None).click()
    await user.should_see("Enter your password to open your records.")
    with user:
        assert user.client.page.path == "/lock"
    assert drawers(user) == []
