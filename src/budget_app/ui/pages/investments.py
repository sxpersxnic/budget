"""Investments (F21): holdings by account with their value and gain, a holding's history, and the sheets to buy,
sell, book a dividend, update a price, paste a price list and add a holding. Every save is one undoable step."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.domain import months
from budget_app.domain.fx import FxError
from budget_app.domain.investments import PRICE, Performance, Step, default_withholding, fmt_price, fmt_quantity
from budget_app.domain.models import (
    ASSET_CLASSES,
    TRADE_BUY,
    TRADE_DIVIDEND,
    TRADE_KINDS,
    TRADE_OPENING,
    TRADE_SELL,
    Account,
    Holding,
    Price,
    Trade,
    Transaction,
)
from budget_app.i18n import N_, t, tc, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import investments as inv_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.investments import TradeInput
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    fmt_signed,
    frame,
    metric,
    month_options,
    named,
    on_data_changed,
    parse_date,
    segmented,
    sheet,
    sheet_buttons,
    undoable,
)
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import account_options, set_options

ROW_ACTION = "flat dense round size=sm"
GAIN_NOTE = N_(
    "Gain = value − money put in (buys − sales) + net dividends − fees, and in percent of the money put in. "
    "Prices are the ones you enter or paste; a buy or sale counts as the price of its day."
)

Refresh = Callable[[], object] | None


def _done(on_saved: Refresh) -> None:
    if on_saved:
        on_saved()


# ------------------------------------------------------------------ helpers
def holding_options(ctx: Context) -> dict[str, str]:
    """Every holding, labelled with its account, grouped by account."""
    out = {}
    for acc in inv_svc.investment_accounts(ctx):
        for h in inv_svc.holdings(ctx, acc.id):
            out[h.id] = f"{inv_svc.holding_label(h)} · {acc.name}"
    return out


def _account_name(ctx: Context, account_id: str) -> str:
    acc = ctx.store.accounts.get(account_id)
    return acc.name if acc else ""


def _gain_text(perf: Performance, currency: str) -> str:
    gain = (fmt_signed(perf.gain) or fmt(0)) + f" {currency}"
    return f"{gain} · {fmt_signed(perf.pct) or '0.0'} %" if perf.pct is not None else gain


def _gain_class(value: float) -> str:
    return "text-pos" if value >= 0.005 else "text-neg" if value <= -0.005 else "text-2"


def _position(perf: Performance, currency: str) -> str:
    """12 × 95.50 CHF on 21 Oct 2026."""
    if perf.price is None:
        return t("No price yet")
    return f"{fmt_quantity(perf.quantity)} × {_last_price(perf, currency)}"


def _last_price(perf: Performance, currency: str) -> str:
    """95.50 CHF on 21 Oct 2026."""
    if perf.price is None or perf.price_date is None:
        return "–"
    price = f"{fmt_price(perf.price)} {currency}"
    return t("{position} on {date}", position=price, date=i18n.date_label(perf.price_date))


def _step_title(step: Step, currency: str) -> str:
    e = step.event
    if e.kind == PRICE:
        return t("Price {price}", price=f"{fmt_price(e.price)} {currency}")
    if e.kind == TRADE_DIVIDEND:
        return t("Dividend {amount}", amount=fmt(e.amount, currency))
    what = f"{fmt_quantity(e.quantity)} × {fmt_price(e.price)}"
    if e.kind == TRADE_OPENING:
        return t("Opening holding {what}", what=what)
    return t("Buy {what}", what=what) if e.kind == TRADE_BUY else t("Sell {what}", what=what)


def tax_label(currency: str) -> str:
    """ "Withholding tax": the Swiss one (Verrechnungssteuer) in CHF, a foreign one (Quellensteuer) otherwise."""
    return t("Withholding tax") if currency == "CHF" else tc("foreign", "Withholding tax")


def withholding_text(amount: float, currency: str) -> str:
    """The withholding tax of a dividend: Swiss (reclaimable) in CHF, foreign otherwise."""
    if currency == "CHF":
        return t("withholding tax {amount}, reclaimable", amount=fmt(amount, currency))
    return t("foreign withholding tax {amount}", amount=fmt(amount, currency))


def _step_caption(ctx: Context, step: Step, trade: Trade | None, currency: str) -> str:
    parts = [i18n.date_label(step.event.date)]
    if trade is not None:
        acc = ctx.store.accounts.get(trade.cash_account)
        if acc:
            parts.append(acc.name)
        if trade.fee >= 0.005:
            parts.append(t("fee {amount}", amount=fmt(trade.fee, currency)))
        if trade.kind == TRADE_DIVIDEND and trade.tax >= 0.005:
            parts.append(withholding_text(trade.tax, currency))
            parts.append(t("net {amount}", amount=fmt(trade.amount - trade.tax, currency)))
        if trade.comment:
            parts.append(trade.comment)
    return " · ".join(parts)


# ------------------------------------------------------------------ the holding sheet (add, rename)
def open_holding_form(
    ctx: Context, on_saved: Refresh = None, account_id: str = "", editing: Holding | None = None
) -> None:
    accounts = {a.id: acc_svc.account_label(a) for a in inv_svc.investment_accounts(ctx)}
    if not accounts:
        error(t("Give an account the asset class Securities or Crypto in Settings first."))
        return
    first = account_id or (editing.account_id if editing else next(iter(accounts)))
    with sheet(t("Edit Holding") if editing else t("New Holding"), width="26rem") as dlg:
        acc = ui.select(accounts, value=first if first in accounts else None, label=t("Account"))
        acc.props("dense outlined options-dense").classes("w-full")
        if editing and (inv_svc.trades_of(ctx, editing.id) or inv_svc.prices_of(ctx, editing.id)):
            acc.disable()
        name = ui.input(t("Name"), value=editing.name if editing else "").props("dense outlined autofocus")
        name.classes("w-full")
        symbol = ui.input(t("ISIN or symbol"), value=editing.symbol if editing else "", placeholder=t("Optional"))
        symbol.props("dense outlined").classes("w-full")
        ui.label(t("A price list finds the holding by its name, ISIN or symbol.")).classes("caption -mt-2 px-1")

        def save() -> None:
            try:
                if editing:
                    inv_svc.edit_holding(ctx, editing.id, name.value, symbol.value, acc.value or "")
                else:
                    inv_svc.add_holding(ctx, acc.value or "", name.value, symbol.value)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(t("Changes saved") if editing else t("Added {name}", name=(name.value or "").strip()))
            dlg.close()
            _done(on_saved)

        name.on("keydown.enter", save)
        sheet_buttons(dlg, t("Save") if editing else t("Add"), save)


# ------------------------------------------------------------------ the trade sheet (buy, sell, dividend)
def open_trade_sheet(
    ctx: Context,
    on_saved: Refresh = None,
    kind: str = TRADE_BUY,
    holding_id: str = "",
    editing: Trade | None = None,
) -> None:
    options = holding_options(ctx)
    if not options:
        error(t("Add a holding first."))
        return
    init = inv_svc.to_input(editing) if editing else TradeInput(kind=kind, holding_id=holding_id, date=date.today())
    if not init.holding_id and len(options) == 1:
        init.holding_id = next(iter(options))
    touched = {"value": False}
    # a new dividend's withholding tax follows the gross amount (35 % in CHF) until it is typed in
    auto_tax = {"on": editing is None, "setting": False}
    # an opening holding of a converted account (decision 13): only its quantity and price change
    opening = editing is not None and editing.kind == TRADE_OPENING
    title = t("Edit Opening Holding") if opening else t("Edit Trade") if editing else t("New Trade")
    with sheet(title, width="30rem") as dlg, ui.column().classes("w-full gap-4"):
        kinds = {TRADE_OPENING: t("Opening holding")} if opening else {k: t(v) for k, v in TRADE_KINDS.items()}
        typ = segmented(kinds, init.kind, full=True)
        typ.set_visibility(not opening)
        holding = ui.select(options, value=init.holding_id or None, label=t("Holding"), with_input=True)
        holding.props("dense outlined").classes("w-full")
        when = date_input(t("Date"), init.date.isoformat() if init.date else "").classes("w-full")
        if opening:
            holding.disable()
            when.disable()
            ui.label(
                t(
                    "The holding the account started with on the day of its conversion. The difference between its "
                    "value and the last recorded balance is booked as Capital gains on investment."
                )
            ).classes("caption -mt-2 px-1")
        with ui.row().classes("w-full gap-2 no-wrap") as trade_row:
            quantity = ui.number(t("Quantity"), value=init.quantity, min=0, precision=6)
            quantity.props("dense outlined inputmode=decimal").classes("flex-1 min-w-0")
            price = ui.number(t("Price"), value=init.price, min=0, precision=6)
            price.props("dense outlined inputmode=decimal").classes("flex-1 min-w-0")
        fee = ui.number(t("Fee"), value=init.fee, min=0, format="%.2f", placeholder=t("Optional"))
        fee.props("dense outlined inputmode=decimal").classes("w-full")
        with ui.row().classes("w-full gap-2 no-wrap") as dividend_row:
            amount = ui.number(t("Gross dividend"), value=init.amount, min=0, format="%.2f")
            amount.props("dense outlined inputmode=decimal").classes("flex-1 min-w-0")
            tax = ui.number(t("Withholding tax"), value=init.tax, min=0, format="%.2f")
            tax.props("dense outlined inputmode=decimal").classes("flex-1 min-w-0")
        cash = ui.select({}, label=t("Cash account"), with_input=True).props("dense outlined").classes("w-full")
        comment = ui.textarea(t("Note"), value=init.comment, placeholder=t("Optional"))
        comment.props("dense outlined autogrow rows=1").classes("w-full")
        summary = ui.label().classes("caption px-1")
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            if editing:
                ui.button(t("Delete"), on_click=lambda: delete()).props("flat").classes("text-negative")  # noqa: PLW0108 - defined below
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Save Changes") if editing else t("Save Trade")).props("unelevated")

    def current_holding() -> Holding | None:
        return ctx.store.holdings.get(holding.value) if holding.value else None

    def read() -> TradeInput:
        return TradeInput(
            kind=typ.value,
            holding_id=holding.value or "",
            date=parse_date(when.value),
            quantity=quantity.value,
            price=price.value,
            fee=fee.value,
            amount=amount.value,
            tax=tax.value,
            cash_account=cash.value or "",
            comment=comment.value or "",
        )

    def describe(inp: TradeInput, h: Holding, cur: str) -> str:
        """What a valid trade books, in a sentence."""
        cash_name, depot_name = (_account_name(ctx, a) for a in (inp.cash_account, h.account_id))
        if opening:
            value = fmt(round((inp.quantity or 0) * (inp.price or 0), 2), cur)
            return t("Worth {amount} on the day of the conversion", amount=value)
        if inp.kind == TRADE_DIVIDEND:
            gross, withheld = round(inp.amount or 0, 2), round(inp.tax or 0, 2)
            text = t("Net {amount} to {account}", amount=fmt(gross - withheld, cur), account=cash_name)
            if withheld < 0.005:
                return text + " · " + t("income in Dividends")
            details = t("income {gross} in Dividends, {tax} under Taxes", gross=fmt(gross, cur), tax=fmt(withheld, cur))
            return f"{text} · {details}"
        moved = fmt(round((inp.quantity or 0) * (inp.price or 0), 2), cur)
        ends = (cash_name, depot_name) if inp.kind == TRADE_BUY else (depot_name, cash_name)
        text = t("Moves {amount} from {source} to {target}", amount=moved, source=ends[0], target=ends[1])
        if (inp.fee or 0) >= 0.005:
            text += " · " + t(
                "the fee of {amount} is spending from {account}", amount=fmt(inp.fee, cur), account=cash_name
            )
        return text

    def changed() -> None:
        kind = typ.value
        dividend = kind == TRADE_DIVIDEND
        trade_row.set_visibility(not dividend)
        fee.set_visibility(not dividend and not opening)
        cash.set_visibility(not opening)
        dividend_row.set_visibility(dividend)
        h = current_holding()
        cur = inv_svc.currency_of(ctx, h) if h else ""
        for el in (price, fee, amount, tax):
            el.props(f'suffix="{cur}"')
        tax.props(f'label="{tax_label(cur)}"')
        if dividend and auto_tax["on"]:
            prefill = default_withholding(amount.value, cur)
            if tax.value != prefill:
                auto_tax["setting"] = True
                tax.value = prefill  # comes back here through on_value_change
                auto_tax["setting"] = False
                return
        set_options(cash, account_options(ctx, currency=cur) if cur else {})
        if cash.value is None and cur:
            cash.value = inv_svc.last_cash_account(ctx, cur) or (
                next(iter(cash.options)) if len(cash.options) == 1 else None
            )
        inp = read()
        try:
            inv_svc.validate_trade(ctx, inp, editing.id if editing else "")
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if touched["value"] or editing else "")
            summary.set_text("")
            return
        save_btn.enable()
        status.set_text("")
        summary.set_text(describe(inp, h, cur) if h else "")

    def save() -> None:
        try:
            inv_svc.save_trade(ctx, read(), editing.id if editing else "")
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        undoable(t("Changes saved") if editing else t("Trade saved"))
        dlg.close()
        _done(on_saved)

    def delete() -> None:
        def do() -> None:
            if editing is None:  # pragma: no cover - the button exists only when editing
                return
            try:
                inv_svc.delete_trade(ctx, editing.id)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(t("Trade deleted"))
            dlg.close()
            _done(on_saved)

        confirm(t("Delete this trade?"), do, detail=t("The transactions it booked are deleted with it."))

    def tax_typed() -> None:
        if not auto_tax["setting"]:
            auto_tax["on"] = False  # typed in: it no longer follows the gross amount
        changed()

    save_btn.on_click(save)
    for el in (typ, holding, when, quantity, price, fee, amount, cash):
        el.on_value_change(changed)
    tax.on_value_change(tax_typed)
    changed()
    touched["value"] = True


# ------------------------------------------------------------------ the price sheet
def open_price_sheet(
    ctx: Context, on_saved: Refresh = None, holding_id: str = "", editing: Price | None = None
) -> None:
    options = holding_options(ctx)
    if not options:
        error(t("Add a holding first."))
        return
    start = editing.holding_id if editing else holding_id or (next(iter(options)) if len(options) == 1 else "")
    touched = {"value": False}
    with (
        sheet(t("Edit Price") if editing else t("Update Price"), width="28rem") as dlg,
        ui.column().classes("w-full gap-4"),
    ):
        holding = ui.select(options, value=start or None, label=t("Holding"), with_input=True)
        holding.props("dense outlined").classes("w-full")
        with ui.row().classes("w-full gap-2 no-wrap"):
            when = date_input(t("Date"), (editing.date if editing else date.today()).isoformat())
            when.classes("flex-1 min-w-0")
            price = ui.number(t("Price"), value=editing.price if editing else None, min=0, precision=6)
            price.props("dense outlined inputmode=decimal autofocus").classes("flex-1 min-w-0")
        effect = ui.label().classes("caption px-1")
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            if editing:
                ui.button(t("Delete"), on_click=lambda: delete()).props("flat").classes("text-negative")  # noqa: PLW0108 - defined below
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Save Price")).props("unelevated")

    def changed() -> None:
        h = ctx.store.holdings.get(holding.value) if holding.value else None
        cur = inv_svc.currency_of(ctx, h) if h else ""
        price.props(f'suffix="{cur}"')
        day = parse_date(when.value)
        try:
            inv_svc.validate_price(ctx, holding.value or "", day, price.value)
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if touched["value"] or editing else "")
            effect.set_text("")
            return
        save_btn.enable()
        status.set_text("")
        if h is None or day is None or price.value is None:  # pragma: no cover - checked by validate_price
            return
        step = inv_svc.price_effect(ctx, h.id, day, price.value, editing.id if editing else "")
        parts = []
        same_day = inv_svc.price_on(ctx, h.id, day)
        if same_day is not None and (editing is None or same_day.id != editing.id):
            parts.append(t("Replaces the price of {price} recorded for that day.", price=fmt_price(same_day.price)))
        if step is not None and step.held_after > 0:
            parts.append(
                t(
                    "{quantity} held: worth {value}, a change of {change}.",
                    quantity=fmt_quantity(step.held_after),
                    value=fmt(step.value, cur),
                    change=(fmt_signed(step.revaluation) or fmt(0)) + f" {cur}",
                )
            )
        else:
            parts.append(t("Nothing is held on that day; the price counts from then on."))
        effect.set_text(" ".join(parts))

    def save() -> None:
        try:
            inv_svc.save_price(
                ctx, holding.value or "", parse_date(when.value), price.value, editing.id if editing else ""
            )
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        undoable(t("Price saved"))
        dlg.close()
        _done(on_saved)

    def delete() -> None:
        def do() -> None:
            if editing is None:  # pragma: no cover - the button exists only when editing
                return
            try:
                inv_svc.delete_price(ctx, editing.id)
            except ValidationError as exc:
                error(str(exc))
                return
            undoable(t("Price deleted"))
            dlg.close()
            _done(on_saved)

        confirm(t("Delete this price?"), do, detail=t("The gain or loss it booked is removed too."))

    save_btn.on_click(save)
    for el in (holding, when, price):
        el.on_value_change(changed)
    changed()
    touched["value"] = True


# ------------------------------------------------------------------ pasting a price list
def open_paste_sheet(ctx: Context, on_saved: Refresh = None) -> None:
    if not inv_svc.holdings(ctx):
        error(t("Add a holding first."))
        return
    state: dict[str, list[inv_svc.PriceLine]] = {"lines": []}
    with sheet(t("Paste Prices"), width="36rem") as dlg, ui.column().classes("w-full gap-3"):
        ui.label(
            t(
                "One line per price: date, holding (name, ISIN or symbol) and price, separated by tabs, "
                "semicolons or commas, as copied from a bank or broker price list. A price already recorded "
                "for that day is replaced."
            )
        ).classes("caption px-1")
        text = ui.textarea(placeholder="2026-10-20\tCH0038863350\t97.40")
        text.props("dense outlined rows=6 debounce=300").classes("w-full font-mono")

        @ui.refreshable
        def preview() -> None:
            lines = state["lines"]
            if not lines:
                return
            with ui.element("div").classes("group"):
                for pl in lines:
                    with ui.element("div").classes("group-row"):
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            if pl.ok and pl.date is not None and pl.price is not None:
                                h = ctx.store.holdings.get(pl.holding_id)
                                ui.label(h.name if h else "").classes("truncate")
                                ui.label(i18n.date_label(pl.date)).classes("caption")
                            else:
                                ui.label(pl.text).classes("truncate text-2")
                                ui.label(t("Line {n}: {error}", n=pl.line, error=pl.error)).classes("caption text-neg")
                        if pl.ok and pl.price is not None:
                            with ui.column().classes("gap-0 items-end"):
                                ui.label(fmt_price(pl.price)).classes("num")
                                ui.label(
                                    t("replaces {price}", price=fmt_price(pl.replaces))
                                    if pl.replaces is not None
                                    else t("new")
                                ).classes("caption")

        preview()
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Save Prices")).props("unelevated")

    def changed() -> None:
        state["lines"] = inv_svc.read_price_list(ctx, text.value or "")
        good = sum(1 for pl in state["lines"] if pl.ok)
        bad = len(state["lines"]) - good
        save_btn.set_text(tn("Save {n} Price", "Save {n} Prices", good) if good else t("Save Prices"))
        save_btn.set_enabled(good > 0)
        status.set_text(tn("{n} line is left out", "{n} lines are left out", bad) if bad else "")
        preview.refresh()

    def save() -> None:
        try:
            n = inv_svc.save_prices(ctx, state["lines"])
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        undoable(tn("Saved {n} price", "Saved {n} prices", n))
        dlg.close()
        _done(on_saved)

    save_btn.on_click(save)
    text.on_value_change(changed)
    changed()


# ------------------------------------------------------------------ a holding's detail and history
def open_holding_sheet(ctx: Context, holding_id: str, on_change: Refresh = None) -> None:
    holding = ctx.store.holdings.get(holding_id)
    if holding is None:
        return
    with sheet(holding.name, width="36rem") as dlg:

        def changed() -> None:
            content.refresh()
            _done(on_change)

        @ui.refreshable
        def content() -> None:
            h = ctx.store.holdings.get(holding_id)
            if h is None:
                dlg.close()
                return
            acc = ctx.store.accounts.get(h.account_id)
            cur = acc.currency if acc else ""
            perf = inv_svc.performance(ctx, h.id)
            ui.label(" · ".join(p for p in (h.symbol, acc_svc.account_label(acc)) if p)).classes("caption -mt-2")
            with ui.element("div").classes("group shrink-0"):  # the sheet scrolls, its lists don't shrink
                for label, value in (
                    (t("Quantity"), fmt_quantity(perf.quantity)),
                    (t("Last price"), _last_price(perf, cur)),
                    (t("Value"), fmt(perf.value, cur)),
                    *([(t("Opening value"), fmt(perf.opening, cur))] if perf.opening >= 0.005 else []),
                    (t("Money put in"), fmt(perf.invested, cur)),
                    (t("Net dividends"), fmt(perf.dividends, cur)),
                    *([(tax_label(cur), fmt(perf.withholding, cur))] if perf.withholding >= 0.005 else []),
                    (t("Fees"), fmt(perf.fees, cur)),
                    (t("Realized"), fmt(perf.realized, cur)),
                    (t("Unrealized"), fmt(perf.unrealized, cur)),
                ):
                    with ui.element("div").classes("group-row"):
                        ui.label(label).classes("flex-1 text-2")
                        ui.label(value).classes("num text-right")
                with ui.element("div").classes("group-row"):
                    ui.label(t("Gain")).classes("flex-1 font-medium")
                    ui.label(_gain_text(perf, cur)).classes("num font-medium " + _gain_class(perf.gain))
            ui.label(
                t(
                    "Realized: sales against the average cost of what was sold. "
                    "Unrealized: value against the average cost of what is held."
                )
            ).classes("caption px-1")
            with ui.row().classes("w-full gap-1 flex-wrap"):
                for kind in (TRADE_BUY, TRADE_SELL, TRADE_DIVIDEND):
                    ui.button(
                        t(TRADE_KINDS[kind]),
                        on_click=lambda k=kind: open_trade_sheet(ctx, changed, kind=k, holding_id=holding_id),
                    ).props("flat dense no-caps").classes("text-primary")
                ui.button(
                    t("Update Price"), on_click=lambda: open_price_sheet(ctx, changed, holding_id=holding_id)
                ).props("flat dense no-caps").classes("text-primary")
            ui.label(t("History")).classes("group-header pt-2")
            trades = {tr.id: tr for tr in inv_svc.trades_of(ctx, h.id)}
            prices = {p.id: p for p in inv_svc.prices_of(ctx, h.id)}
            with ui.element("div").classes("group shrink-0"):
                entries = inv_svc.history(ctx, h.id)
                if not entries:
                    with ui.element("div").classes("group-row"):
                        ui.label(t("No trades or prices yet.")).classes("text-2")
                for step in entries:
                    trade = trades.get(step.event.id)
                    with ui.element("div").classes("group-row"):
                        with ui.column().classes("gap-0 flex-1 min-w-0"):
                            ui.label(_step_title(step, cur)).classes("truncate")
                            ui.label(_step_caption(ctx, step, trade, cur)).classes("caption")
                        if abs(step.revaluation) >= 0.005:
                            ui.label((fmt_signed(step.revaluation) or "") + f" {cur}").classes(
                                "num text-[13px] " + _gain_class(step.revaluation)
                            ).tooltip(t("Capital gain or loss booked with it"))
                        with ui.row().classes("row-actions gap-0 no-wrap"):
                            if trade is not None:
                                named(
                                    ui.button(
                                        icon="o_edit",
                                        on_click=lambda tr=trade: open_trade_sheet(ctx, changed, editing=tr),
                                    )
                                    .props(ROW_ACTION)
                                    .classes("text-primary"),
                                    t("Edit"),
                                )
                                named(
                                    ui.button(icon="o_delete", on_click=lambda tr=trade: delete_trade(tr))
                                    .props(ROW_ACTION)
                                    .classes("text-negative"),
                                    t("Delete"),
                                )
                            elif (p := prices.get(step.event.id)) is not None:
                                named(
                                    ui.button(
                                        icon="o_edit", on_click=lambda p=p: open_price_sheet(ctx, changed, editing=p)
                                    )
                                    .props(ROW_ACTION)
                                    .classes("text-primary"),
                                    t("Edit"),
                                )
                                named(
                                    ui.button(icon="o_delete", on_click=lambda p=p: delete_price(p))
                                    .props(ROW_ACTION)
                                    .classes("text-negative"),
                                    t("Delete"),
                                )
            with ui.row().classes("w-full items-center gap-2 pt-1"):
                ui.button(t("Edit Holding…"), on_click=lambda: open_holding_form(ctx, changed, editing=h)).props(
                    "flat dense no-caps"
                ).classes("text-primary")
                ui.button(t("Delete Holding…"), on_click=lambda: delete_holding(h)).props("flat dense no-caps").classes(
                    "text-negative"
                )
                ui.space()
                ui.button(t("Close"), on_click=dlg.close).props("flat").classes("text-primary")

        def delete_trade(trade: Trade) -> None:
            def do() -> None:
                try:
                    inv_svc.delete_trade(ctx, trade.id)
                except ValidationError as exc:
                    error(str(exc))
                    return
                undoable(t("Trade deleted"))
                changed()

            confirm(t("Delete this trade?"), do, detail=t("The transactions it booked are deleted with it."))

        def delete_price(price: Price) -> None:
            def do() -> None:
                inv_svc.delete_price(ctx, price.id)
                undoable(t("Price deleted"))
                changed()

            confirm(t("Delete this price?"), do, detail=t("The gain or loss it booked is removed too."))

        def delete_holding(h: Holding) -> None:
            def do() -> None:
                inv_svc.delete_holding(ctx, h.id)
                undoable(t("Deleted {name}", name=h.name))
                changed()  # the sheet closes itself: the holding is gone

            n = len(inv_svc.trades_of(ctx, h.id)) + len(inv_svc.prices_of(ctx, h.id))
            confirm(
                t("Delete “{name}”?", name=h.name),
                do,
                detail=tn(
                    "Its {n} trade or price and the transactions it booked are deleted too.",
                    "Its {n} trades and prices and the transactions they booked are deleted too.",
                    n,
                )
                if n
                else "",
            )

        content()


def open_source(ctx: Context, tx: Transaction, on_saved: Refresh = None) -> None:
    """Open the trade or price a transaction was booked by (from the transaction editor)."""
    found = inv_svc.source(ctx, tx)
    if isinstance(found, Trade):
        open_trade_sheet(ctx, on_saved, editing=found)
    elif isinstance(found, Price):
        open_price_sheet(ctx, on_saved, editing=found)
    elif isinstance(found, Account):
        ui.notify(
            t(
                "Booked when {name} became a securities or crypto account in {month}: the difference between its "
                "last recorded balance and the value of its opening holdings. It follows them on the Investments page.",
                name=found.name,
                month=months.label(found.invest_from),
            ),
            position="bottom",
            timeout=8000,
        )
    else:
        error(t("This transaction was booked on the Investments page; its trade or price no longer exists."))


# ------------------------------------------------------------------ converting an account with history
def open_conversion_sheet(ctx: Context, account_id: str, asset_class: str, on_done: Refresh = None) -> None:
    """Make an account with transactions or balances a securities or crypto account (decision 13): the month it
    happens in, and the holdings it starts with at their price on that month's first day."""
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        return
    rows: list[inv_svc.OpeningHolding] = [inv_svc.OpeningHolding()]
    current = months.current_month()
    used = [e.month for e in ctx.store.account_entries.all() if e.account_id == acc.id]
    used += [
        months.month_of(tx.date) for tx in ctx.store.transactions.all() if acc.id in (tx.from_account, tx.to_account)
    ]
    first = min([*used, current])
    with sheet(t("Convert to {asset_class}", asset_class=t(ASSET_CLASSES[asset_class])), width="42rem") as dlg:
        ui.label(
            t(
                "{name} has transactions or recorded balances. They stay as its history up to the month before the "
                "conversion. From the conversion month on, it is valued from the holdings below, at their price on "
                "the first day of that month, and takes no other transactions or balances.",
                name=acc.name,
            )
        ).classes("caption -mt-2 px-1")
        month = ui.select(month_options(first, current), value=current, label=t("Conversion month"))
        month.props("dense outlined options-dense").classes("w-full")
        balance_note = ui.label().classes("caption -mt-2 px-1")
        ui.label(t("Opening Holdings")).classes("group-header pt-2")

        @ui.refreshable
        def holdings_list() -> None:
            with ui.element("div").classes("group shrink-0 w-full"):
                for n, row in enumerate(rows):
                    with ui.element("div").classes("group-row flex-wrap gap-2"):
                        name = ui.input(t("Name"), value=row.name).props("dense outlined")
                        name.classes("flex-[2] min-w-[8rem]")
                        symbol = ui.input(t("ISIN or symbol"), value=row.symbol, placeholder=t("Optional"))
                        symbol.props("dense outlined").classes("flex-1 min-w-[7rem]")
                        quantity = ui.number(t("Quantity"), value=row.quantity, min=0, precision=6)
                        quantity.props("dense outlined inputmode=decimal").classes("flex-1 min-w-[6rem]")
                        price = ui.number(t("Price"), value=row.price, min=0, precision=6)
                        price.props(f'dense outlined inputmode=decimal suffix="{acc.currency}"')
                        price.classes("flex-1 min-w-[6rem]")
                        for el, attr in ((name, "name"), (symbol, "symbol"), (quantity, "quantity"), (price, "price")):
                            el.on_value_change(lambda e, r=row, a=attr: keep(r, a, e.value))
                        named(
                            ui.button(icon="o_delete", on_click=lambda n=n: remove(n))
                            .props(ROW_ACTION)
                            .classes("text-negative self-center"),
                            t("Remove"),
                        )

        holdings_list()
        ui.button(t("Add Holding"), icon="add", on_click=lambda: add()).props("flat dense no-caps").classes(  # noqa: PLW0108 - defined below
            "text-primary self-start"
        )
        summary = ui.label().classes("caption px-1")
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption text-neg")
            ui.space()
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Convert")).props("unelevated")

    def keep(row: inv_svc.OpeningHolding, attr: str, value: object) -> None:
        setattr(row, attr, (value or "") if attr in ("name", "symbol") else value)
        changed()

    def add() -> None:
        rows.append(inv_svc.OpeningHolding())
        holdings_list.refresh()
        changed()

    def remove(n: int) -> None:
        rows.pop(n)
        if not rows:
            rows.append(inv_svc.OpeningHolding())
        holdings_list.refresh()
        changed()

    def changed() -> None:
        cur = acc.currency
        before = inv_svc.recorded_before(ctx, acc.id, month.value or current)
        balance_note.set_text(
            t(
                "Last recorded balance: {amount} at the end of {month}.",
                amount=fmt(before.amount, cur),
                month=months.label(before.month),
            )
            if before
            else t("No balance recorded before; it starts from 0.")
        )
        try:
            conv = inv_svc.check_conversion(ctx, acc.id, asset_class, month.value or "", rows)
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if any(not r.blank for r in rows) or month.value != current else "")
            summary.set_text("")
            return
        save_btn.enable()
        status.set_text("")
        parts = [
            t(
                "Opening value {value}, a difference of {difference} to the last balance, booked on {date} as Capital "
                "gains on investment.",
                value=fmt(conv.value, cur),
                difference=(fmt_signed(conv.difference) or fmt(0)) + f" {cur}",
                date=i18n.date_label(conv.day),
            )
        ]
        if conv.replaced:
            parts.append(
                tn(
                    "The balance recorded for {month} is replaced by the value of the holdings.",
                    "The {n} balances recorded from {month} on are replaced by the value of the holdings.",
                    len(conv.replaced),
                    month=months.label(conv.replaced[0]),
                )
            )
        summary.set_text(" ".join(parts))

    def save() -> None:
        try:
            inv_svc.convert_account(ctx, acc.id, asset_class, month.value or "", rows)
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        undoable(
            t("{name} is valued from its holdings from {month} on", name=acc.name, month=months.label(month.value))
        )
        dlg.close()
        _done(on_done)

    save_btn.on_click(save)
    month.on_value_change(changed)
    changed()


# ------------------------------------------------------------------ the page
def _summary(ctx: Context, view: str) -> None:
    total = Performance()
    for acc in inv_svc.investment_accounts(ctx):
        factor = ctx.convert_on(1.0, acc.currency, view, None)
        for h in inv_svc.holdings(ctx, acc.id):
            total = total + inv_svc.performance(ctx, h.id).scaled(factor)
    with ui.element("div").classes("surface"), ui.element("div").classes("metrics").style("--n: 3"):
        metric(t("Value"), total.value, view, t("At the latest prices"))
        metric(
            t("Money put in"),
            total.invested,
            view,
            t("Dividends {dividends} · fees {fees}", dividends=fmt(total.dividends), fees=fmt(total.fees)),
        )
        with ui.element("div").classes("metric"):
            ui.label(t("Gain")).classes("metric-label")
            ui.label((fmt_signed(total.gain) or fmt(0)) + f" {view}").classes("metric-value " + _gain_class(total.gain))
            ui.label(
                t("{pct} % of the money put in", pct=fmt_signed(total.pct) or "0.00")
                if total.pct is not None
                else t("Nothing put in")
            ).classes("metric-sub")


RECLAIM_YEARS = 3  # the reclaimable withholding tax of this year and the two before


def _reclaimable(ctx: Context) -> None:
    """The Swiss withholding tax on CHF dividends of the last years, to claim back with the tax return."""
    first = date.today().year - RECLAIM_YEARS + 1
    years = {year: amount for year, amount in inv_svc.reclaimable_tax(ctx).items() if year >= first}
    if not years:
        return
    with ui.column().classes("w-full gap-1"):
        with ui.element("div").classes("group"):
            for year, amount in years.items():
                with ui.element("div").classes("group-row"):
                    ui.label(t("Reclaimable withholding tax {year}", year=str(year))).classes("flex-1")
                    ui.label(fmt(amount, "CHF")).classes("num font-medium")
        ui.label(
            t("Swiss withholding tax on dividends in CHF accounts. Claim it back with your tax return for that year.")
        ).classes("caption px-1")


def _holding_row(ctx: Context, h: Holding, cur: str, refresh: Callable[[], object]) -> Performance:
    perf = inv_svc.performance(ctx, h.id)

    def show() -> None:
        with ui.context.client.content:  # outside the list, which refreshes while the sheet stays open
            open_holding_sheet(ctx, h.id, refresh)

    with ui.element("div").classes("group-row cursor-pointer").props("role=button tabindex=0") as row:
        row.on("click", show).on("keydown.enter", show)
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            ui.label(h.name).classes("truncate w-full")
            caption = _position(perf, cur) if perf.quantity > 0 or perf.price is None else t("Sold")
            ui.label(" · ".join(p for p in (h.symbol, caption) if p)).classes("caption")
        with ui.column().classes("gap-0 items-end shrink-0"):
            ui.label(fmt(perf.value, cur)).classes("num font-medium")
            ui.label(_gain_text(perf, cur)).classes("caption num " + _gain_class(perf.gain))
        ui.icon("chevron_right", size="18px").classes("text-3")
    return perf


def page() -> None:
    with frame(t("Investments"), "/investments", narrow=True) as (ctx, actions):
        view = ctx.store.settings.display_currency

        @ui.refreshable
        def buttons() -> None:
            if not inv_svc.holdings(ctx):
                return
            ui.button(t("Buy"), icon="add", on_click=lambda: open_trade_sheet(ctx, refresh, TRADE_BUY)).props(
                "unelevated"
            )
            for label, open_sheet in (
                (t("Sell"), lambda: open_trade_sheet(ctx, refresh, TRADE_SELL)),
                (t("Dividend"), lambda: open_trade_sheet(ctx, refresh, TRADE_DIVIDEND)),
                (t("Update Price"), lambda: open_price_sheet(ctx, refresh)),
                (t("Paste Prices…"), lambda: open_paste_sheet(ctx, refresh)),
            ):
                ui.button(label, on_click=open_sheet).props("flat no-caps").classes("text-primary")

        @ui.refreshable
        def body() -> None:
            accounts = inv_svc.investment_accounts(ctx)
            if not accounts:
                with (
                    ui.element("div").classes("group"),
                    empty_state(
                        t("No securities or crypto accounts yet."),
                        t(
                            "Give an account the asset class Securities or Crypto under Accounts, then add its "
                            "holdings here. They are valued at quantity × the latest price, and buys, sales and "
                            "dividends are booked as transactions."
                        ),
                    ),
                ):
                    ui.button(t("Accounts…"), on_click=lambda: ui.navigate.to("/accounts")).props(
                        "flat dense no-caps"
                    ).classes("link-btn -ml-2")
                return
            if inv_svc.holdings(ctx):
                try:
                    _summary(ctx, view)
                except FxError as exc:
                    error(str(exc))
                ui.label(t(GAIN_NOTE)).classes("caption -mt-2 px-1")
                _reclaimable(ctx)
            for acc in accounts:
                with ui.column().classes("w-full gap-0"):
                    ui.label(f"{acc.name} · {acc.currency} · {t(ASSET_CLASSES[acc.asset_class])}").classes(
                        "group-header"
                    )
                    with ui.element("div").classes("group"):
                        rows = inv_svc.holdings(ctx, acc.id)
                        total = Performance()
                        for h in rows:
                            total = total + _holding_row(ctx, h, acc.currency, refresh)
                        if len(rows) > 1:
                            with ui.element("div").classes("group-row"):
                                ui.label(t("Total")).classes("flex-1 font-semibold")
                                with ui.column().classes("gap-0 items-end"):
                                    ui.label(fmt(total.value, acc.currency)).classes("num font-semibold")
                                    ui.label(_gain_text(total, acc.currency)).classes(
                                        "caption num " + _gain_class(total.gain)
                                    )
                                ui.element("div").classes("w-[18px]")
                        with ui.element("div").classes("group-row"):
                            ui.button(
                                t("Add Holding…"), on_click=lambda a=acc: open_holding_form(ctx, refresh, a.id)
                            ).props("flat dense no-caps").classes("link-btn -ml-2")

        def refresh() -> None:
            buttons.refresh()
            body.refresh()

        with actions:
            buttons()
        body()
        on_data_changed(refresh)
