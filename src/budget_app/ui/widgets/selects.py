"""Reusable dropdowns: searchable categories, currencies, accounts."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from nicegui import app, ui

from budget_app import i18n
from budget_app.config import RESOURCES_DIR
from budget_app.domain.coicop import fold, notes_payload, notes_version
from budget_app.domain.everyday import CategoryOption, category_options
from budget_app.domain.models import ACCOUNT_REGULAR
from budget_app.i18n import t
from budget_app.services import accounts as acc_svc
from budget_app.services import payees as payee_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context

NOTES_ROUTE = "/coicop-notes/{lang}"


def add_notes_route() -> None:
    """Serve the COICOP notes per language as JSON (F40). The picker loads them once, when it first opens, and
    shows them on hover without asking the server again; the URL carries a version, so browsers may cache it."""

    @app.get(NOTES_ROUTE)
    def coicop_notes(lang: str) -> JSONResponse:
        payload = notes_payload(lang if lang in i18n.LANGUAGES else i18n.DEFAULT)
        return JSONResponse(payload, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def notes_url(lang: str) -> str:
    return NOTES_ROUTE.format(lang=lang) + f"?v={notes_version(lang)}"


class CategorySelect(ui.select, component=RESOURCES_DIR / "category_select.js"):
    """The category picker (F08): a searchable select whose value is a COICOP code (or TAX).

    Its search also matches everyday names, search words and official names: every option carries its folded
    `search` text, which `category_select.js` compares in the browser (NiceGUI's own filter only sees the
    label), and the words of what its COICOP notes include (`note_search`, F40), whose matches come after
    those by name. Hovering an option shows its notes, loaded once from `notes-url`. `extra` adds options
    without COICOP details, such as income categories on the import page."""

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
            o.code: {
                "code": o.code,
                "caption": o.caption,
                "level": o.level,
                "recent": o.recent,
                "search": o.search,
                "note_search": o.note_search,
            }
            for o in options
        }
        self._details |= {key: {"search": fold(text)} for key, text in extra.items()}
        super().__init__(
            {o.code: o.label for o in options} | extra,
            label=label,
            value=value or None,
            with_input=True,
            on_change=on_change,
        )
        lang = i18n.language()
        self._props["recent-title"] = t("Recently used")
        self._props["notes-url"] = notes_url(lang)
        self._props["notes-labels"] = {
            "includes": t("Includes"),
            "also_includes": t("Also includes"),
            "excludes": t("Excludes"),
            "unofficial": t("Unofficial translation"),  # shown when the loaded notes say they are translated
            "show": t("Show what this category includes"),
        }

    def _update_options(self) -> None:
        super()._update_options()
        details = getattr(self, "_details", {})
        self._props["options"] = [opt | details.get(self._values[opt["value"]], {}) for opt in self._props["options"]]


class PayeeInput(ui.input, component=RESOURCES_DIR / "payee_input.js"):
    """The payee field (F09): free text, with the known payees as suggestions below it (`autocomplete`, in
    order). Its value is the text typed or the suggestion picked, so a new payee is simply typed."""


def payee_input(ctx: Context, value: str = "", on_change=None) -> PayeeInput:
    """A payee field suggesting the recently used payees first, then the others by name."""
    inp = PayeeInput(t("Payee"), value=value or "", autocomplete=payee_svc.names(ctx), on_change=on_change)
    return inp.props("dense outlined clearable options-dense").classes("w-full")


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
    ctx: Context,
    kind: str = ACCOUNT_REGULAR,
    currency: str | None = None,
    exclude_currency: str | None = None,
    holdings: bool = False,
) -> dict[str, str]:
    """Accounts for a dropdown; securities and crypto accounts (valued from holdings, F21) only with `holdings`."""
    return {
        a.id: acc_svc.account_label(a)
        for a in acc_svc.accounts(ctx, kind, currency)
        if (exclude_currency is None or a.currency != exclude_currency) and (holdings or not a.holds_securities)
    }


def set_options(sel: ui.select, options: dict | list, keep: bool = True) -> None:
    """Replace the options of a select, clearing the value when it is no longer valid."""
    values = options.keys() if isinstance(options, dict) else options
    if sel.value is not None and (not keep or sel.value not in values):
        sel.value = None
    sel.set_options(options, value=sel.value)
