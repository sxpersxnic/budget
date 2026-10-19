"""Transaction overview: filter, sort, edit and delete."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import TX_EXCHANGE, TX_EXPENDITURE, TX_INCOME, TX_TYPES, Transaction
from budget_app.i18n import t, tc, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.reports import ADV_REPAY_NAME
from budget_app.ui.layout import date_input, error, frame, on_data_changed, parse_date, success
from budget_app.ui.widgets.grid import confirm, date_col, export_menu, make_grid, money_col, num_col
from budget_app.ui.widgets.transaction_form import open_transaction_sheet


def _columns() -> list[dict]:
    return [
        date_col("date", t("Date"), sort="desc", width=120),
        {"field": "category", "headerName": t("Category"), "width": 250},
        money_col("amount", t("Amount"), width=150),
        {"field": "items", "headerName": t("Items"), "width": 90, "type": "rightAligned", "cellClass": "muted"},
        {"field": "from", "headerName": tc("account", "From"), "width": 160},
        {"field": "to", "headerName": tc("account", "To"), "width": 180},
        {"field": "type", "headerName": t("Type"), "width": 130, "cellClass": "muted"},
        num_col("amount_chf", t("In CHF"), width=120, cellClass="muted"),
        {"field": "advance", "headerName": t("Advance"), "width": 150},
        {"field": "comment", "headerName": t("Note"), "width": 200},
        date_col("fx_date", t("Rate Date"), width=120, cellClass="muted"),
    ]


def _type_label(tx_type: str) -> str:
    return t(TX_TYPES[tx_type]) if tx_type in TX_TYPES else tx_type


def _row(
    tx: Transaction, accs: dict[str, str], income_cats: dict[str, str], item_counts: dict, item_names: dict
) -> dict:
    if tx.type == TX_EXPENDITURE:
        category = get_coicop().label(tx.category)
    elif tx.type == TX_INCOME:
        category = t(ADV_REPAY_NAME) if tx.is_advance else income_cats.get(tx.category, "")
    else:
        category = _type_label(tx.type)
    advance = ""
    if tx.is_advance and tx.type == TX_EXPENDITURE:
        advance = f"{tx.advance_amount:.2f} · {tx.debtor}"
    elif tx.is_advance:
        advance = t("repaid by {debtor}", debtor=tx.debtor)
    to = accs.get(tx.to_account, "")
    if tx.type == TX_EXCHANGE:
        to += f" (+{tx.to_amount:.2f} {tx.to_currency})"
    return {
        "id": tx.id,
        "date": tx.date.isoformat(),
        "type": _type_label(tx.type),
        "amount": tx.amount,
        "currency": tx.currency,
        "amount_chf": round(tx.amount_chf, 2),
        "category": category,
        "from": accs.get(tx.from_account, ""),
        "to": to,
        "advance": advance,
        "comment": tx.comment,
        "fx_date": tx.fx_date.isoformat(),
        "items": item_counts.get(tx.id) or None,
        "_search": " ".join(
            [
                category,
                accs.get(tx.from_account, ""),
                to,
                tx.debtor,
                tx.comment,
                str(tx.amount),
                item_names.get(tx.id, ""),  # find a receipt by what was bought
            ]
        ).lower(),
        "_accounts": [tx.from_account, tx.to_account, tx.advance_account],
        "_type": tx.type,
    }


def page() -> None:
    with frame(t("Transactions"), "/transactions") as (ctx, actions):
        with actions:
            text = (
                ui.input(placeholder=t("Search")).props("dense outlined clearable").classes("search w-64 max-sm:w-full")
            )
            with text.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
            filter_btn = ui.button(t("Filters"), icon="o_filter_list").props("flat").classes("text-primary")
            export_menu("transactions", t("Transactions"), _columns, lambda: shown)
            ui.button(t("Import…"), icon="o_file_upload", on_click=lambda: ui.navigate.to("/import")).props(
                "flat"
            ).classes("text-primary")
            ui.button(
                t("New Transaction"), icon="add", on_click=lambda: open_transaction_sheet(ctx, apply_filters)
            ).props("unelevated")
        with ui.row().classes("w-full items-start gap-2 flex-wrap") as filters:
            d_from = date_input(t("From")).classes("w-40")
            d_to = date_input(t("To")).classes("w-40")
            a_min = ui.number(t("Min amount")).props("dense outlined").classes("w-32")
            a_max = ui.number(t("Max amount")).props("dense outlined").classes("w-32")
            acc = ui.select(
                {a.id: acc_svc.account_label(a) for a in acc_svc.accounts(ctx)},
                label=t("Account"),
                with_input=True,
                clearable=True,
            )
            acc.props("dense outlined").classes("w-56")
            typ = (
                ui.select({k: t(v) for k, v in TX_TYPES.items()}, label=t("Type"), clearable=True)
                .props("dense outlined")
                .classes("w-44")
            )
        filters.set_visibility(False)
        filter_btn.on_click(lambda: filters.set_visibility(not filters.visible))
        with ui.element("div").classes("selection-bar") as bar:
            selected_label = ui.label().classes("font-medium")
            ui.space()
            ui.button(t("Edit"), icon="o_edit", on_click=lambda: edit_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-primary"
            )
            ui.button(t("Delete"), icon="o_delete", on_click=lambda: delete_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-negative"
            )
        bar.set_visibility(False)
        grid = make_grid(_columns())
        count = ui.label().classes("caption px-1")
        shown: list[dict] = []  # the rows the filters let through, for the export

        def apply_filters() -> None:
            start, end = parse_date(d_from.value), parse_date(d_to.value)
            q = (text.value or "").lower().strip()
            accs = {a.id: acc_svc.account_label(a) for a in ctx.store.accounts.all()}
            income_cats = {c.id: c.name for c in ctx.store.income_categories.all()}
            item_counts, item_names = items_svc.counts(ctx), items_svc.names_by_transaction(ctx)
            rows = []
            for tx in ctx.store.transactions.all():
                if (start and tx.date < start) or (end and tx.date > end):
                    continue
                if (a_min.value is not None and tx.amount < a_min.value) or (
                    a_max.value is not None and tx.amount > a_max.value
                ):
                    continue
                if typ.value and tx.type != typ.value:
                    continue
                r = _row(tx, accs, income_cats, item_counts, item_names)
                if acc.value and acc.value not in r["_accounts"]:
                    continue
                if q and q not in r["_search"]:
                    continue
                rows.append(r)
            rows.sort(key=lambda r: r["date"], reverse=True)  # the grid's default order
            shown[:] = rows
            grid.options["rowData"] = rows
            grid.update()
            total = len(ctx.store.transactions.all())
            count.set_text(
                tn("{shown} of {n} transaction", "{shown} of {n} transactions", total, shown=len(rows))
                if len(rows) != total
                else tn("{n} transaction", "{n} transactions", total)
            )
            bar.set_visibility(False)

        for el in (d_from, d_to, a_min, a_max, acc, typ, text):
            el.on_value_change(apply_filters)
        apply_filters()
        on_data_changed(apply_filters)

        def open_editor(tx_id: str) -> None:
            tx = ctx.store.transactions.get(tx_id)
            if tx is not None:
                open_transaction_sheet(ctx, apply_filters, editing=tx)

        async def selection_changed() -> None:
            rows = await grid.get_selected_rows()
            bar.set_visibility(bool(rows))
            selected_label.set_text(t("{n} selected", n=len(rows)))

        async def edit_selected() -> None:
            rows = await grid.get_selected_rows()
            if len(rows) != 1:
                error(t("Select exactly one transaction to edit"))
                return
            open_editor(rows[0]["id"])

        async def delete_selected() -> None:
            rows = await grid.get_selected_rows()
            if not rows:
                return

            def do() -> None:
                try:
                    n = tx_svc.delete(ctx, [r["id"] for r in rows])
                    success(tn("Deleted {n} transaction", "Deleted {n} transactions", n))
                except ValidationError as exc:
                    error(str(exc))
                apply_filters()

            n = len(rows)
            confirm(tn("Delete {n} transaction?", "Delete {n} transactions?", n), do, detail=t("This can’t be undone."))

        grid.on("selectionChanged", selection_changed)
        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
