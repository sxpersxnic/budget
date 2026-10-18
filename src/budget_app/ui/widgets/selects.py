"""Reusable dropdowns: searchable categories, currencies, accounts."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import ACCOUNT_REGULAR
from budget_app.services import accounts as acc_svc
from budget_app.services.context import Context


def expenditure_category_select(value: str | None = None, on_change=None, label: str = "Category") -> ui.select:
    """COICOP (any level) + Taxes, with a search field."""
    sel = ui.select(get_coicop().selectable(), label=label, value=value or None, with_input=True, on_change=on_change)
    return sel.props("dense outlined options-dense clearable").classes("w-full")


def income_category_options(ctx: Context) -> dict[str, str]:
    return {c.id: c.name for c in acc_svc.income_categories(ctx)}


def income_category_select(ctx: Context, value: str | None = None, on_change=None) -> ui.select:
    sel = ui.select(
        income_category_options(ctx), label="Income category", value=value or None, with_input=True, on_change=on_change
    )
    return sel.props("dense outlined clearable").classes("w-full")


def currency_select(
    ctx: Context, value: str | None = None, on_change=None, label: str | None = "Currency"
) -> ui.select:
    currencies = ctx.fx.currencies()
    sel = ui.select(
        currencies, label=label, value=value if value in currencies else None, with_input=True, on_change=on_change
    )
    return sel.props("dense outlined options-dense").classes("w-28")


def account_options(
    ctx: Context, kind: str = ACCOUNT_REGULAR, currency: str | None = None, exclude_currency: str | None = None
) -> dict[str, str]:
    return {
        a.id: acc_svc.account_label(a)
        for a in acc_svc.accounts(ctx, kind, currency)
        if exclude_currency is None or a.currency != exclude_currency
    }


def set_options(sel: ui.select, options: dict | list, keep: bool = True) -> None:
    """Replace the options of a select, clearing the value when it is no longer valid."""
    values = options.keys() if isinstance(options, dict) else options
    if sel.value is not None and (not keep or sel.value not in values):
        sel.value = None
    sel.set_options(options, value=sel.value)
