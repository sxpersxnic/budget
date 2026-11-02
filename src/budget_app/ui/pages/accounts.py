"""Wealth: every account's balance, the advance-payment accounts, and the sheets that add an account and record a
balance."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.models import (
    ACCOUNT_ADVANCE,
    ACCOUNT_REGULAR,
    ASSET_CASH,
    ASSET_CLASSES,
    ASSET_LOAN,
    HOLDING_CLASSES,
)
from budget_app.i18n import t
from budget_app.services import accounts as acc_svc
from budget_app.services import investments as inv_svc
from budget_app.services import loans as loan_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import (
    data_month_range,
    error,
    fmt,
    frame,
    month_options,
    named,
    sheet,
    sheet_buttons,
    undoable,
)
from budget_app.ui.pages.investments import open_conversion_sheet
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import account_options, currency_select

ROW_ACTION = "flat dense round size=sm"


def account_entry_dialog(ctx: Context, on_saved, month: str | None = None, account_id: str | None = None) -> None:
    """Add or edit the recorded balance of an account at the end of a month."""
    first, _ = data_month_range(ctx, back=60)
    with sheet(t("Record Balance"), width="26rem") as dlg:
        ui.label(t("What did the account hold at the end of the month?")).classes("footnote -mt-2")
        month_sel = (
            ui.select(
                month_options(first, months.current_month()), value=month or months.previous_month(), label=t("Month")
            )
            .props("dense outlined options-dense")
            .classes("w-full")
        )
        acc_sel = ui.select(
            account_options(ctx, converted=True, loans=False), value=account_id, label=t("Account"), with_input=True
        )
        acc_sel.props("dense outlined").classes("w-full")
        amount = (
            ui.number(t("Balance"), format="%.2f").props("outlined inputmode=decimal").classes("hero-amount w-full")
        )
        hint = ui.label().classes("caption px-1 -mt-2")

        def load() -> None:
            acc = ctx.store.accounts.get(acc_sel.value) if acc_sel.value else None
            amount.props(f"suffix={acc.currency}" if acc else "suffix=")
            entry = wealth_svc.entry_for(ctx, acc_sel.value, month_sel.value) if acc and month_sel.value else None
            amount.value = entry.amount if entry else None
            hint.set_text(t("Already recorded. Saving replaces it.") if entry else "")
            check()

        def check() -> None:
            save_btn.set_enabled(bool(month_sel.value and acc_sel.value and amount.value is not None))

        def save() -> None:
            try:
                wealth_svc.upsert_entry(ctx, acc_sel.value, month_sel.value, amount.value)
            except Exception as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Balance recorded"))
            on_saved()

        save_btn = sheet_buttons(dlg, t("Save"), save)
        month_sel.on_value_change(load)
        acc_sel.on_value_change(load)
        amount.on_value_change(check)
        load()


def _account_sheet(ctx: Context, kind: str, refresh, acc=None) -> None:
    """Add (acc=None) or edit an account."""
    advance = kind == ACCOUNT_ADVANCE
    if acc:
        title = t("Edit Advance Payment Account") if advance else t("Edit Account")
    else:
        title = t("New Advance Payment Account") if advance else t("New Account")
    in_use = acc is not None and acc_svc.account_in_use(ctx, acc.id)
    with sheet(title, width="26rem") as dlg:
        name = ui.input(t("Name"), value=acc.name if acc else "").props("dense outlined autofocus").classes("w-full")
        cur = currency_select(ctx, acc.currency if acc else ctx.store.settings.display_currency)
        if in_use:
            cur.disable()
            ui.label(t("The currency can’t change while transactions or recorded balances use this account.")).classes(
                "caption -mt-2"
            )
        elif not acc:
            ui.label(t("Every transaction on this account will be in this currency.")).classes("caption -mt-2")
        asset_class = None
        if not advance:  # F21: what the account holds; advance-payment accounts are cash
            is_loan = acc is not None and acc.is_loan  # F22: a loan's account keeps its class; the loan owns it
            classes = {k: t(v) for k, v in ASSET_CLASSES.items() if (k == ASSET_LOAN) == is_loan}
            asset_class = (
                ui.select(
                    classes,
                    value=acc.asset_class if acc and acc.asset_class in classes else ASSET_CASH,
                    label=t("Asset class"),
                )
                .props("dense outlined options-dense")
                .classes("w-full")
            )
            if is_loan:
                asset_class.disable()
            class_note = ui.label().classes("caption -mt-2")

            def explain() -> None:
                fresh = ctx.store.accounts.get(acc.id) if acc else None
                if asset_class.value == ASSET_LOAN:
                    text = t("The outstanding debt of its loan on the Loans page; no balances to record.")
                elif fresh is not None and fresh.invest_from and asset_class.value in HOLDING_CLASSES:
                    text = t(
                        "Valued from its holdings from {month} on; the balances recorded before stay as its history.",
                        month=months.label(fresh.invest_from),
                    )
                elif asset_class.value in HOLDING_CLASSES:
                    text = t("Valued from its holdings on the Investments page; no balances to record.")
                else:
                    text = t("Valued from the balances you record at the end of each month.")
                class_note.set_text(text)

            def class_chosen() -> None:
                chosen = asset_class.value
                if acc is not None and inv_svc.needs_conversion(ctx, acc.id, chosen):
                    # an account with history becomes a securities or crypto account through a conversion (decision 13)
                    fresh = ctx.store.accounts.get(acc.id)
                    asset_class.value = fresh.asset_class if fresh else ASSET_CASH  # until the conversion is saved

                    def converted() -> None:  # the conversion saved the class: this sheet is done
                        dlg.close()
                        refresh()

                    open_conversion_sheet(ctx, acc.id, chosen, converted)
                explain()

            asset_class.on_value_change(class_chosen)
            explain()

        def save():
            chosen = asset_class.value if asset_class else ASSET_CASH
            try:
                if acc:
                    acc_svc.edit_account(ctx, acc.id, name.value, cur.value, chosen)
                else:
                    acc_svc.add_account(ctx, name.value, cur.value, kind, chosen)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            refresh()

        sheet_buttons(dlg, t("Save") if acc else t("Add"), save)
        name.on("keydown.enter", save)


def accounts_group(ctx: Context, kind: str, title: str) -> None:
    @ui.refreshable
    def body():
        rows = acc_svc.accounts(ctx, kind)
        with ui.element("div").classes("group"):
            for acc in rows:
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(acc.name).classes("truncate")
                        if kind == ACCOUNT_REGULAR and acc.holds_securities:
                            ui.label(
                                t(
                                    "{asset_class} · {amount} in holdings",
                                    asset_class=t(ASSET_CLASSES[acc.asset_class]),
                                    amount=fmt(inv_svc.account_value(ctx, acc.id), acc.currency),
                                )
                            ).classes("caption")
                        elif kind == ACCOUNT_REGULAR and acc.is_loan:  # F22
                            loan = loan_svc.of_account(ctx, acc.id)
                            owed = t(
                                "{amount} outstanding",
                                amount=fmt(loan_svc.debt(ctx, loan) if loan else 0, acc.currency),
                            )
                            ui.label(f"{t(ASSET_CLASSES[ASSET_LOAN])} · {owed}").classes("caption")
                        elif kind == ACCOUNT_REGULAR:
                            last = wealth_svc.balance_entry(ctx, acc.id, "9999-12")
                            balance = (
                                t(
                                    "{amount} at the end of {month}",
                                    amount=fmt(last.amount, acc.currency),
                                    month=months.label(last.month),
                                )
                                if last
                                else t("No balance recorded")
                            )
                            if acc.asset_class != ASSET_CASH:
                                balance = (
                                    f"{t(ASSET_CLASSES.get(acc.asset_class, ASSET_CLASSES[ASSET_CASH]))} · {balance}"
                                )
                            ui.label(balance).classes("caption")
                        else:
                            ui.label(
                                t(
                                    "{amount} outstanding",
                                    amount=fmt(tx_svc.advance_account_balance(ctx, acc.id), acc.currency),
                                )
                            ).classes("caption")
                    ui.label(acc.currency).classes("text-2 text-[13px]")
                    with ui.row().classes("row-actions gap-0 no-wrap"):
                        named(
                            ui.button(icon="o_edit", on_click=lambda a=acc: _account_sheet(ctx, kind, body.refresh, a))
                            .props(ROW_ACTION)
                            .classes("text-primary"),
                            t("Edit"),
                        )

                        def delete(a=acc):
                            def do():
                                try:
                                    acc_svc.delete_account(ctx, a.id)
                                    undoable(t("Deleted {name}", name=a.name))
                                except ValidationError as exc:
                                    error(str(exc))
                                body.refresh()

                            confirm(
                                t("Delete “{name}”?", name=a.name),
                                do,
                                detail=t(
                                    "Its recorded balances are removed too. "
                                    "Accounts with transactions can’t be deleted."
                                ),
                            )

                        named(
                            ui.button(icon="o_delete", on_click=delete).props(ROW_ACTION).classes("text-negative"),
                            t("Delete"),
                        )
            with ui.element("div").classes("group-row"):
                ui.button(
                    t("Add Advance Payment Account…") if kind == ACCOUNT_ADVANCE else t("Add Account…"),
                    on_click=lambda: _account_sheet(ctx, kind, body.refresh),
                ).props("flat dense no-caps").classes("link-btn -ml-2")

    with ui.column().classes("w-full gap-0"):
        ui.label(title).classes("group-header")
        body()


def page() -> None:
    with frame(t("Accounts"), "/accounts", narrow=True) as (ctx, _):
        accounts_group(ctx, ACCOUNT_REGULAR, t("Accounts"))
        accounts_group(ctx, ACCOUNT_ADVANCE, t("Advance Payment Accounts"))
