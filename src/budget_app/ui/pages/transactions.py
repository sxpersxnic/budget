"""Transaction overview: filter, sort, edit and delete."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import TX_EXCHANGE, TX_EXPENDITURE, TX_INCOME, TX_TYPES, Transaction
from budget_app.services import accounts as acc_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.reports import ADV_REPAY_NAME
from budget_app.ui.layout import date_input, error, frame, on_data_changed, parse_date, success
from budget_app.ui.widgets.grid import confirm, date_col, make_grid, money_col, num_col
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

COLUMNS = [
    date_col("date", "Date", sort="desc", width=120),
    {"field": "category", "headerName": "Category", "width": 250},
    money_col("amount", "Amount", width=150),
    {"field": "from", "headerName": "From", "width": 160},
    {"field": "to", "headerName": "To", "width": 180},
    {"field": "type", "headerName": "Type", "width": 130, "cellClass": "muted"},
    num_col("amount_chf", "In CHF", width=120, cellClass="muted"),
    {"field": "advance", "headerName": "Advance", "width": 150},
    {"field": "comment", "headerName": "Note", "width": 200},
    date_col("fx_date", "Rate Date", width=120, cellClass="muted"),
]


def _row(t: Transaction, accs: dict[str, str], income_cats: dict[str, str]) -> dict:
    if t.type == TX_EXPENDITURE:
        category = get_coicop().label(t.category)
    elif t.type == TX_INCOME:
        category = ADV_REPAY_NAME if t.is_advance else income_cats.get(t.category, "")
    else:
        category = TX_TYPES.get(t.type, t.type)
    advance = ""
    if t.is_advance and t.type == TX_EXPENDITURE:
        advance = f"{t.advance_amount:.2f} · {t.debtor}"
    elif t.is_advance:
        advance = f"repaid by {t.debtor}"
    to = accs.get(t.to_account, "")
    if t.type == TX_EXCHANGE:
        to += f" (+{t.to_amount:.2f} {t.to_currency})"
    return {
        "id": t.id,
        "date": t.date.isoformat(),
        "type": TX_TYPES.get(t.type, t.type),
        "amount": t.amount,
        "currency": t.currency,
        "amount_chf": round(t.amount_chf, 2),
        "category": category,
        "from": accs.get(t.from_account, ""),
        "to": to,
        "advance": advance,
        "comment": t.comment,
        "fx_date": t.fx_date.isoformat(),
        "_search": " ".join([category, accs.get(t.from_account, ""), to, t.debtor, t.comment, str(t.amount)]).lower(),
        "_accounts": [t.from_account, t.to_account, t.advance_account],
        "_type": t.type,
    }


def page() -> None:
    with frame("Transactions", "/transactions") as (ctx, actions):
        with actions:
            text = ui.input(placeholder="Search").props("dense outlined clearable").classes("search w-64 max-sm:w-full")
            with text.add_slot("prepend"):
                ui.icon("search", size="18px").classes("text-3")
            filter_btn = ui.button("Filters", icon="o_filter_list").props("flat").classes("text-primary")
            ui.button("New Transaction", icon="add", on_click=lambda: open_transaction_sheet(ctx, apply_filters)).props(
                "unelevated"
            )
        with ui.row().classes("w-full items-start gap-2 flex-wrap") as filters:
            d_from = date_input("From").classes("w-40")
            d_to = date_input("To").classes("w-40")
            a_min = ui.number("Min amount").props("dense outlined").classes("w-32")
            a_max = ui.number("Max amount").props("dense outlined").classes("w-32")
            acc = ui.select(
                {a.id: acc_svc.account_label(a) for a in acc_svc.accounts(ctx)},
                label="Account",
                with_input=True,
                clearable=True,
            )
            acc.props("dense outlined").classes("w-56")
            typ = ui.select(TX_TYPES, label="Type", clearable=True).props("dense outlined").classes("w-44")
        filters.set_visibility(False)
        filter_btn.on_click(lambda: filters.set_visibility(not filters.visible))
        with ui.element("div").classes("selection-bar") as bar:
            selected_label = ui.label().classes("font-medium")
            ui.space()
            ui.button("Edit", icon="o_edit", on_click=lambda: edit_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-primary"
            )
            ui.button("Delete", icon="o_delete", on_click=lambda: delete_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-negative"
            )
        bar.set_visibility(False)
        grid = make_grid(COLUMNS)
        count = ui.label().classes("caption px-1")

        def apply_filters() -> None:
            start, end = parse_date(d_from.value), parse_date(d_to.value)
            q = (text.value or "").lower().strip()
            accs = {a.id: acc_svc.account_label(a) for a in ctx.store.accounts.all()}
            income_cats = {c.id: c.name for c in ctx.store.income_categories.all()}
            rows = []
            for t in ctx.store.transactions.all():
                if (start and t.date < start) or (end and t.date > end):
                    continue
                if (a_min.value is not None and t.amount < a_min.value) or (
                    a_max.value is not None and t.amount > a_max.value
                ):
                    continue
                if typ.value and t.type != typ.value:
                    continue
                r = _row(t, accs, income_cats)
                if acc.value and acc.value not in r["_accounts"]:
                    continue
                if q and q not in r["_search"]:
                    continue
                rows.append(r)
            grid.options["rowData"] = rows
            grid.update()
            total = len(ctx.store.transactions.all())
            count.set_text(f"{len(rows)} of {total} transactions" if len(rows) != total else f"{total} transactions")
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
            selected_label.set_text(f"{len(rows)} selected")

        async def edit_selected() -> None:
            rows = await grid.get_selected_rows()
            if len(rows) != 1:
                error("Select exactly one transaction to edit")
                return
            open_editor(rows[0]["id"])

        async def delete_selected() -> None:
            rows = await grid.get_selected_rows()
            if not rows:
                return

            def do() -> None:
                try:
                    n = tx_svc.delete(ctx, [r["id"] for r in rows])
                    success(f"Deleted {n} transaction{'' if n == 1 else 's'}")
                except ValidationError as exc:
                    error(str(exc))
                apply_filters()

            n = len(rows)
            confirm(f"Delete {n} transaction{'' if n == 1 else 's'}?", do, detail="This can’t be undone.")

        grid.on("selectionChanged", selection_changed)
        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
