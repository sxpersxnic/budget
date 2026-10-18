"""Shared AG Grid helpers for the overview tables."""

from __future__ import annotations

from nicegui import ui


def num_col(field: str, header: str, **extra) -> dict:
    col = {"field": field, "headerName": header, "type": "rightAligned", "filter": "agNumberColumnFilter"}
    col[":valueFormatter"] = (
        "p => p.value == null ? '' : p.value.toLocaleString('de-CH', "
        "{minimumFractionDigits: 2, maximumFractionDigits: 2})"
    )
    col.update(extra)
    return col


def make_grid(columns: list[dict], height: str = "60vh") -> ui.aggrid:
    grid = ui.aggrid(
        {
            "columnDefs": columns,
            "rowData": [],
            "defaultColDef": {"sortable": True, "resizable": True, "filter": True},
            "rowSelection": {"mode": "multiRow", "headerCheckbox": True, "selectAll": "filtered"},
            ":getRowId": "p => p.data.id",
            "pagination": True,
            "paginationPageSize": 100,
            "paginationPageSizeSelector": [50, 100, 500, 1000],
        },
        auto_size_columns=False,
    )
    return grid.classes("w-full").style(f"height: {height}")


def confirm(message: str, on_yes, action: str = "Delete") -> None:
    with ui.dialog() as dlg, ui.card():
        ui.label(message)
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button(action, color="negative", on_click=lambda: (dlg.close(), on_yes()))
    dlg.open()
