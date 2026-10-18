"""Shared AG Grid helpers for the list pages."""

from __future__ import annotations

from nicegui import ui

from budget_app.ui.layout import sheet

NUMBER = "toLocaleString('de-CH', {minimumFractionDigits: 2, maximumFractionDigits: 2}).replace('-', '−')"
# ISO values keep sorting and filtering exact; the formatter only changes what is shown.
DATE_FMT = (
    "p => p.value ? new Date(p.value + 'T00:00').toLocaleDateString('en-GB', "
    "{day: 'numeric', month: 'short', year: 'numeric'}) : ''"
)
MONTH_FMT = (
    "p => p.value ? new Date(p.value + '-01T00:00').toLocaleDateString('en-GB', {month: 'short', year: 'numeric'}) : ''"
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
    col = {"field": field, "headerName": header, ":valueFormatter": DATE_FMT}
    col.update(extra)
    return col


def month_col(field: str, header: str, **extra) -> dict:
    col = {"field": field, "headerName": header, ":valueFormatter": MONTH_FMT}
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
            "overlayNoRowsTemplate": '<span style="color: var(--label-2)">Nothing matches these filters.</span>',
        },
        auto_size_columns=False,
    )
    return grid.classes("w-full").style(f"height: {height}; min-height: 420px")


def confirm(message: str, on_yes, action: str = "Delete", detail: str = "") -> None:
    with sheet(message, width="24rem") as dlg:
        if detail:
            ui.label(detail).classes("footnote")
        with ui.row().classes("w-full justify-end gap-2 pt-1"):
            ui.button("Cancel", on_click=dlg.close).props("flat").classes("text-primary")
            ui.button(action, color="negative", on_click=lambda: (dlg.close(), on_yes())).props("unelevated")
