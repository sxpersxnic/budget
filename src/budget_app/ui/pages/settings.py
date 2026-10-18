"""Settings: budget horizon, display currency, accounts, advance accounts, income categories."""

from __future__ import annotations

from nicegui import ui

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, frame, month_options, sheet, sheet_buttons, success
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import currency_select

ROW_ACTION = "flat dense round size=sm"


def _account_sheet(ctx: Context, kind: str, refresh, acc=None) -> None:
    """Add (acc=None) or edit an account."""
    noun = "Advance Payment Account" if kind == ACCOUNT_ADVANCE else "Account"
    in_use = acc is not None and acc_svc.account_in_use(ctx, acc.id)
    with sheet(f"Edit {noun}" if acc else f"New {noun}", width="26rem") as dlg:
        name = ui.input("Name", value=acc.name if acc else "").props("dense outlined autofocus").classes("w-full")
        cur = currency_select(ctx, acc.currency if acc else ctx.store.settings.display_currency)
        if in_use:
            cur.disable()
            ui.label("The currency can’t change while transactions use this account.").classes("caption -mt-2")
        elif not acc:
            ui.label("Every transaction on this account will be in this currency.").classes("caption -mt-2")

        def save():
            try:
                if acc:
                    acc_svc.edit_account(ctx, acc.id, name.value, cur.value)
                else:
                    acc_svc.add_account(ctx, name.value, cur.value, kind)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            refresh()

        sheet_buttons(dlg, "Save" if acc else "Add", save)
        name.on("keydown.enter", save)


def _accounts_group(ctx: Context, kind: str, title: str) -> None:
    @ui.refreshable
    def body():
        rows = acc_svc.accounts(ctx, kind)
        with ui.element("div").classes("group"):
            for acc in rows:
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(acc.name).classes("truncate")
                        if kind == ACCOUNT_REGULAR:
                            last = wealth_svc.balance_entry(ctx, acc.id, "9999-12")
                            ui.label(
                                f"{fmt(last.amount, acc.currency)} at the end of {months.label(last.month)}"
                                if last
                                else "No balance recorded"
                            ).classes("caption")
                        else:
                            ui.label(
                                f"{fmt(tx_svc.advance_account_balance(ctx, acc.id), acc.currency)} outstanding"
                            ).classes("caption")
                    ui.label(acc.currency).classes("text-2 text-[13px]")
                    with ui.row().classes("row-actions gap-0 no-wrap"):
                        ui.button(
                            icon="o_edit", on_click=lambda a=acc: _account_sheet(ctx, kind, body.refresh, a)
                        ).props(ROW_ACTION).classes("text-primary").tooltip("Edit")

                        def delete(a=acc):
                            def do():
                                try:
                                    acc_svc.delete_account(ctx, a.id)
                                    success(f"Deleted {a.name}")
                                except ValidationError as exc:
                                    error(str(exc))
                                body.refresh()

                            confirm(
                                f"Delete “{a.name}”?",
                                do,
                                detail="Its recorded balances are removed too. Accounts with transactions can’t be "
                                "deleted.",
                            )

                        ui.button(icon="o_delete", on_click=delete).props(ROW_ACTION).classes("text-negative").tooltip(
                            "Delete"
                        )
            with ui.element("div").classes("group-row"):
                ui.button(
                    f"Add {'Advance Payment Account' if kind == ACCOUNT_ADVANCE else 'Account'}…",
                    on_click=lambda: _account_sheet(ctx, kind, body.refresh),
                ).props("flat dense no-caps").classes("link-btn -ml-2")

    with ui.column().classes("w-full gap-0"):
        ui.label(title).classes("group-header")
        body()


def _category_sheet(ctx: Context, refresh, cat=None) -> None:
    with sheet("Rename Income Category" if cat else "New Income Category", width="24rem") as dlg:
        name = ui.input("Name", value=cat.name if cat else "").props("dense outlined autofocus").classes("w-full")

        def save():
            try:
                if cat:
                    acc_svc.rename_income_category(ctx, cat.id, name.value)
                else:
                    acc_svc.add_income_category(ctx, name.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            refresh()

        sheet_buttons(dlg, "Save" if cat else "Add", save)
        name.on("keydown.enter", save)


def _income_group(ctx: Context) -> None:
    @ui.refreshable
    def body():
        with ui.element("div").classes("group"):
            for cat in acc_svc.income_categories(ctx):
                with ui.element("div").classes("group-row"):
                    ui.label(cat.name).classes("flex-1 truncate")
                    with ui.row().classes("row-actions gap-0 no-wrap"):
                        ui.button(icon="o_edit", on_click=lambda c=cat: _category_sheet(ctx, body.refresh, c)).props(
                            ROW_ACTION
                        ).classes("text-primary").tooltip("Rename")

                        def delete(c=cat):
                            def do():
                                try:
                                    acc_svc.delete_income_category(ctx, c.id)
                                except ValidationError as exc:
                                    error(str(exc))
                                body.refresh()

                            confirm(f"Delete “{c.name}”?", do, detail="Categories that are in use can’t be deleted.")

                        ui.button(icon="o_delete", on_click=delete).props(ROW_ACTION).classes("text-negative").tooltip(
                            "Delete"
                        )
            with ui.element("div").classes("group-row"):
                ui.button("Add Category…", on_click=lambda: _category_sheet(ctx, body.refresh)).props(
                    "flat dense no-caps"
                ).classes("link-btn -ml-2")

    with ui.column().classes("w-full gap-0"):
        ui.label("Income Categories").classes("group-header")
        body()


def page() -> None:
    with frame("Settings", "/settings", narrow=True) as (ctx, _):
        with ui.column().classes("w-full gap-0"):
            ui.label("Budget").classes("group-header")
            with ui.element("div").classes("group"):
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label("Plan Until")
                        ui.label("Extending it continues every budget entry that runs forever.").classes("caption")
                    cm = months.current_month()
                    opts = month_options(cm, months.add_months(cm, 12 * 15))
                    horizon = ctx.horizon()
                    opts.setdefault(horizon, months.label(horizon))
                    sel = ui.select(opts, value=horizon).props("dense outlined options-dense").classes("w-36")
                    apply_btn = ui.button("Apply").props("unelevated dense").classes("px-3")
                    apply_btn.set_visibility(False)

                    def apply():
                        added = budget_svc.set_horizon(ctx, sel.value)
                        apply_btn.set_visibility(False)
                        success(
                            f"Planning until {months.label(sel.value)}"
                            + (f", {added} budget entries added" if added else "")
                        )

                    apply_btn.on_click(apply)
                    sel.on_value_change(lambda e: apply_btn.set_visibility(e.value != ctx.horizon()))
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label("Display Currency")
                        ui.label("Amounts are shown in this currency unless a page says otherwise.").classes("caption")

                    def set_cur(e):
                        ctx.store.settings.display_currency = e.value
                        ctx.store.save_settings()
                        success(f"Showing amounts in {e.value}")

                    currency_select(ctx, ctx.store.settings.display_currency, on_change=set_cur, label=None)
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label("Base Currency")
                        ui.label("Every amount is also stored in this currency. It can’t be changed.").classes(
                            "caption"
                        )
                    ui.label(BASE_CURRENCY).classes("text-2 pr-2")
        _accounts_group(ctx, ACCOUNT_REGULAR, "Accounts")
        _accounts_group(ctx, ACCOUNT_ADVANCE, "Advance Payment Accounts")
        _income_group(ctx)
