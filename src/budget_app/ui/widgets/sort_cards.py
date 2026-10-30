"""The questions of To sort: one card per payee whose spending or income has no category (services/sorting.py).

A card shows who it is (initials for now), how many transactions and how much, and asks one question with the
likeliest answers as buttons; *Something else…* opens the full picker, *Ask Me per Purchase* opens the list
filtered to that payee's transactions to sort. *Remember for next time* (on by default) adds a rule. An answered
card shrinks to one line ("Amazon → Phones, computers and devices") until the page is left; the answer can be
undone from the confirmation.

The To sort tab shows every waiting payee; the import (step 2) shows those of the file just imported.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from urllib.parse import urlencode

from nicegui import ui

from budget_app.domain import months
from budget_app.domain.coicop import TAX, get_coicop
from budget_app.domain.models import TX_EXPENDITURE
from budget_app.domain.payees import key
from budget_app.i18n import t, tn
from budget_app.services import sorting
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, refresh_due_badge, sheet, undoable
from budget_app.ui.widgets.selects import expenditure_category_select, income_category_select

HUES = (211, 28, 150, 270, 340, 190, 45)  # the initials' background, chosen by name


def initials(name: str) -> str:
    """ "Sandro M." → "SM", "Glace Truck" → "G": the first word's letter, and the second's when it is an initial."""
    words = [w for w in "".join(c if c.isalpha() or c.isspace() else " " for c in name).split() if w]
    if not words:
        return "?"
    second = words[1][0] if len(words) > 1 and len(words[1]) <= 2 else ""
    return (words[0][0] + second).upper()


def logo(name: str) -> ui.element:
    """The payee's mark: its initials on a colour chosen by its name."""
    hue = HUES[sum(map(ord, key(name))) % len(HUES)]
    return ui.label(initials(name)).classes("sort-logo").style(f"background: hsl({hue} 45% 46%)")


def answer_label(ctx: Context, type_: str, code: str) -> str:
    """The everyday name of an answer, without its code: "Groceries", "Taxes", an income category's name."""
    if type_ != TX_EXPENDITURE:
        cat = ctx.store.income_categories.get(code)
        return cat.name if cat else code
    if code == TAX:
        return get_coicop().label(TAX)
    found = get_coicop().get(code)
    return found.display_name if found else code


def facts(group: sorting.Group, currency: str) -> str:
    """ "47 purchases · 6'812.40 CHF · Nov 2023 – Oct 2026"."""
    if group.kind == sorting.ATM:
        what = tn("{n} withdrawal", "{n} withdrawals", group.count)
    elif group.type == TX_EXPENDITURE and group.kind != sorting.PERSON:
        what = tn("{n} purchase", "{n} purchases", group.count)
    else:
        what = tn("{n} payment", "{n} payments", group.count)
    parts = [what, fmt(group.total, currency)]
    if group.first and group.last:
        first, last = months.label(months.month_of(group.first)), months.label(months.month_of(group.last))
        parts.append(first if first == last else f"{first} – {last}")
    return " · ".join(parts)


def question(group: sorting.Group) -> str:
    return {
        sorting.ATM: t("Cash from a cash machine. What do you usually spend it on?"),
        sorting.PERSON: t("A person you paid by TWINT. What are these payments usually?"),
        sorting.INCOME: t("What is this money?"),
    }.get(group.kind, t("What do you usually buy here?"))


def sort_cards(ctx: Context, ids: Collection[str] | None = None, on_change: Callable[[], object] | None = None):
    """The cards for the payees waiting to be sorted (of `ids`, when given). Returns the refreshable, so the
    caller can redraw it after other changes."""
    answered: list[tuple[str, str]] = []  # (payee, answer) given on this page, shown as one line each
    currency = ctx.store.settings.display_currency

    def give(group: sorting.Group, code: str, remember: bool) -> None:
        try:
            n = sorting.answer(ctx, group, code, remember)
        except ValidationError as exc:
            error(str(exc))
            return
        label = answer_label(ctx, group.type, code)
        answered.append((group.name, label))
        undoable(tn("{n} transaction is now {category}", "{n} transactions are now {category}", n, category=label))
        cards.refresh()
        refresh_due_badge()  # not the page's whole refresh: the answered lines stay
        if on_change:
            on_change()

    def other(group: sorting.Group, remember: bool) -> None:
        with sheet(group.name, width="28rem") as dlg:
            ui.label(facts(group, currency)).classes("caption -mt-2")

            def chosen(code: str | None) -> None:
                if code:
                    dlg.close()
                    give(group, code, remember)

            picker = (
                expenditure_category_select(on_change=lambda e: chosen(e.value), ctx=ctx)
                if group.type == TX_EXPENDITURE
                else income_category_select(ctx, on_change=lambda e: chosen(e.value))
            )
            picker.props("autofocus")
            dlg.on("show", lambda: picker.run_method("showPopup"))

    def per_purchase(group: sorting.Group) -> None:
        ui.navigate.to("/transactions?" + urlencode({"category": sorting.FILTER, "payee": group.name}))

    @ui.refreshable
    def cards() -> None:
        groups = sorting.groups(ctx, ids)
        with ui.element("div").classes("sort-cards"):
            for name, label in answered:
                with ui.element("div").classes("sort-card answered"):
                    logo(name)
                    ui.label(name).classes("sort-name")
                    ui.label(f"→ {label}").classes("sort-answer")
            for group in groups:
                with ui.element("div").classes("sort-card"):
                    logo(group.name)
                    with ui.element("div").classes("sort-who"):
                        ui.label(group.name).classes("sort-name")
                        ui.label(facts(group, currency)).classes("sort-facts")
                    with ui.element("div").classes("sort-ask"):
                        ui.label(question(group)).classes("sort-question")
                        remember = ui.checkbox(t("Remember for next time"), value=True).props("dense")
                        with ui.element("div").classes("sort-choices"):
                            for code in group.choices:
                                ui.button(
                                    answer_label(ctx, group.type, code),
                                    on_click=lambda g=group, c=code, r=remember: give(g, c, bool(r.value)),
                                ).props("flat no-caps").classes("sort-choice")
                            ui.button(
                                t("Something else…"),
                                on_click=lambda g=group, r=remember: other(g, bool(r.value)),
                            ).props("flat no-caps").classes("sort-choice other")
                            ui.button(t("Ask Me per Purchase"), on_click=lambda g=group: per_purchase(g)).props(
                                "flat no-caps"
                            ).classes("sort-choice other")
                        remember.move(target_index=-1)  # below the answers

    cards()
    return cards
