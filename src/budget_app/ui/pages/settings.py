"""Settings: budget horizon, display currency, accounts, advance accounts, income categories."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import card, error, fmt, frame, month_options, success
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import currency_select


def _edit_account_dialog(ctx: Context, acc, refresh) -> None:
    in_use = acc_svc.account_in_use(ctx, acc.id)
    with ui.dialog() as dlg, ui.card().classes("min-w-[24rem]"):
        ui.label("Edit account").classes("text-lg font-semibold")
        name = ui.input("Name", value=acc.name).props("dense outlined").classes("w-full")
        cur = currency_select(ctx, acc.currency)
        if in_use:
            cur.disable()
            ui.label("The currency is fixed because the account is in use.").classes("text-xs text-slate-500")

        def save():
            try:
                acc_svc.edit_account(ctx, acc.id, name.value, cur.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            refresh()

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Save", on_click=save)
    dlg.open()


def _accounts_card(ctx: Context, kind: str, title: str) -> None:
    @ui.refreshable
    def body():
        rows = acc_svc.accounts(ctx, kind)
        if not rows:
            ui.label("No accounts yet.").classes("text-slate-500")
        with ui.grid(columns="minmax(10rem,1fr) 5rem minmax(9rem,auto) auto").classes(
            "w-full items-center gap-x-4 gap-y-1"
        ):
            if rows:
                for h in ("Name", "Currency", "Last balance" if kind == ACCOUNT_REGULAR else "Outstanding", ""):
                    ui.label(h).classes("text-xs uppercase text-slate-500")
            for acc in rows:
                ui.label(acc.name)
                ui.label(acc.currency)
                if kind == ACCOUNT_REGULAR:
                    last = wealth_svc.balance_entry(ctx, acc.id, "9999-12")
                    ui.label(f"{fmt(last.amount, acc.currency)} ({months.label(last.month)})" if last else "–")
                else:
                    ui.label(fmt(tx_svc.advance_account_balance(ctx, acc.id), acc.currency))
                with ui.row().classes("gap-0"):
                    ui.button(icon="edit", on_click=lambda a=acc: _edit_account_dialog(ctx, a, body.refresh)).props(
                        "flat dense round"
                    )

                    def delete(a=acc):
                        def do():
                            try:
                                acc_svc.delete_account(ctx, a.id)
                                success(f"Deleted {a.name}")
                            except ValidationError as exc:
                                error(str(exc))
                            body.refresh()

                        confirm(
                            f"Delete account '{a.name} ({a.currency})'? Recorded balances of it are removed too.", do
                        )

                    ui.button(icon="delete", on_click=delete).props("flat dense round color=negative")

    with card(title):
        body()
        with ui.row().classes("items-start gap-2 pt-2"):
            name = ui.input("New account name").props("dense outlined").classes("w-64")
            cur = currency_select(ctx, ctx.store.settings.display_currency)

            def add():
                try:
                    acc_svc.add_account(ctx, name.value, cur.value, kind)
                except ValidationError as exc:
                    error(str(exc))
                    return
                name.value = ""
                body.refresh()

            ui.button("Add", icon="add", on_click=add)


def _income_card(ctx: Context) -> None:
    @ui.refreshable
    def body():
        cats = acc_svc.income_categories(ctx)
        if not cats:
            ui.label("No income categories yet.").classes("text-slate-500")
        for cat in cats:
            with ui.row().classes("items-center gap-2"):
                inp = ui.input(value=cat.name).props("dense outlined").classes("w-64")

                def rename(c=cat, i=inp):
                    try:
                        acc_svc.rename_income_category(ctx, c.id, i.value)
                        success("Renamed")
                    except ValidationError as exc:
                        error(str(exc))
                        i.value = c.name

                ui.button(icon="check", on_click=rename).props("flat dense round").tooltip("Save name")

                def delete(c=cat):
                    def do():
                        try:
                            acc_svc.delete_income_category(ctx, c.id)
                        except ValidationError as exc:
                            error(str(exc))
                        body.refresh()

                    confirm(f"Delete income category '{c.name}'?", do)

                ui.button(icon="delete", on_click=delete).props("flat dense round color=negative")

    with card("Income categories"):
        body()
        with ui.row().classes("items-start gap-2 pt-2"):
            name = ui.input("New income category").props("dense outlined").classes("w-64")

            def add():
                try:
                    acc_svc.add_income_category(ctx, name.value)
                except ValidationError as exc:
                    error(str(exc))
                    return
                name.value = ""
                body.refresh()

            ui.button("Add", icon="add", on_click=add)


def page() -> None:
    with frame("Settings", "/settings") as ctx:
        with ui.row().classes("w-full gap-4 flex-nowrap max-md:flex-wrap"):
            with card("Budget horizon"):
                ui.label(
                    "Budgets can be planned until this month. Extending it continues all "
                    "budget entries whose final month is 'Forever'."
                ).classes("text-sm text-slate-500")
                cm = months.current_month()
                opts = month_options(cm, months.add_months(cm, 12 * 15))
                horizon = ctx.horizon()
                opts.setdefault(horizon, months.label(horizon))
                sel = ui.select(opts, value=horizon, label="Budget until").props("dense outlined").classes("w-48")

                def apply():
                    added = budget_svc.set_horizon(ctx, sel.value)
                    success(
                        f"Budget horizon set to {months.label(sel.value)}"
                        + (f" · {added} budget entries added" if added else "")
                    )

                ui.button("Apply", on_click=apply)
            with card("Display currency"):
                ui.label(
                    "The currency the budget is displayed in by default. The base currency (CHF) is fixed."
                ).classes("text-sm text-slate-500")

                def set_cur(e):
                    ctx.store.settings.display_currency = e.value
                    ctx.store.save_settings()
                    success(f"Display currency: {e.value}")

                currency_select(ctx, ctx.store.settings.display_currency, on_change=set_cur, label="Display currency")
        _accounts_card(ctx, ACCOUNT_REGULAR, "Accounts")
        _accounts_card(ctx, ACCOUNT_ADVANCE, "Advance payment accounts")
        _income_card(ctx)
