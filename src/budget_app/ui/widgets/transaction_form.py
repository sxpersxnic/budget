"""The transaction form, used by the 'Add transaction' page and the edit dialog."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
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
from budget_app.i18n import t, tc
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.ui.layout import date_input, error, fmt, parse_date, segmented, sheet, success
from budget_app.ui.widgets.item_list import ItemList
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
        self,
        ctx: Context,
        editing: Transaction | None = None,
        on_saved: Callable[[Transaction], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ):
        self.ctx = ctx
        self.editing = editing
        self.on_saved = on_saved
        self.on_cancel = on_cancel
        self._busy = False
        self._touched = False  # explain why Save is disabled only once the user has started
        init = (
            tx_svc.to_input(editing)
            if editing
            else TransactionInput(date=date.today(), currency=ctx.store.settings.display_currency)
        )
        self._build(init)
        self._changed()
        self._touched = True

    # ------------------------------------------------------------------ layout
    def _build(self, init: TransactionInput) -> None:
        ch = self._changed
        with ui.column().classes("w-full gap-4"):
            # The type decides every other field, so it comes first.
            self.type = segmented({k: t(v) for k, v in TX_TYPES.items()}, init.type, on_change=ch, full=True)
            with ui.column().classes("w-full gap-1"):
                with ui.row().classes("w-full gap-2 items-center no-wrap"):
                    self.amount = ui.number(
                        t("Amount"),
                        value=init.amount,
                        min=0,
                        format="%.2f",
                        on_change=ch,
                        validation={t("Must be positive"): lambda v: v is None or v > 0},
                    )
                    self.amount.props("outlined inputmode=decimal autofocus").classes("hero-amount flex-1 min-w-0")
                    self.currency = currency_select(self.ctx, init.currency, on_change=ch)
                self.base_hint = ui.label().classes("caption px-1")
            self.date = date_input(t("Date"), init.date.isoformat() if init.date else "", on_change=ch).classes(
                "w-full"
            )

            # --- expenditure
            with ui.column().classes("w-full gap-3") as self.exp_box:
                self.exp_category = expenditure_category_select(
                    init.category if init.type == TX_EXPENDITURE else None, on_change=ch
                )
                self.exp_from = self._account_select(
                    t("From Account"), init.from_account if init.type == TX_EXPENDITURE else None
                )
                self.exp_adv = ui.switch(
                    t("Partly paid for others (advance payment)"),
                    value=init.is_advance and init.type == TX_EXPENDITURE,
                    on_change=ch,
                ).props("dense")
                with ui.column().classes("w-full gap-3 subform") as self.exp_adv_box:
                    with ui.row().classes("w-full gap-2 items-center no-wrap"):
                        self.adv_value = ui.number(t("Paid for others"), value=init.advance_value, min=0, on_change=ch)
                        self.adv_value.props("dense outlined").classes("flex-1 min-w-0")
                        self.adv_mode = segmented(
                            {"percent": "%", "amount": t("Amount")}, init.advance_mode, on_change=ch
                        )
                    self.adv_hint = ui.label().classes("caption px-1 -mt-2")
                    self.exp_adv_account = (
                        ui.select({}, label=t("Advance Payment Account"), on_change=ch)
                        .props("dense outlined")
                        .classes("w-full")
                    )
                    self._init_exp_adv_account = init.advance_account if init.type == TX_EXPENDITURE else ""
                    self.debtor = (
                        ui.input(t("Debtor"), value=init.debtor if init.type == TX_EXPENDITURE else "", on_change=ch)
                        .props("dense outlined")
                        .classes("w-full")
                    )

            # --- income
            with ui.column().classes("w-full gap-3") as self.inc_box:
                self.inc_category = income_category_select(
                    self.ctx,
                    init.category if init.type == TX_INCOME and not init.is_advance else None,
                    on_change=ch,
                )
                self.inc_to = self._account_select(t("To Account"), init.to_account if init.type == TX_INCOME else None)
                self.inc_adv = ui.switch(
                    t("Repays an advance payment"), value=init.is_advance and init.type == TX_INCOME, on_change=ch
                ).props("dense")
                with ui.column().classes("w-full gap-3 subform") as self.inc_adv_box:
                    self.inc_adv_account = ui.select(
                        {}, label=t("Advance Payment Account"), value=None, on_change=self._inc_adv_account_changed
                    )
                    self.inc_adv_account.props("dense outlined").classes("w-full")
                    self.open_items = ui.select({}, label=t("Outstanding Payment"), on_change=self._open_item_changed)
                    self.open_items.props("dense outlined").classes("w-full")
                    self._init_inc_adv = (init.advance_account, init.settles_id) if init.type == TX_INCOME else ("", "")

            # --- transfer / exchange
            with ui.column().classes("w-full gap-1") as self.move_box:
                init_move = init.type in (TX_TRANSFER, TX_EXCHANGE)
                with ui.row().classes("w-full gap-2 items-center no-wrap max-sm:flex-wrap"):
                    self.move_from = self._account_select(
                        tc("account", "From"), init.from_account if init_move else None
                    )
                    ui.icon("arrow_forward", size="18px").classes("text-3 max-sm:hidden")
                    self.move_to = self._account_select(tc("account", "To"), init.to_account if init_move else None)
                self.exchange_hint = ui.label().classes("caption px-1")

            with ui.column().classes("w-full") as self.items_box:
                self.items = ItemList(
                    items_svc.known_names(self.ctx),
                    items_svc.items_for(self.ctx, self.editing.id) if self.editing else [],
                    amount=lambda: self.amount.value,
                    currency=lambda: self.currency.value or "",
                    use_total=self._use_items_total,
                )

            self.comment = (
                ui.textarea(t("Note"), value=init.comment, placeholder=t("Optional"))
                .props("dense outlined autogrow rows=1")
                .classes("w-full")
            )
            with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
                self.status = ui.label().classes("caption")
                ui.space()
                if self.on_cancel:
                    ui.button(t("Cancel"), on_click=self.on_cancel).props("flat").classes("text-primary")
                self.save_btn = ui.button(
                    t("Save Changes") if self.editing else t("Add Transaction"), on_click=self._save
                ).props("unelevated")

    def _account_select(self, label: str, value: str | None) -> ui.select:
        value = value or None
        sel = ui.select(
            {value: value} if value else {}, label=label, value=value, with_input=True, on_change=self._changed
        )
        return sel.props("dense outlined").classes("w-full flex-1 min-w-0")

    # ------------------------------------------------------------------ events
    def _use_items_total(self, total: float) -> None:
        self.amount.value = total

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
            self.status.set_text(str(exc) if self._touched or self.editing else "")
        self._update_hints(inp)
        self.items.refresh()

    # ------------------------------------------------------------- dependents
    def _refresh_options(self) -> None:
        kind, cur = self.type.value, self.currency.value
        self.exp_box.set_visibility(kind == TX_EXPENDITURE)
        self.inc_box.set_visibility(kind == TX_INCOME)
        self.move_box.set_visibility(kind in (TX_TRANSFER, TX_EXCHANGE))
        self.items_box.set_visibility(kind in (TX_EXPENDITURE, TX_INCOME))
        self.exp_adv_box.set_visibility(bool(self.exp_adv.value))
        self.inc_adv_box.set_visibility(bool(self.inc_adv.value))
        self.inc_category.set_visibility(not self.inc_adv.value)
        set_options(self.inc_category, income_category_options(self.ctx))

        set_options(self.exp_from, account_options(self.ctx, currency=cur))
        set_options(self.inc_to, account_options(self.ctx, currency=cur))

        adv_opts = {AUTO_ADVANCE: t("Advance payments (default)")} | dict(
            account_options(self.ctx, ACCOUNT_ADVANCE, cur).items()
        )
        default_adv = next(  # the stored account name, so not translated
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
                item_opts[settled.id] = f"{i18n.date_label(settled.date)} · {settled.debtor or '—'}"
            self.open_items.set_options(item_opts, value=self._init_inc_adv[1])
            self._init_inc_adv = ("", "")
        else:
            set_options(self.open_items, item_opts)

        if kind == TX_EXCHANGE:
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
                    self.base_hint.set_text(
                        t("= {amount} (ECB rate of {date})", amount=fmt(chf, "CHF"), date=i18n.date_label(fx_date))
                    )
                if inp.type == TX_EXPENDITURE and inp.is_advance:
                    share = tx_svc.advance_share(inp)
                    self.adv_hint.set_text(
                        t(
                            "Others: {others} · own share: {own}",
                            others=fmt(share, inp.currency),
                            own=fmt(inp.amount - share, inp.currency),
                        )
                    )
                if inp.type == TX_EXCHANGE and inp.to_account:
                    dst = self.ctx.store.accounts.get(inp.to_account)
                    if dst:
                        to_amt = self.ctx.fx.convert(inp.amount, inp.currency, dst.currency, fx_date)
                        self.exchange_hint.set_text(
                            t(
                                "≈ {amount} credited (ECB rate of {date})",
                                amount=fmt(to_amt, dst.currency),
                                date=i18n.date_label(fx_date),
                            )
                        )
            except FxError:  # missing rates: the hints are optional
                log.debug("No exchange rate for hint", exc_info=True)

    # ------------------------------------------------------------------ data
    def read(self) -> TransactionInput:
        kind = self.type.value
        inp = TransactionInput(
            date=parse_date(self.date.value),
            type=kind,
            amount=self.amount.value,
            currency=self.currency.value or "",
            comment=self.comment.value or "",
        )
        if kind == TX_EXPENDITURE:
            inp.category = self.exp_category.value or ""
            inp.from_account = self.exp_from.value or ""
            inp.is_advance = bool(self.exp_adv.value)
            if inp.is_advance:
                inp.advance_mode = self.adv_mode.value
                inp.advance_value = self.adv_value.value
                acc = self.exp_adv_account.value
                inp.advance_account = "" if acc in (None, AUTO_ADVANCE) else acc
                inp.debtor = self.debtor.value or ""
        elif kind == TX_INCOME:
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
        inp = self.read()
        # transfers and exchanges have no product list; switching to one drops an existing list
        item_inputs = self.items.read() if inp.type in (TX_EXPENDITURE, TX_INCOME) else []
        try:
            items_svc.validate(item_inputs)  # before saving, so a bad item cannot leave half a change
            tx = tx_svc.save(self.ctx, inp, self.editing.id if self.editing else "")
            items_svc.replace(self.ctx, tx.id, item_inputs)
        except Exception as exc:  # validation and FX errors
            error(str(exc))
            return
        success(t("Changes saved") if self.editing else t("Transaction added"))
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
            self.items.clear()
        finally:
            self._busy = False
        self._touched = False
        self._changed()
        self._touched = True


def open_transaction_sheet(
    ctx: Context, on_saved: Callable[[], None] | None = None, editing: Transaction | None = None
) -> None:
    """The transaction form as a modal sheet, so a transaction can be added from any page."""

    def saved(_: Transaction) -> None:
        dlg.close()
        if on_saved:
            on_saved()

    with sheet(t("Edit Transaction") if editing else t("New Transaction"), width="34rem") as dlg:
        TransactionForm(ctx, editing=editing, on_saved=saved, on_cancel=dlg.close)
