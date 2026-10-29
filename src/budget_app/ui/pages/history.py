"""Change history: the most recent changes, what they did, and Undo (F28)."""

from __future__ import annotations

from datetime import datetime

from nicegui import ui

from budget_app import i18n
from budget_app.i18n import t
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.storage.history import Action
from budget_app.ui.layout import error, frame, success
from budget_app.ui.widgets.empty import empty_state

LIMIT = 200


def _when(action: Action) -> str:
    at = datetime.fromisoformat(action.at)
    return f"{i18n.date_label(at.date())}, {at:%H:%M}"


def page() -> None:
    with frame(t("Change History"), "/history", narrow=True) as (ctx, _):

        @ui.refreshable
        def body() -> None:
            actions = undo_svc.recent(ctx, LIMIT)
            with ui.element("div").classes("group"):
                if not actions:
                    empty_state(
                        t("No changes yet."),
                        t("The last changes to your records. Undo puts the records back as they were before a change."),
                    )
                for act in actions:
                    with ui.element("div").classes("group-row history-row"):
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(undo_svc.label(ctx, act)).classes("truncate" + (" text-2" if act.undone else ""))
                            caption = f"{_when(act)} · {undo_svc.summary(act)}"
                            if act.undone:
                                caption += " · " + t("undone")
                            ui.label(caption).classes("caption")
                            with ui.expansion(t("Details")).props("dense").classes("history-details"):
                                for change in act.changes:
                                    ui.label(undo_svc.describe_change(ctx, change)).classes("caption")
                        if not act.undone:
                            ui.button(t("Undo"), on_click=lambda a=act: undo(a)).props("flat dense no-caps").classes(
                                "text-primary"
                            )

        def undo(act: Action) -> None:
            try:
                undo_svc.undo(ctx, act.id)
            except ValidationError as exc:
                error(str(exc))
                return
            success(t("Undone: {action}", action=undo_svc.label(ctx, act)))
            body.refresh()

        body()
