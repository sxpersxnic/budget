"""Confirmation messages in the UI tests: `ui.notify` toasts and undoable `ui.notification` toasts."""

from nicegui import ElementFilter, ui
from nicegui.testing import User


def undo_toasts(user: User) -> list[ui.notification]:
    with user:
        return list(ElementFilter(kind=ui.notification, local_scope=False))


def notified(user: User, text: str) -> bool:
    """True if a toast containing `text` was shown, whether plain or with an Undo button."""
    if user.notify.contains(text):
        return True
    return any(text in str(n.props["options"].get("message", "")) for n in undo_toasts(user))
