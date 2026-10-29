"""Quick add from a phone (F35, decision 21): the pairing page and the quick-add page, made for a small screen.

A phone on the home network opens `/pair` (the address and a QR code are in Settings → Security on the computer),
types the six-digit code shown there and gets a phone session; `/quick` then records an expense. Neither page shows
anything else of the records: the accounts and categories to choose from, and what this phone just saved.
"""

from __future__ import annotations

from datetime import date

from nicegui import ui

from budget_app.domain import everyday
from budget_app.domain.models import ACCOUNT_REGULAR, TX_EXPENDITURE
from budget_app.i18n import t
from budget_app.services import accounts as acc_svc
from budget_app.services import context as context_module
from budget_app.services import security
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError, get_ctx
from budget_app.services.transactions import TransactionInput
from budget_app.ui import theme
from budget_app.ui.layout import error, fmt

RECENT = 5  # what this page shows of what was saved from it


def _card(title: str):
    theme.apply_colors()
    ui.page_title(f"{title} – Budget")
    page = ui.column().classes("lock-page items-center")
    with page:
        card = ui.column().classes("lock-card quick-card items-stretch gap-3")
    with card, ui.row().classes("items-center gap-2 self-center pb-1"):
        ui.icon("savings", size="26px").classes("text-primary")
        ui.label(title).classes("text-[20px] font-semibold")
    return card


def pair_page() -> None:
    """The phone's way in: the pairing code from Settings on the computer."""
    with _card(t("Pair This Phone")):
        if not security.phone_enabled():
            ui.label(
                t("Quick add from a phone is off. Turn it on in Budget on the computer: Settings → Security.")
            ).classes("footnote text-center")
            return
        ui.label(t("Type the code shown in Budget on the computer under Settings → Security → Pair a Phone.")).classes(
            "footnote text-center"
        )
        code = (
            ui.input(t("Pairing code"))
            .props("dense outlined autofocus inputmode=numeric autocomplete=one-time-code maxlength=7")
            .classes("w-full mono text-center")
        )

        def pair() -> None:
            try:
                nonce = security.pair(code.value or "")
            except ValidationError as exc:
                error(str(exc))
                code.value = ""
                return
            ui.navigate.to(f"/unlock/{nonce}")

        code.on("keydown.enter", pair)
        ui.button(t("Pair"), on_click=pair).props("unelevated no-caps").classes("w-full")


def page() -> None:
    """Record an expense: amount, account, category, payee, date and a note."""
    with _card(t("Quick Add")):
        if not context_module.is_open():  # an encrypted folder waits for its password on the computer
            ui.label(t("Unlock Budget on the computer first.")).classes("footnote text-center")
            return
        ctx = get_ctx()
        accounts = [a for a in acc_svc.accounts(ctx, ACCOUNT_REGULAR) if not a.holds_securities]
        if not accounts:
            ui.label(t("Add an account in Budget on the computer first.")).classes("footnote text-center")
            return
        recent = tx_svc.recent_categories(ctx)
        common = everyday.everyday()
        categories = {o.code: o.label for o in everyday.category_options(recent) if o.recent or o.code in common}
        last = next((tx.from_account for tx in reversed(ctx.store.transactions.all()) if tx.from_account), "")
        by_id = {a.id: a for a in accounts}
        amount = (
            ui.number(t("Amount"), min=0, format="%.2f")
            .props("dense outlined autofocus inputmode=decimal")
            .classes("w-full quick-amount")
        )
        account = (
            ui.select(
                {a.id: f"{a.name} · {a.currency}" for a in accounts},
                value=last if last in by_id else accounts[0].id,
                label=t("Account"),
            )
            .props("dense outlined")
            .classes("w-full")
        )
        category = (
            ui.select(categories, label=t("Category"), with_input=True)
            .props("dense outlined options-dense")
            .classes("w-full")
        )
        payee = ui.input(t("Payee")).props("dense outlined").classes("w-full")
        day = ui.input(t("Date"), value=date.today().isoformat()).props("dense outlined type=date").classes("w-full")
        note = ui.input(t("Note")).props("dense outlined").classes("w-full")
        saved = ui.column().classes("w-full gap-1 pt-1")

        def save() -> None:
            acc = by_id.get(account.value or "")
            try:
                when = date.fromisoformat(day.value or "")
            except ValueError:
                error(t("Choose a date"))
                return
            inp = TransactionInput(
                date=when,
                type=TX_EXPENDITURE,
                amount=amount.value,
                currency=acc.currency if acc else "",
                category=category.value or "",
                from_account=acc.id if acc else "",
                payee=payee.value or "",
                comment=note.value or "",
            )
            try:
                tx_svc.save(ctx, inp)
            except ValidationError as exc:
                error(str(exc))
                return
            text = f"{fmt(inp.amount, inp.currency)} · {categories.get(inp.category, inp.category)}"
            ui.notify(t("Saved {what}", what=text), type="positive")
            with saved:
                ui.label(text).classes("caption")
            while len(saved.default_slot.children) > RECENT:
                saved.default_slot.children[0].delete()
            amount.value = None
            payee.value = ""
            note.value = ""

        ui.button(t("Save Expense"), on_click=save).props("unelevated no-caps").classes("w-full")
