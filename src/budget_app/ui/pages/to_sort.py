"""To sort (a tab of Transactions): the imported spending and income that has no category yet, one question per
payee (ui/widgets/sort_cards.py). The sidebar counts the payees waiting here."""

from __future__ import annotations

from nicegui import ui

from budget_app.i18n import t
from budget_app.services import sorting
from budget_app.ui.layout import frame, on_data_changed
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.sort_cards import sort_cards


def page() -> None:
    with frame(t("To Sort"), "/to-sort", narrow=True) as (ctx, _actions):

        @ui.refreshable
        def body() -> None:
            waiting = sorting.count(ctx)
            if not waiting:
                empty_state(
                    t("Everything is sorted."),
                    t("Imported transactions Budget can't place wait here, one question per payee."),
                )
                return
            with ui.element("div").classes("surface flex flex-col gap-5"):
                ui.label(t("One answer sorts every transaction of a payee.")).classes("caption")
                sort_cards(ctx, on_change=lambda: None if sorting.count(ctx) else body.refresh())

        body()
        on_data_changed(body.refresh)
