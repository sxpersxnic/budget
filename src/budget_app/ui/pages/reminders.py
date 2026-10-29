"""Reminders (F31): what wants doing or looking at, grouped by kind, each row a link to where it is dealt with. In the
app only; which kinds appear is chosen in Settings."""

from __future__ import annotations

from nicegui import ui

from budget_app.i18n import t, tn
from budget_app.services import reminders as rem_svc
from budget_app.services.reminders import Reminder
from budget_app.ui.layout import fmt, frame, on_data_changed

ICONS = {
    rem_svc.BALANCES: "o_account_balance_wallet",
    rem_svc.RECURRING: "o_event_repeat",
    rem_svc.BILLS: "o_request_quote",
    rem_svc.BUDGETS: "o_error_outline",
    rem_svc.GOALS: "o_flag",
}
TONES = {rem_svc.LATE: "text-neg", rem_svc.WARN: "notable-warn"}


def row(item: Reminder) -> None:
    with ui.link(target=item.link).classes("notable-row"):
        ui.icon(ICONS[item.kind], size="20px").classes(TONES[item.tone])
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(item.title).classes("n-title")
            ui.label(item.sub).classes("n-sub " + ("text-neg" if item.tone == rem_svc.LATE else ""))
        ui.label(fmt(item.amount, item.currency) if item.amount is not None else "").classes("n-amount")
        ui.icon("chevron_right", size="18px").classes("text-3")


def page() -> None:
    with frame(t("Reminders"), "/reminders", narrow=True) as (ctx, _):
        ui.label(
            t(
                "Balances to record, recurring transactions and bills that are due, budgets exceeded and goals behind "
                "their plan, all in one place while the app runs. Nothing is sent anywhere; choose in Settings which "
                "kinds appear here."
            )
        ).classes("caption -mt-2")

        @ui.refreshable
        def groups() -> None:
            found = rem_svc.reminders(ctx, ctx.store.settings.display_currency)
            n = sum(len(v) for v in found.values())
            with ui.column().classes("w-full gap-4"):
                if not found:
                    with ui.element("div").classes("group"), ui.element("div").classes("group-row"):
                        ui.label(t("Nothing to do right now.")).classes("text-2")
                    return
                ui.label(tn("{n} reminder", "{n} reminders", n)).classes("caption")
                for kind, items in found.items():
                    with ui.column().classes("w-full gap-0"):
                        ui.label(f"{t(rem_svc.TITLES[kind])} · {len(items)}").classes("group-header")
                        with ui.element("div").classes("surface flex flex-col gap-1"):
                            for item in items:
                                row(item)

        groups()
        on_data_changed(groups.refresh)
