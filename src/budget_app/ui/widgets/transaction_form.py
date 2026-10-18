"""The transaction form, used by the 'Add transaction' page and the edit dialog."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app.domain.fx import FxError
from budget_app.domain.models import (
    ACCOUNT_ADVANCE,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_TRANSFER,
    TX_TYPES,
    Transaction,
)
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.ui.layout import date_input, error, fmt, parse_date, success
from budget_app.ui.widgets.selects import (
    account_options,
    currency_select,
    expenditure_category_select,
    income_category_options,
    income_category_select,
    set_options,
)

log = logging.getLogger(__name__)
AUTO_ADVANCE = "__auto__"


class TransactionForm:
    def __init__(
        self, ctx: Context, editing: Transaction | None = None, on_saved: Callable[[Transaction], None] | None = None
    ):
        self.ctx = ctx
        self.editing = editing
        self.on_saved = on_saved
        self._busy = False
        init = (
            tx_svc.to_input(editing)
            if editing
            else TransactionInput(date=date.today(), currency=ctx.store.settings.display_currency)
        )
        self._build(init)
        self._changed()

    # ------------------------------------------------------------------ layout
    def _build(self, init: TransactionInput) -> None:
        ch = self._changed
        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("w-full items-start gap-3 flex-wrap"):
                self.date = date_input("Date", init.date.isoformat() if init.date else "", on_change=ch).classes("w-44")
                self.amount = ui.number(
                    "Amount",
                    value=init.amount,
                    min=0,
                    format="%.2f",
                    on_change=ch,
                    validation={"Must be positive": lambda v: v is None or v > 0},
                )
                self.amount.props("dense outlined").classes("w-40")
                self.currency = currency_select(self.ctx, init.currency, on_change=ch)
                self.base_hint = ui.label().classes("text-sm text-slate-500 self-center")
            self.type = ui.toggle(dict(TX_TYPES.items()), value=init.type, on_change=ch)
            self.type.props("no-caps unelevated toggle-color=primary")

            # --- expenditure
            with ui.column().classes("w-full gap-3") as self.exp_box:
                with ui.row().classes("gap-3 flex-wrap"):
                    self.exp_category = expenditure_category_select(
                        init.category if init.type == TX_EXPENDITURE else None, on_change=ch
                    )
                    self.exp_from = self._account_select(
                        "From account", init.from_account if init.type == TX_EXPENDITURE else None
                    )
                self.exp_adv = ui.checkbox(
                    "Advance payment (partly paid for others)",
                    value=init.is_advance and init.type == TX_EXPENDITURE,
                    on_change=ch,
                )
                with ui.row().classes("gap-3 flex-wrap items-start pl-8") as self.exp_adv_box:
                    self.adv_mode = ui.toggle(
                        {"percent": "%", "amount": "Amount"}, value=init.advance_mode, on_change=ch
                    ).props("dense no-caps")
                    self.adv_value = ui.number("Paid for others", value=init.advance_value, min=0, on_change=ch)
                    self.adv_value.props("dense outlined").classes("w-40")
                    self.exp_adv_account = (
                        ui.select({}, label="Advance payment account", on_change=ch)
                        .props("dense outlined")
                        .classes("min-w-[16rem]")
                    )
                    self._init_exp_adv_account = init.advance_account if init.type == TX_EXPENDITURE else ""
                    self.debtor = (
                        ui.input("Debtor", value=init.debtor if init.type == TX_EXPENDITURE else "", on_change=ch)
                        .props("dense outlined")
                        .classes("w-56")
                    )
                    self.adv_hint = ui.label().classes("text-sm text-slate-500 self-center")

            # --- income
            with ui.column().classes("w-full gap-3") as self.inc_box:
                with ui.row().classes("gap-3 flex-wrap"):
                    self.inc_category = income_category_select(
                        self.ctx,
                        init.category if init.type == TX_INCOME and not init.is_advance else None,
                        on_change=ch,
                    )
                    self.inc_to = self._account_select(
                        "To account", init.to_account if init.type == TX_INCOME else None
                    )
                self.inc_adv = ui.checkbox(
                    "Repayment of an advance payment", value=init.is_advance and init.type == TX_INCOME, on_change=ch
                )
                with ui.row().classes("gap-3 flex-wrap pl-8") as self.inc_adv_box:
                    self.inc_adv_account = ui.select(
                        {}, label="Advance payment account", value=None, on_change=self._inc_adv_account_changed
                    )
                    self.inc_adv_account.props("dense outlined").classes("min-w-[16rem]")
                    self.open_items = ui.select({}, label="Outstanding payment", on_change=self._open_item_changed)
                    self.open_items.props("dense outlined").classes("min-w-[24rem]")
                    self._init_inc_adv = (init.advance_account, init.settles_id) if init.type == TX_INCOME else ("", "")

            # --- transfer / exchange
            with ui.row().classes("gap-3 flex-wrap items-center") as self.move_box:
                init_move = init.type in (TX_TRANSFER, TX_EXCHANGE)
                self.move_from = self._account_select("From account", init.from_account if init_move else None)
                ui.icon("arrow_forward").classes("text-slate-400")
                self.move_to = self._account_select("To account", init.to_account if init_move else None)
                self.exchange_hint = ui.label().classes("text-sm text-slate-500")

            self.comment = (
                ui.textarea("Comment (optional)", value=init.comment)
                .props("dense outlined autogrow")
                .classes("w-full max-w-2xl")
            )
            with ui.row().classes("gap-2 items-center"):
                self.save_btn = ui.button("Save changes" if self.editing else "Save", icon="save", on_click=self._save)
                self.status = ui.label().classes("text-sm text-slate-500")

    def _account_select(self, label: str, value: str | None) -> ui.select:
        value = value or None
        sel = ui.select(
            {value: value} if value else {}, label=label, value=value, with_input=True, on_change=self._changed
        )
        return sel.props("dense outlined").classes("min-w-[16rem]")

    # ------------------------------------------------------------------ events
    def _inc_adv_account_changed(self) -> None:
        self.open_items.value = None
        self._changed()

    def _open_item_changed(self) -> None:
        item = self.ctx.store.transactions.get(self.open_items.value) if self.open_items.value else None
        if item and self.amount.value is None:
            left = item.advance_amount - tx_svc.settled_amount(
                self.ctx, item.id, self.editing.id if self.editing else ""
            )
            self.amount.value = round(left, 2)
        self._changed()

    def _changed(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._refresh_options()
        finally:
            self._busy = False
        inp = self.read()
        try:
            tx_svc.validate(self.ctx, inp, self.editing.id if self.editing else "")
            self.save_btn.enable()
            self.status.set_text("")
        except ValidationError as exc:
            self.save_btn.disable()
            self.status.set_text(str(exc))
        self._update_hints(inp)

    # ------------------------------------------------------------- dependents
    def _refresh_options(self) -> None:
        t, cur = self.type.value, self.currency.value
        self.exp_box.set_visibility(t == TX_EXPENDITURE)
        self.inc_box.set_visibility(t == TX_INCOME)
        self.move_box.set_visibility(t in (TX_TRANSFER, TX_EXCHANGE))
        self.exp_adv_box.set_visibility(bool(self.exp_adv.value))
        self.inc_adv_box.set_visibility(bool(self.inc_adv.value))
        self.inc_category.set_visibility(not self.inc_adv.value)
        set_options(self.inc_category, income_category_options(self.ctx))

        set_options(self.exp_from, account_options(self.ctx, currency=cur))
        set_options(self.inc_to, account_options(self.ctx, currency=cur))

        adv_opts = {AUTO_ADVANCE: "Advance payments (default)"} | dict(
            account_options(self.ctx, ACCOUNT_ADVANCE, cur).items()
        )
        default_adv = next(
            (k for k, v in adv_opts.items() if k != AUTO_ADVANCE and v.startswith("Advance payments (")), None
        )
        if default_adv:
            adv_opts.pop(AUTO_ADVANCE)
        if self._init_exp_adv_account:
            self.exp_adv_account.set_options(
                adv_opts, value=self._init_exp_adv_account if self._init_exp_adv_account in adv_opts else None
            )
            self._init_exp_adv_account = ""
        else:
            set_options(self.exp_adv_account, adv_opts)
        if self.exp_adv_account.value is None:
            self.exp_adv_account.set_value(default_adv or AUTO_ADVANCE)

        set_options(self.inc_adv_account, account_options(self.ctx, ACCOUNT_ADVANCE, cur))
        init_acc = self._init_inc_adv[0]
        if init_acc and self.inc_adv_account.value is None and init_acc in self.inc_adv_account.options:
            self.inc_adv_account.set_value(init_acc)
        acc = self.inc_adv_account.value
        items = tx_svc.open_advances(self.ctx, acc, self.editing.id if self.editing else "") if acc else []
        item_opts = {o.tx.id: o.label for o in items}
        if self._init_inc_adv[1]:
            settled = self.ctx.store.transactions.get(self._init_inc_adv[1])
            if settled is not None and settled.id not in item_opts:
                item_opts[settled.id] = f"{settled.date.isoformat()} · {settled.debtor or '—'}"
            self.open_items.set_options(item_opts, value=self._init_inc_adv[1])
            self._init_inc_adv = ("", "")
        else:
            set_options(self.open_items, item_opts)

        if t == TX_EXCHANGE:
            set_options(self.move_from, account_options(self.ctx))
            src = self.ctx.store.accounts.get(self.move_from.value) if self.move_from.value else None
            if src and self.currency.value != src.currency:
                self.currency.set_value(src.currency)
            set_options(self.move_to, account_options(self.ctx, exclude_currency=src.currency if src else None))
            self.currency.disable()
        else:
            self.currency.enable()
            set_options(self.move_from, account_options(self.ctx, currency=cur))
            set_options(self.move_to, account_options(self.ctx, currency=cur))

    def _update_hints(self, inp: TransactionInput) -> None:
        self.base_hint.set_text("")
        self.adv_hint.set_text("")
        self.exchange_hint.set_text("")
        if inp.amount and inp.currency:
            try:
                chf, fx_date = self.ctx.to_base_latest(inp.amount, inp.currency)
                if inp.currency != "CHF":
                    self.base_hint.set_text(f"= {fmt(chf, 'CHF')} (ECB {fx_date.isoformat()})")
                if inp.type == TX_EXPENDITURE and inp.is_advance:
                    share = tx_svc.advance_share(inp)
                    self.adv_hint.set_text(
                        f"Others: {fmt(share, inp.currency)} · own share: {fmt(inp.amount - share, inp.currency)}"
                    )
                if inp.type == TX_EXCHANGE and inp.to_account:
                    dst = self.ctx.store.accounts.get(inp.to_account)
                    if dst:
                        to_amt = self.ctx.fx.convert(inp.amount, inp.currency, dst.currency, fx_date)
                        self.exchange_hint.set_text(
                            f"≈ {fmt(to_amt, dst.currency)} credited (ECB {fx_date.isoformat()})"
                        )
            except FxError:  # missing rates: the hints are optional
                log.debug("No exchange rate for hint", exc_info=True)

    # ------------------------------------------------------------------ data
    def read(self) -> TransactionInput:
        t = self.type.value
        inp = TransactionInput(
            date=parse_date(self.date.value),
            type=t,
            amount=self.amount.value,
            currency=self.currency.value or "",
            comment=self.comment.value or "",
        )
        if t == TX_EXPENDITURE:
            inp.category = self.exp_category.value or ""
            inp.from_account = self.exp_from.value or ""
            inp.is_advance = bool(self.exp_adv.value)
            if inp.is_advance:
                inp.advance_mode = self.adv_mode.value
                inp.advance_value = self.adv_value.value
                acc = self.exp_adv_account.value
                inp.advance_account = "" if acc in (None, AUTO_ADVANCE) else acc
                inp.debtor = self.debtor.value or ""
        elif t == TX_INCOME:
            inp.to_account = self.inc_to.value or ""
            inp.is_advance = bool(self.inc_adv.value)
            if inp.is_advance:
                inp.advance_account = self.inc_adv_account.value or ""
                inp.settles_id = self.open_items.value or ""
            else:
                inp.category = self.inc_category.value or ""
        else:
            inp.from_account = self.move_from.value or ""
            inp.to_account = self.move_to.value or ""
        return inp

    def _save(self) -> None:
        try:
            tx = tx_svc.save(self.ctx, self.read(), self.editing.id if self.editing else "")
        except Exception as exc:  # validation and FX errors
            error(str(exc))
            return
        success("Transaction saved")
        if self.on_saved:
            self.on_saved(tx)
        if not self.editing:
            self.reset()

    def reset(self) -> None:
        """Clear every field except date and currency (and the chosen transaction type)."""
        self._busy = True
        try:
            for el in (
                self.amount,
                self.exp_category,
                self.exp_from,
                self.adv_value,
                self.inc_category,
                self.inc_to,
                self.inc_adv_account,
                self.open_items,
                self.move_from,
                self.move_to,
            ):
                el.set_value(None)
            self.exp_adv.set_value(False)
            self.inc_adv.set_value(False)
            self.debtor.set_value("")
            self.comment.set_value("")
            self.exp_adv_account.set_value(None)
        finally:
            self._busy = False
        self._changed()
