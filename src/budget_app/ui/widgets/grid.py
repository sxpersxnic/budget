"""Shared AG Grid helpers for the list pages."""

from __future__ import annotations

import html
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.i18n import t
from budget_app.services import export as export_svc
from budget_app.ui.layout import error, named, save_file, sheet

# Amounts keep the Swiss format in every language.
NUMBER = "toLocaleString('de-CH', {minimumFractionDigits: 2, maximumFractionDigits: 2}).replace('-', '−')"
# AG Grid's own texts (pagination, filters) per language; English is AG Grid's default.
AG_GRID_LOCALE: dict[str, dict[str, str]] = {}
AG_GRID_LOCALE["de"] = {
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
AG_GRID_LOCALE["fr"] = {
    "page": "Page",
    "of": "sur",
    "to": "à",
    "more": "plus",
    "pageSizeSelectorLabel": "Lignes par page :",
    "firstPage": "Première page",
    "previousPage": "Page précédente",
    "nextPage": "Page suivante",
    "lastPage": "Dernière page",
    "noRowsToShow": "Aucune entrée",
    "loadingOoo": "Chargement…",
    "selectAll": "Tout sélectionner",
    "searchOoo": "Rechercher…",
    "blanks": "(Vide)",
    "filterOoo": "Filtrer…",
    "equals": "Égal à",
    "notEqual": "Différent de",
    "blank": "Vide",
    "notBlank": "Non vide",
    "empty": "Choisir",
    "lessThan": "Inférieur à",
    "greaterThan": "Supérieur à",
    "lessThanOrEqual": "Inférieur ou égal à",
    "greaterThanOrEqual": "Supérieur ou égal à",
    "inRange": "Entre",
    "inRangeStart": "De",
    "inRangeEnd": "À",
    "contains": "Contient",
    "notContains": "Ne contient pas",
    "startsWith": "Commence par",
    "endsWith": "Se termine par",
    "andCondition": "ET",
    "orCondition": "OU",
    "applyFilter": "Appliquer",
    "resetFilter": "Réinitialiser",
    "clearFilter": "Effacer",
    "cancelFilter": "Annuler",
    "ariaChecked": "sélectionné",
    "ariaUnchecked": "non sélectionné",
}
AG_GRID_LOCALE["it"] = {
    "page": "Pagina",
    "of": "di",
    "to": "a",
    "more": "altro",
    "pageSizeSelectorLabel": "Righe per pagina:",
    "firstPage": "Prima pagina",
    "previousPage": "Pagina precedente",
    "nextPage": "Pagina successiva",
    "lastPage": "Ultima pagina",
    "noRowsToShow": "Nessuna voce",
    "loadingOoo": "Caricamento…",
    "selectAll": "Seleziona tutto",
    "searchOoo": "Cerca…",
    "blanks": "(Vuoto)",
    "filterOoo": "Filtra…",
    "equals": "Uguale a",
    "notEqual": "Diverso da",
    "blank": "Vuoto",
    "notBlank": "Non vuoto",
    "empty": "Scegliere",
    "lessThan": "Minore di",
    "greaterThan": "Maggiore di",
    "lessThanOrEqual": "Minore o uguale a",
    "greaterThanOrEqual": "Maggiore o uguale a",
    "inRange": "Tra",
    "inRangeStart": "Da",
    "inRangeEnd": "A",
    "contains": "Contiene",
    "notContains": "Non contiene",
    "startsWith": "Inizia con",
    "endsWith": "Finisce con",
    "andCondition": "E",
    "orCondition": "O",
    "applyFilter": "Applica",
    "resetFilter": "Reimposta",
    "clearFilter": "Cancella",
    "cancelFilter": "Annulla",
    "ariaChecked": "selezionato",
    "ariaUnchecked": "non selezionato",
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
    col = {"field": field, "headerName": header, "type": "rightAligned"}
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
            # one way to filter: the page's search and Filters panel, so the headers carry no filter icons
            "defaultColDef": {"sortable": True, "resizable": True, "filter": False, "suppressHeaderMenuButton": True},
            "rowSelection": {"mode": "multiRow", "headerCheckbox": True, "selectAll": "filtered"},
            ":getRowId": "p => p.data.id",
            "pagination": True,
            "paginationPageSize": 100,
            "paginationPageSizeSelector": [50, 100, 500, 1000],
            "overlayNoRowsTemplate": '<span style="color: var(--label-2)">'
            + html.escape(t("Nothing matches these filters."))
            + "</span>",
            # the selection column has no header text: its select-all checkbox carries the column's name
            "localeText": AG_GRID_LOCALE.get(i18n.language(), {}) | {"ariaHeaderSelection": t("Select all rows")},
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


def export_menu(name: str, title: str, columns: Callable[[], list[dict]], rows: Callable[[], list[dict]]) -> None:
    """Share button with CSV and Excel exports of a list page's rows as currently filtered (F24)."""
    with (
        named(ui.button(icon="ios_share").props("flat round").classes("text-primary"), t("Export the list")),
        ui.menu().props("auto-close"),
    ):
        export_items(name, title, columns, rows)


def export_items(name: str, title: str, columns: Callable[[], list[dict]], rows: Callable[[], list[dict]]) -> None:
    """The CSV and Excel export items, in the menu being built (a page's *More* menu, or the share button's)."""

    async def export(kind: str) -> None:
        try:
            df = export_svc.view_frame(columns(), rows(), currency_header=t("Currency"))
            data = export_svc.frame_csv(df) if kind == "csv" else export_svc.frame_xlsx(df, title)
        except Exception as exc:
            error(t("Export failed: {error}", error=exc))
            return
        await save_file(data, f"{name}_{date.today().isoformat()}.{kind}")

    ui.menu_item(t("Export as CSV…"), on_click=lambda: export("csv"))
    ui.menu_item(t("Export as Excel…"), on_click=lambda: export("xlsx"))


@contextmanager
def more_menu():
    """The page's secondary actions behind one "More" button, so the toolbar keeps its search and filters in one
    row (UX review, section 2). Yields the open menu; add `ui.menu_item`s to it."""
    with (
        named(ui.button(icon="more_horiz").props("flat round").classes("text-primary"), t("More")),
        ui.menu().props("auto-close") as menu,
    ):
        yield menu
