"""Keyboard shortcuts, the shortcuts sheet, and names for screen readers on every page (F32)."""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from test_faster_entry_ui import key, keyboards, menu_items
from test_transaction_form import field, put

from budget_app.domain import months
from budget_app.services import templates as templates_svc
from budget_app.ui import layout

ROUTES = [path for path, _, _ in layout.NAV] + [layout.SETTINGS[0], *layout.NAV_PARENT]


def page_keys(user: User) -> ui.keyboard:
    return keyboards(user)[0]


async def arrives(user: User, path: str) -> None:
    for _ in range(50):
        if user.back_history and user.back_history[-1] == path:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"still on {user.back_history[-1:]}, not {path}")


def sheet_open(user: User) -> bool:
    return any(d.value for d in user.find(kind=ui.dialog).elements)


async def test_question_mark_and_the_sidebar_open_the_shortcuts(user: User) -> None:
    await user.open("/")
    key(user, page_keys(user), "?")
    await user.should_see("Search this page")
    await user.should_see("Previous or next month")
    user.find(kind=ui.button, content="Done").click()

    await user.open("/transactions")
    user.find(kind=ui.button, content="Keyboard Shortcuts").click()
    await user.should_see("Save the open sheet")


async def test_digits_go_to_the_sidebar_places_and_comma_to_settings(user: User) -> None:
    await user.open("/")
    key(user, page_keys(user), "3")
    await arrives(user, layout.NAV[2][0])
    key(user, page_keys(user), ",")
    await arrives(user, "/settings")
    key(user, page_keys(user), "1")
    await arrives(user, "/")
    key(user, page_keys(user), "3", ctrl=True)  # with a modifier: the browser's
    key(user, page_keys(user), "0")  # no place
    await asyncio.sleep(0.05)
    assert user.back_history[-1] == "/"


async def test_arrows_step_the_month(user: User) -> None:
    await user.open("/budget")
    month = next(s for s in user.find(kind=ui.select).elements if s.props.get("aria-label") == "Month")
    start = month.value
    key(user, page_keys(user), "ArrowLeft")
    assert month.value == months.add_months(start, -1)
    key(user, page_keys(user), "ArrowRight")
    key(user, page_keys(user), "ArrowRight")
    assert month.value == months.add_months(start, 1)


async def test_arrows_do_nothing_without_a_month(user: User) -> None:
    await user.open("/budget")
    await user.open("/payees")  # the stepper of the page before is not this page's
    key(user, page_keys(user), "ArrowLeft")
    assert user.back_history[-1] == "/payees"


async def test_slash_focuses_the_search(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(ui, "run_javascript", lambda code, **_: ran.append(code))
    await user.open("/transactions")
    key(user, page_keys(user), "/")
    assert ran == [layout.SEARCH_FOCUS]
    assert any("search" in el.classes for el in user.find(kind=ui.input).elements)  # the field it focuses


async def test_page_keys_wait_while_a_sheet_is_open(user: User) -> None:
    await user.open("/")
    key(user, page_keys(user), "n")
    assert sheet_open(user)
    key(user, page_keys(user), "3")
    key(user, page_keys(user), "?")
    await asyncio.sleep(0.05)
    assert user.back_history[-1] == "/"
    with pytest.raises(AssertionError):
        await user.should_see("Search this page", retries=2)


async def test_ctrl_enter_presses_the_sheet_action(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, field(user, ui.number, "Amount"), 4.5)
    put(user, field(user, ui.select, "Category"), "01.1")
    put(user, field(user, ui.select, "From Account"), setup["chf"].id)
    UserInteraction(user, {menu_items(user)[-1]}, None).click()  # "Save as Template…"
    put(user, field(user, ui.input, "Name"), "Coffee")
    card = max((c for c in user.find(kind=ui.card).elements if "sheet" in c.classes), key=lambda c: c.id)
    listener = next(lst for lst in card._event_listeners.values() if lst.type == "keydown")
    assert "Enter" in listener.js_handler and "stopPropagation" in listener.js_handler  # the form doesn't save too
    UserInteraction(user, {card}, None).trigger("keydown")
    assert [tp.name for tp in templates_svc.all_templates(ctx)] == ["Coffee"]
    assert ctx.store.transactions.all() == []


async def test_ctrl_enter_leaves_a_disabled_action_alone(user: User) -> None:
    await user.open("/")
    with user:
        with layout.sheet("Try") as dlg:
            pressed: list[bool] = []
            layout.sheet_buttons(dlg, "Go", lambda: pressed.append(True), enabled=False)
        card = dlg.default_slot.children[0]
    UserInteraction(user, {card}, None).trigger("keydown")
    assert pressed == []


async def test_skip_link_and_landmarks(user: User) -> None:
    await user.open("/")
    main = next(el for el in user.find(kind=ui.column).elements if el.props.get("role") == "main")
    skip = next(el for el in user.find(kind=ui.link).elements if "skip-link" in el.classes)
    assert skip.props["href"] == f"#{main.html_id}"
    drawer = user.find(kind=ui.left_drawer).elements.pop()
    assert drawer.props["role"] == "navigation" and drawer.props["aria-label"] == "Places"


@pytest.mark.parametrize("path", ROUTES)
async def test_every_icon_button_has_a_name(user: User, demo, path: str) -> None:
    """A button that shows only an icon needs a name a screen reader can say (aria-label)."""
    await user.open(path)
    nameless = [
        b.props.get("icon")
        for b in user.find(kind=ui.button).elements
        if b.props.get("icon") and not b.text and not b.props.get("aria-label")
    ]
    assert nameless == [], f"{path}: {nameless}"


@pytest.mark.parametrize("path", ROUTES)
async def test_nothing_jumps_the_tab_order(user: User, path: str) -> None:
    """The tab order follows the page: no element takes a positive tabindex."""
    await user.open(path)
    with user:
        elements = list(ui.context.client.layout.descendants())
    assert [e for e in elements if int(str(e.props.get("tabindex", 0))) > 0] == []
