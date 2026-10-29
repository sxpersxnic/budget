"""Subscriptions (F17): charges that repeat, found in the transactions, with what they cost a year.

Three groups: *Detected* (payees charging at the same rhythm for about the same amount, each with *Make
Recurring…*, which opens F05's series sheet prefilled from the last charge, and *Ignore*), *Recurring
Transactions* (the running expenditure series of F05 with their yearly cost) and, closed, *Ignored* with *Watch
Again*. Nothing is booked here; the rules are in `domain.subscriptions`.
"""

from __future__ import annotations

from nicegui import ui

from budget_app import i18n
from budget_app.domain import subscriptions as rules
from budget_app.domain.models import SubscriptionIgnore
from budget_app.i18n import t, tn
from budget_app.services import subscriptions as sub_svc
from budget_app.services.context import ValidationError
from budget_app.services.subscriptions import Subscription
from budget_app.ui.layout import error, fmt, frame, on_data_changed, undoable
from budget_app.ui.pages.add_budget import recurrence_label
from budget_app.ui.pages.recurring import open_recurring_sheet

ROW_ACTION = "flat dense round size=sm"


def caption(sub: Subscription) -> str:
    """'49.90 CHF · Every month · 8 charges since 15 Mar 2026 · last on 15 Oct 2026 · next around 15 Nov 2026'."""
    return " · ".join(
        [
            fmt(sub.amount, sub.currency),
            recurrence_label(sub.period),
            tn("{n} charge since {date}", "{n} charges since {date}", sub.count, date=i18n.date_label(sub.first)),
            t("last on {date}", date=i18n.date_label(sub.last)),
            t("next around {date}", date=i18n.date_label(sub.next)),
        ]
    )


def yearly_label(amount: float, currency: str) -> str:
    return t("{amount} a year", amount=fmt(amount, currency))


def page() -> None:
    with frame(t("Subscriptions"), "/subscriptions", narrow=True) as (ctx, _actions):
        currency = ctx.store.settings.display_currency
        ui.label(
            t(
                "Charges that come back at the same rhythm for about the same amount: streaming, phone, gym, "
                "insurance. Found in your transactions; nothing is booked for you. Turn one into a recurring "
                "transaction, or ignore it."
            )
        ).classes("caption -mt-2")

        def refresh() -> None:
            detected.refresh()
            series_list.refresh()
            ignored_list.refresh()

        def make_recurring(sub: Subscription) -> None:
            try:
                prefill = sub_svc.to_recurring(ctx, sub)
            except ValidationError as exc:
                error(str(exc))
                return
            open_recurring_sheet(ctx, refresh, prefill=prefill, first_booked=sub.last_id)

        def ignore(sub: Subscription) -> None:
            sub_svc.ignore(ctx, sub)
            undoable(t("Ignored {name}", name=sub.name))
            refresh()

        def unignore(item: SubscriptionIgnore) -> None:
            try:
                sub_svc.unignore(ctx, item.id)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(t("Watching {name} again", name=item.name))
            refresh()

        @ui.refreshable
        def detected() -> None:
            subs = sub_svc.detect(ctx, currency)
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    ui.label(t("Detected")).classes("group-header")
                    ui.space()
                    if subs:
                        total = sum(s.yearly_view for s in subs)
                        ui.label(
                            tn(
                                "{n} subscription · about {amount} a year",
                                "{n} subscriptions · about {amount} a year",
                                len(subs),
                                amount=fmt(total, currency),
                            )
                        ).classes("caption pb-1.5 pr-1")
                with ui.element("div").classes("group"):
                    if not subs:
                        with ui.element("div").classes("group-row"):
                            ui.label(
                                t(
                                    "Nothing detected yet. A subscription shows once it was charged {n} times at the "
                                    "same rhythm ({m} times for half-yearly and yearly ones).",
                                    n=rules.MIN_CHARGES,
                                    m=rules.MIN_CHARGES_LONG,
                                )
                            ).classes("text-2")
                    for sub in subs:
                        with ui.element("div").classes("group-row flex-wrap"):
                            with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                                ui.label(sub.name).classes("truncate")
                                ui.label(caption(sub)).classes("caption")
                            ui.label(yearly_label(sub.yearly, sub.currency)).classes("num whitespace-nowrap")
                            with ui.row().classes("items-center gap-0 no-wrap"):
                                ui.button(t("Make Recurring…"), on_click=lambda s=sub: make_recurring(s)).props(
                                    "flat dense no-caps"
                                ).classes("text-primary")
                                ui.button(icon="o_visibility_off", on_click=lambda s=sub: ignore(s)).props(
                                    f'{ROW_ACTION} aria-label="{t("Ignore")}"'
                                ).classes("text-2").tooltip(t("Ignore"))

        @ui.refreshable
        def series_list() -> None:
            rows = sub_svc.recurring_spend(ctx, currency)
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    ui.label(t("Recurring Transactions")).classes("group-header")
                    ui.space()
                    if rows:
                        total = sum(r.yearly_view for r in rows)
                        ui.label(t("about {amount} a year", amount=fmt(total, currency))).classes("caption pb-1.5 pr-1")
                with ui.element("div").classes("group"):
                    if not rows:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No recurring expenditure yet.")).classes("text-2")
                    for row in rows:
                        with ui.element("div").classes("group-row"):
                            with ui.column().classes("gap-0 flex-1 min-w-0"):
                                ui.label(row.title).classes("truncate")
                                amount = fmt(row.series.amount, row.series.currency)
                                ui.label(f"{amount} · {recurrence_label(row.series.recur_months)}").classes("caption")
                            ui.label(yearly_label(row.yearly, row.series.currency)).classes("num whitespace-nowrap")
                    with ui.element("div").classes("group-row"):
                        ui.link(t("All Recurring"), "/recurring").classes("link-btn no-underline text-[13px]")

        @ui.refreshable
        def ignored_list() -> None:
            items = sub_svc.ignored(ctx)
            if not items:
                return
            title = tn("Ignored · {n} subscription", "Ignored · {n} subscriptions", len(items))
            with ui.expansion(title).classes("w-full group closed-bills").props("dense"):
                for item in items:
                    with ui.element("div").classes("group-row"):
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(item.name or item.key).classes("truncate")
                            ui.label(item.currency).classes("caption")
                        ui.button(t("Watch Again"), on_click=lambda i=item: unignore(i)).props(
                            "flat dense no-caps"
                        ).classes("text-primary")

        detected()
        series_list()
        ignored_list()
        on_data_changed(refresh)
