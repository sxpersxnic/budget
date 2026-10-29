"""The empty state of a list: what the page is for, in a sentence or two, and the action that fills it.

Page headers carry no instructions (UX review, section 2); a first-time user reads them here instead, where the
list will be once there is something in it.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


def empty_state(
    title: str, text: str = "", action: str | None = None, on_click: Callable[[], object] | None = None
) -> ui.element:
    """A row of a group: `title` ("No goals yet."), `text` and, with `action`, the page's primary button.

    A page that offers its primary action in the empty state hides the header's button while the list is empty, so
    the screen keeps one primary action.
    """
    with ui.element("div").classes("group-row empty-state") as row:
        ui.label(title).classes("empty-title")
        if text:
            ui.label(text).classes("caption empty-text")
        if action:
            ui.button(action, icon="add", on_click=on_click).props("unelevated").classes("mt-1")
    return row
