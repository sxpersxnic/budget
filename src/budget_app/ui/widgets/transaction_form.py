"""The transaction form, used by the 'Add transaction' page and the edit dialog.

A new expenditure or income with a payee and no category shows the suggested category (F07) under the category
field, filled in only when clicked; editing one offers "Always Categorize Like This…", which creates a rule."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from nicegui import ui
from nicegui.events import KeyEventArguments

from budget_app import i18n
from budget_app.domain.fx import FxError
from budget_app.domain.models import (
    ACCOUNT_ADVANCE,
    REVAL_INVEST,
    REVALUATION_KINDS,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    TX_TRANSFER,
    TX_TYPES,
    Template,
    Transaction,
)
from budget_app.i18n import N_, t, tc
from budget_app.services import accounts as acc_svc
from budget_app.services import categorize
from budget_app.services import items as items_svc
from budget_app.services import payees as payee_svc
from budget_app.services import rules as rules_svc
from budget_app.services import templates as templates_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    fmt_signed,
    parse_date,
    segmented,
    sheet,
    sheet_buttons,
    success,
    undoable,
)
from budget_app.ui.widgets.amount import AmountInput
from budget_app.ui.widgets.item_list import ItemList
from budget_app.ui.widgets.rule_sheet import always_like_this
from budget_app.ui.widgets.selects import (
    account_options,
    currency_select,
    expenditure_category_select,
    income_category_options,
    income_category_select,
    payee_input,
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
        prefill: TransactionInput | None = None,
        items_from: str = "",
        on_duplicate: Callable[[Transaction], None] | None = None,
    ):
        self.ctx = ctx
        self.editing = editing
        self.on_saved = on_saved
        self.on_cancel = on_cancel
        self.on_duplicate = on_duplicate
        self._items_from = items_from  # a duplicate starts with the original's product list
        self._recent = {acc: i for i, acc in enumerate(tx_svc.recent_accounts(ctx))}
        self._busy = False
        self._touched = False  # explain why Save is disabled only once the user has started
        # F07: suggestions for new transactions; the rules and the history are read once, again after each save
        self._suggester = None if editing else categorize.Suggester(ctx)
        self._suggestion: categorize.Suggestion | None = None
        self._aids: dict[str, tuple[ui.row, ui.button, ui.label]] = {}  # per type: the suggestion line
        self._rule_buttons: dict[str, ui.button] = {}  # per type, when editing: "Always Categorize Like This…"
        init = (
            tx_svc.edit_input(ctx, editing)
            if editing
            else prefill or TransactionInput(date=date.today(), currency=ctx.store.settings.display_currency)
        )
        self._build(init)
        self._changed()
        self._touched = True

    # ------------------------------------------------------------------ layout
    def _build(self, init: TransactionInput) -> None:
        ch = self._changed
        with ui.column().classes("w-full gap-4"):
            # The type decides every other field, so it comes first.
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                self.type = segmented({k: t(v) for k, v in TX_TYPES.items()}, init.type, on_change=ch, full=True)
                self.type.classes("flex-1 min-w-0")
                if not self.editing:
                    self._templates_menu()
            with ui.column().classes("w-full gap-1") as self.amount_box:
                with ui.row().classes("w-full gap-2 items-center no-wrap"):
                    self.amount = AmountInput(
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
            # who was paid or who paid (F09); transfers, exchanges and revaluations stay between own accounts
            self.payee = payee_input(self.ctx, init.payee if init.type in payee_svc.PAYEE_TYPES else "", on_change=ch)

            # --- expenditure
            with ui.column().classes("w-full gap-3") as self.exp_box:
                self.exp_category = expenditure_category_select(
                    init.category if init.type == TX_EXPENDITURE else None, on_change=ch, ctx=self.ctx
                )
                self._category_aids(TX_EXPENDITURE)
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
                self._category_aids(TX_INCOME)
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
                self.to_amount = AmountInput(
                    t("Amount Received"),
                    value=init.to_amount if init.type == TX_EXCHANGE else None,
                    min=0,
                    format="%.2f",
                    on_change=ch,
                    validation={t("Must be positive"): lambda v: v is None or v > 0},
                )
                self.to_amount.props("outlined inputmode=decimal").classes("w-full mt-2")
                self.exchange_hint = ui.label().classes("caption px-1 whitespace-pre-line")

            # --- revaluation (F43)
            with ui.column().classes("w-full gap-3") as self.reval_box:
                is_reval = init.type == TX_REVALUATION
                self.reval_account = self._account_select(t("Account"), init.reval_account if is_reval else None)
                self.reval_kind = (
                    ui.select(
                        {k: t(v) for k, v in REVALUATION_KINDS.items()},
                        value=init.category if is_reval and init.category in REVALUATION_KINDS else REVAL_INVEST,
                        label=t("Kind"),
                        on_change=ch,
                    )
                    .props("dense outlined")
                    .classes("w-full")
                )
                self.reval_mode = segmented(
                    {"difference": t("Change"), "values": t("Old and New Value")},
                    init.reval_mode if is_reval else "difference",
                    on_change=ch,
                    full=True,
                )
                self.reval_change = ui.number(
                    t("Change in value"), value=init.reval_change if is_reval else None, format="%.2f", on_change=ch
                )
                self.reval_change.props("outlined inputmode=decimal").classes("w-full")
                with ui.row().classes("w-full gap-2 no-wrap") as self.reval_values:
                    self.reval_old = ui.number(
                        t("Old value"), value=init.reval_old if is_reval else None, format="%.2f", on_change=ch
                    )
                    self.reval_old.props("dense outlined inputmode=decimal").classes("flex-1 min-w-0")
                    self.reval_new = ui.number(
                        t("New value"), value=init.reval_new if is_reval else None, format="%.2f", on_change=ch
                    )
                    self.reval_new.props("dense outlined inputmode=decimal").classes("flex-1 min-w-0")
                self.reval_hint = ui.label().classes("caption px-1")

            with ui.column().classes("w-full") as self.items_box:
                self.items = ItemList(
                    items_svc.known_names(self.ctx),
                    items_svc.items_for(self.ctx, self.editing.id if self.editing else self._items_from)
                    if self.editing or self._items_from
                    else [],
                    amount=lambda: self.amount.value,
                    currency=lambda: self.currency.value or "",
                    use_total=self._use_items_total,
                )

            self.comment = (
                ui.textarea(t("Note"), value=init.comment, placeholder=t("Optional"), on_change=ch)  # rules read it
                .props("dense outlined autogrow rows=1")
                .classes("w-full")
            )
            with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
                self.status = ui.label().classes("caption")
                ui.space()
                if self.editing and self.on_duplicate:
                    editing = self.editing
                    ui.button(t("Duplicate"), icon="o_content_copy", on_click=lambda: self.on_duplicate(editing)).props(
                        "flat"
                    ).classes("text-primary")
                if self.on_cancel:
                    ui.button(t("Cancel"), on_click=self.on_cancel).props("flat").classes("text-primary")
                self.save_btn = ui.button(
                    t("Save Changes") if self.editing else t("Add Transaction"), on_click=self._save
                ).props("unelevated")
            ui.keyboard(on_key=self._shortcut, ignore=[])

    def _category_aids(self, kind: str) -> None:
        """Under the category field: the suggestion of a new transaction, or "Always Categorize Like This…"."""
        if self.editing:
            self._rule_buttons[kind] = (
                ui.button(t("Always Categorize Like This…"), icon="o_rule", on_click=self._make_rule)
                .props("flat dense no-caps size=13px")
                .classes("link-btn self-start -mt-2")
            )
            return
        with ui.row().classes("w-full items-center gap-x-2 gap-y-0 -mt-2 px-1") as row:
            use = (
                ui.button(icon="o_auto_awesome", on_click=self._use_suggestion)
                .props("flat dense no-caps size=13px")
                .classes("link-btn")
            )
            reason = ui.label().classes("caption")
        self._aids[kind] = (row, use, reason)

    def _recent_first(self, options: dict[str, str]) -> dict[str, str]:
        """Recently used accounts at the top of a dropdown (F06), the others in their usual order."""
        order = sorted(options, key=lambda k: (self._recent.get(k, len(self._recent)), list(options).index(k)))
        return {k: options[k] for k in order}

    def _shortcut(self, e: KeyEventArguments) -> None:
        """⌘↩ / Ctrl+Enter saves, from any field."""
        if e.action.keydown and e.key.enter and (e.modifiers.meta or e.modifiers.ctrl) and self.save_btn.enabled:
            self._save()

    def _templates_menu(self) -> None:
        """Start from a saved template, or save the current fields as one (F06)."""
        button = ui.button(icon="o_bookmarks").props("flat round dense").classes("text-primary")
        with button.tooltip(t("Templates")), ui.menu().props("auto-close") as menu:
            self._template_menu = menu
            self._fill_templates_menu()

    def _fill_templates_menu(self) -> None:
        self._template_menu.clear()
        with self._template_menu:
            templates = templates_svc.all_templates(self.ctx)
            for tp in templates:
                with (
                    ui.menu_item(on_click=lambda tp=tp: self._apply_template(tp)),
                    ui.row().classes("items-center gap-2 no-wrap w-full"),
                ):
                    ui.label(tp.name).classes("flex-1")
                    # .stop: deleting must not also apply the template underneath
                    ui.button(icon="close").props("flat round dense size=xs").classes("text-2").tooltip(t("Delete")).on(
                        "click.stop", lambda tp=tp: self._delete_template(tp)
                    )
            if templates:
                ui.separator()
            ui.menu_item(t("Save as Template…"), on_click=self._save_template)

    def _apply_template(self, tp: Template) -> None:
        inp = templates_svc.as_input(tp, self.read())
        self._busy = True
        try:
            self.type.set_value(inp.type)
            self.amount.set_value(inp.amount)
            self.currency.set_value(inp.currency)
            self._refresh_options()
            if inp.type == TX_EXPENDITURE:
                self.exp_category.set_value(inp.category or None)
                self.exp_from.set_value(inp.from_account or None)
            elif inp.type == TX_INCOME:
                self.inc_category.set_value(inp.category or None)
                self.inc_to.set_value(inp.to_account or None)
            else:
                self.move_from.set_value(inp.from_account or None)
                self.move_to.set_value(inp.to_account or None)
            self.comment.set_value(inp.comment)
            self.payee.set_value(inp.payee)
        finally:
            self._busy = False
        self._changed()

    def _delete_template(self, tp: Template) -> None:
        templates_svc.delete(self.ctx, tp.id)
        self._fill_templates_menu()

    def _save_template(self) -> None:
        with sheet(t("Save as Template"), width="24rem") as dlg:
            name = ui.input(t("Name"), placeholder=t("e.g. Coffee")).props("dense outlined autofocus").classes("w-full")

            def save() -> None:
                try:
                    templates_svc.save(self.ctx, name.value, self.read())
                except ValidationError as exc:
                    error(str(exc))
                    return
                dlg.close()
                success(t("Saved “{name}”", name=name.value.strip()))
                self._fill_templates_menu()

            name.on("keydown.enter", save)
            sheet_buttons(dlg, t("Save"), save)

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
        self._update_suggestion(inp)
        self.items.refresh()

    # ------------------------------------------------------------- dependents
    def _refresh_options(self) -> None:
        kind, cur = self.type.value, self.currency.value
        self.exp_box.set_visibility(kind == TX_EXPENDITURE)
        self.inc_box.set_visibility(kind == TX_INCOME)
        self.move_box.set_visibility(kind in (TX_TRANSFER, TX_EXCHANGE))
        self.to_amount.set_visibility(kind == TX_EXCHANGE)
        self.amount_box.set_visibility(kind != TX_REVALUATION)  # a revaluation is a change, signed
        self.reval_box.set_visibility(kind == TX_REVALUATION)
        values = self.reval_mode.value == "values"
        self.reval_change.set_visibility(not values)
        self.reval_values.set_visibility(values)
        self.items_box.set_visibility(kind in (TX_EXPENDITURE, TX_INCOME))
        self.payee.set_visibility(kind in payee_svc.PAYEE_TYPES)
        self.exp_adv_box.set_visibility(bool(self.exp_adv.value))
        self.inc_adv_box.set_visibility(bool(self.inc_adv.value))
        self.inc_category.set_visibility(not self.inc_adv.value)
        set_options(self.inc_category, income_category_options(self.ctx))

        set_options(self.exp_from, self._recent_first(account_options(self.ctx, currency=cur)))
        set_options(self.inc_to, self._recent_first(account_options(self.ctx, currency=cur)))

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

        if kind == TX_REVALUATION:
            set_options(self.reval_account, self._recent_first(account_options(self.ctx)))
            acc = self.ctx.store.accounts.get(self.reval_account.value) if self.reval_account.value else None
            if acc and self.currency.value != acc.currency:
                self.currency.set_value(acc.currency)
            suffix = f'suffix="{acc.currency}"' if acc else "remove=suffix"
            for el in (self.reval_change, self.reval_old, self.reval_new):
                el.props(suffix)
        if kind == TX_EXCHANGE:
            set_options(self.move_from, self._recent_first(account_options(self.ctx)))
            src = self.ctx.store.accounts.get(self.move_from.value) if self.move_from.value else None
            if src and self.currency.value != src.currency:
                self.currency.set_value(src.currency)
            set_options(
                self.move_to,
                self._recent_first(account_options(self.ctx, exclude_currency=src.currency if src else None)),
            )
            self.currency.disable()
            dst = self.ctx.store.accounts.get(self.move_to.value) if self.move_to.value else None
            self.to_amount.props(f'suffix="{dst.currency}"' if dst else "remove=suffix")
        else:
            self.currency.enable()
            set_options(self.move_from, self._recent_first(account_options(self.ctx, currency=cur)))
            set_options(self.move_to, self._recent_first(account_options(self.ctx, currency=cur)))

    def _account_field(self, kind: str) -> ui.select:
        return self.exp_from if kind == TX_EXPENDITURE else self.inc_to

    def _update_suggestion(self, inp: TransactionInput) -> None:
        """Show the suggested category (F07) when a new expenditure or income has a payee but no category yet."""
        kind = inp.type
        has_category = bool(inp.category) or inp.is_advance  # a repayment has no category of its own
        for k, button in self._rule_buttons.items():
            button.set_visibility(k == kind and has_category)
        self._suggestion = None
        if self._suggester is not None and kind in self._aids and inp.payee.strip() and not has_category:
            account = self._account_field(kind).value or None  # an account already chosen stays
            self._suggestion = self._suggester.suggest(kind, inp.payee, inp.comment, account=account)
        for k, (row, use, reason) in self._aids.items():
            hit = self._suggestion if k == kind else None
            row.set_visibility(hit is not None)
            if hit is None:
                continue
            use.set_text(t("Use {category}", category=rules_svc.category_label(self.ctx, kind, hit.category)))
            fills = self._fills_account(hit)
            reason.set_text(
                f"{hit.reason} · {acc_svc.account_label(self.ctx.store.accounts.get(fills))}" if fills else hit.reason
            )

    def _fills_account(self, hit: categorize.Suggestion) -> str:
        """The suggested account if it fits the currency and no account is chosen yet, else ""."""
        field = self._account_field(self.type.value)
        return hit.account if hit.account and hit.account in field.options and not field.value else ""

    def _use_suggestion(self) -> None:
        """Fill in the suggested category, and the suggested account if the account field is still empty."""
        hit, kind = self._suggestion, self.type.value
        if hit is None:
            return
        self._busy = True
        try:
            (self.exp_category if kind == TX_EXPENDITURE else self.inc_category).set_value(hit.category)
            if account := self._fills_account(hit):
                self._account_field(kind).set_value(account)
        finally:
            self._busy = False
        self._changed()

    def _make_rule(self) -> None:
        """A rule that categorizes transactions like this one from now on (F07)."""
        inp = self.read()
        always_like_this(self.ctx, inp.type, inp.category, inp.payee, inp.comment)

    def _update_hints(self, inp: TransactionInput) -> None:
        self.reval_hint.set_text("")
        if inp.type == TX_REVALUATION:
            change = tx_svc.revaluation_change(inp)
            if change:
                self.reval_hint.set_text(
                    t("A gain of {amount}", amount=fmt(change, inp.currency))
                    if change > 0
                    else t("A loss of {amount}", amount=fmt(-change, inp.currency))
                )
        self.base_hint.set_text("")
        self.adv_hint.set_text("")
        self.exchange_hint.set_text("")
        if inp.amount and inp.currency:
            try:
                chf, fx_date = self.ctx.to_base_on(inp.amount, inp.currency, inp.date or date.today())
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
                        self.exchange_hint.set_text(self._exchange_hint(inp, dst.currency, fx_date))
            except FxError:  # missing rates: the hints are optional
                log.debug("No exchange rate for hint", exc_info=True)

    def _exchange_hint(self, inp: TransactionInput, to_cur: str, fx_date: date) -> str:
        """The ECB rate next to the bank's, so a typing error or a costly exchange stands out."""
        amount = inp.amount
        if not amount:
            return ""
        ecb_amount = self.ctx.fx.convert(amount, inp.currency, to_cur, fx_date)

        def rate(received: float) -> str:
            return f"1 {inp.currency} = {received / amount:.4f} {to_cur}"

        lines = [
            t(
                "ECB rate of {date}: {rate} → {amount}",
                date=i18n.date_label(fx_date),
                rate=rate(ecb_amount),
                amount=fmt(ecb_amount, to_cur),
            )
        ]
        if inp.to_amount:
            lines.append(
                t(
                    "Rate received: {rate} · {difference} against the ECB rate",
                    rate=rate(inp.to_amount),
                    difference=f"{fmt_signed(inp.to_amount - ecb_amount) or fmt(0)} {to_cur}",
                )
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ data
    def read(self) -> TransactionInput:
        kind = self.type.value
        inp = TransactionInput(
            date=parse_date(self.date.value),
            type=kind,
            amount=self.amount.value,
            currency=self.currency.value or "",
            comment=self.comment.value or "",
            payee=(self.payee.value or "") if kind in payee_svc.PAYEE_TYPES else "",
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
        elif kind == TX_REVALUATION:
            inp.reval_account = self.reval_account.value or ""
            inp.category = self.reval_kind.value or ""
            inp.reval_mode = self.reval_mode.value
            inp.reval_change = self.reval_change.value
            inp.reval_old, inp.reval_new = self.reval_old.value, self.reval_new.value
            change = tx_svc.revaluation_change(inp)
            inp.amount = abs(change) if change else None
        else:
            inp.from_account = self.move_from.value or ""
            inp.to_account = self.move_to.value or ""
            if kind == TX_EXCHANGE:
                inp.to_amount = self.to_amount.value
        return inp

    def _save(self) -> None:
        inp = self.read()
        # transfers and exchanges have no product list; switching to one drops an existing list
        item_inputs = self.items.read() if inp.type in (TX_EXPENDITURE, TX_INCOME) else []
        try:
            items_svc.validate(item_inputs)  # before saving, so a bad item cannot leave half a change
            with self.ctx.store.history.action(N_("Save a transaction")):  # one step to undo, items included
                tx = tx_svc.save(self.ctx, inp, self.editing.id if self.editing else "")
                items_svc.replace(self.ctx, tx.id, item_inputs)
        except Exception as exc:  # validation and FX errors
            error(str(exc))
            return
        undoable(t("Changes saved") if self.editing else t("Transaction added"))
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
                self.to_amount,
                self.reval_account,
                self.reval_change,
                self.reval_old,
                self.reval_new,
            ):
                el.set_value(None)
            self.exp_adv.set_value(False)
            self.inc_adv.set_value(False)
            self.debtor.set_value("")
            self.comment.set_value("")
            self.payee.set_value("")
            self.payee.set_autocomplete(payee_svc.names(self.ctx))  # with the payee just saved
            self._suggester = categorize.Suggester(self.ctx)  # and with what it was categorized as
            self.exp_adv_account.set_value(None)
            self.items.clear()
        finally:
            self._busy = False
        self._touched = False
        self._changed()
        self._touched = True


def open_transaction_sheet(
    ctx: Context,
    on_saved: Callable[[], None] | None = None,
    editing: Transaction | None = None,
    prefill: TransactionInput | None = None,
    items_from: str = "",
) -> None:
    """The transaction form as a modal sheet, so a transaction can be added from any page."""
    if editing is not None and editing.type == TX_REVALUATION and editing.linked_id:
        editing = ctx.store.transactions.get(editing.linked_id) or editing  # a spread is edited via its exchange

    def saved(_: Transaction) -> None:
        dlg.close()
        if on_saved:
            on_saved()

    def duplicate(tx: Transaction) -> None:
        dlg.close()
        open_transaction_sheet(ctx, on_saved, prefill=tx_svc.duplicate_input(ctx, tx, date.today()), items_from=tx.id)

    with sheet(t("Edit Transaction") if editing else t("New Transaction"), width="34rem") as dlg:
        TransactionForm(
            ctx,
            editing=editing,
            on_saved=saved,
            on_cancel=dlg.close,
            prefill=prefill,
            items_from=items_from,
            on_duplicate=duplicate,
        )
