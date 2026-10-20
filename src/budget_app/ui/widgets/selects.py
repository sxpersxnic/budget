"""Reusable dropdowns: searchable categories, currencies, accounts."""

from __future__ import annotations

from nicegui import ui

from budget_app.config import RESOURCES_DIR
from budget_app.domain.coicop import fold
from budget_app.domain.everyday import CategoryOption, category_options
from budget_app.domain.models import ACCOUNT_REGULAR
from budget_app.i18n import t
from budget_app.services import accounts as acc_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context


class CategorySelect(ui.select, component=RESOURCES_DIR / "category_select.js"):
    """The category picker (F08): a searchable select whose value is a COICOP code (or TAX).

    Its search also matches everyday names, search words and official names: every option carries its folded
    `search` text, which `category_select.js` compares in the browser (NiceGUI's own filter only sees the
    label). `extra` adds options without COICOP details, such as income categories on the import page."""

    def __init__(
        self,
        options: list[CategoryOption],
        extra: dict[str, str] | None = None,
        *,
        label: str,
        value: str | None = None,
        on_change=None,
    ) -> None:
        extra = extra or {}
        self._details: dict[str, dict] = {
            o.code: {"caption": o.caption, "level": o.level, "recent": o.recent, "search": o.search} for o in options
        }
        self._details |= {key: {"search": fold(text)} for key, text in extra.items()}
        super().__init__(
            {o.code: o.label for o in options} | extra,
            label=label,
            value=value or None,
            with_input=True,
            on_change=on_change,
        )
        self._props["recent-title"] = t("Recently used")

    def _update_options(self) -> None:
        super()._update_options()
        details = getattr(self, "_details", {})
        self._props["options"] = [opt | details.get(self._values[opt["value"]], {}) for opt in self._props["options"]]


def expenditure_category_select(
    value: str | None = None, on_change=None, label: str | None = None, ctx: Context | None = None
) -> ui.select:
    """COICOP (any level) + Taxes, with a search field; with `ctx`, the recently used categories come first."""
    recent = tx_svc.recent_categories(ctx) if ctx else []
    sel = CategorySelect(category_options(recent), label=label or t("Category"), value=value, on_change=on_change)
    return sel.props("dense outlined options-dense clearable").classes("w-full")


def income_category_options(ctx: Context) -> dict[str, str]:
    return {c.id: c.name for c in acc_svc.income_categories(ctx)}


def income_category_select(ctx: Context, value: str | None = None, on_change=None) -> ui.select:
    sel = ui.select(
        income_category_options(ctx),
        label=t("Income category"),
        value=value or None,
        with_input=True,
        on_change=on_change,
    )
    return sel.props("dense outlined clearable").classes("w-full")


def currency_select(
    ctx: Context, value: str | None = None, on_change=None, label: str | None = "Currency"
) -> ui.select:
    """`label=None` hides the label; the default is the translated "Currency"."""
    currencies = ctx.fx.currencies()
    sel = ui.select(
        currencies,
        label=t(label) if label else None,
        value=value if value in currencies else None,
        with_input=True,
        on_change=on_change,
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
