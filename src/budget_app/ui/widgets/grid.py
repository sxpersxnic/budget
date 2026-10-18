"""Shared AG Grid helpers for the list pages."""

from __future__ import annotations

import html

from nicegui import ui

from budget_app import i18n
from budget_app.i18n import t
from budget_app.ui.layout import sheet

# Amounts keep the Swiss format in every language.
NUMBER = "toLocaleString('de-CH', {minimumFractionDigits: 2, maximumFractionDigits: 2}).replace('-', '−')"
# AG Grid's own texts (pagination, filters) in German; English is AG Grid's default.
AG_GRID_DE = {
    "page": "Seite",
    "of": "von",
    "to": "bis",
    "more": "mehr",
    "pageSizeSelectorLabel": "Zeilen pro Seite:",
    "firstPage": "Erste Seite",
    "previousPage": "Vorherige Seite",
    "nextPage": "Nächste Seite",
    "lastPage": "Letzte Seite",
    "noRowsToShow": "Keine Einträge",
    "loadingOoo": "Wird geladen …",
    "selectAll": "Alle auswählen",
    "searchOoo": "Suchen …",
    "blanks": "(Leer)",
    "filterOoo": "Filtern …",
    "equals": "Gleich",
    "notEqual": "Ungleich",
    "blank": "Leer",
    "notBlank": "Nicht leer",
    "empty": "Bitte wählen",
    "lessThan": "Kleiner als",
    "greaterThan": "Grösser als",
    "lessThanOrEqual": "Kleiner oder gleich",
    "greaterThanOrEqual": "Grösser oder gleich",
    "inRange": "Zwischen",
    "inRangeStart": "Von",
    "inRangeEnd": "Bis",
    "contains": "Enthält",
    "notContains": "Enthält nicht",
    "startsWith": "Beginnt mit",
    "endsWith": "Endet mit",
    "andCondition": "UND",
    "orCondition": "ODER",
    "applyFilter": "Anwenden",
    "resetFilter": "Zurücksetzen",
    "clearFilter": "Leeren",
    "cancelFilter": "Abbrechen",
    "ariaChecked": "ausgewählt",
    "ariaUnchecked": "nicht ausgewählt",
}


# ISO values keep sorting and filtering exact; the formatters only change what is shown.
def _date_fmt() -> str:
    return (
        f"p => p.value ? new Date(p.value + 'T00:00').toLocaleDateString('{i18n.browser_locale()}', "
        "{day: 'numeric', month: 'short', year: 'numeric'}) : ''"
    )


def _month_fmt() -> str:
    return (
        f"p => p.value ? new Date(p.value + '-01T00:00').toLocaleDateString('{i18n.browser_locale()}', "
        "{month: 'short', year: 'numeric'}) : ''"
    )


def num_col(field: str, header: str, **extra) -> dict:
    col = {"field": field, "headerName": header, "type": "rightAligned", "filter": "agNumberColumnFilter"}
    col[":valueFormatter"] = f"p => p.value == null ? '' : p.value.{NUMBER}"
    col.update(extra)
    return col


def money_col(field: str, header: str, **extra) -> dict:
    """Amount followed by the row's currency code; income is signed and green, the way a statement reads."""
    col = num_col(field, header, **extra)
    col[":valueFormatter"] = (
        f"p => p.value == null ? '' : (p.data._type === 'income' ? '+' : '') + p.value.{NUMBER} + ' ' + p.data.currency"
    )
    col["cellClassRules"] = {"amt-in": "data._type === 'income'"}
    return col


def date_col(field: str, header: str, **extra) -> dict:
    col = {"field": field, "headerName": header, ":valueFormatter": _date_fmt()}
    col.update(extra)
    return col


def month_col(field: str, header: str, **extra) -> dict:
    col = {"field": field, "headerName": header, ":valueFormatter": _month_fmt()}
    col.update(extra)
    return col


def make_grid(columns: list[dict], height: str = "calc(100vh - 230px)") -> ui.aggrid:
    grid = ui.aggrid(
        {
            "columnDefs": columns,
            "rowData": [],
            "defaultColDef": {"sortable": True, "resizable": True, "filter": True, "suppressHeaderMenuButton": True},
            "rowSelection": {"mode": "multiRow", "headerCheckbox": True, "selectAll": "filtered"},
            ":getRowId": "p => p.data.id",
            "pagination": True,
            "paginationPageSize": 100,
            "paginationPageSizeSelector": [50, 100, 500, 1000],
            "overlayNoRowsTemplate": '<span style="color: var(--label-2)">'
            + html.escape(t("Nothing matches these filters."))
            + "</span>",
            "localeText": AG_GRID_DE if i18n.language() == "de" else {},
        },
        auto_size_columns=False,
    )
    return grid.classes("w-full").style(f"height: {height}; min-height: 420px")


def confirm(message: str, on_yes, action: str | None = None, detail: str = "") -> None:
    with sheet(message, width="24rem") as dlg:
        if detail:
            ui.label(detail).classes("footnote")
        with ui.row().classes("w-full justify-end gap-2 pt-1"):
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            ui.button(action or t("Delete"), color="negative", on_click=lambda: (dlg.close(), on_yes())).props(
                "unelevated"
            )
