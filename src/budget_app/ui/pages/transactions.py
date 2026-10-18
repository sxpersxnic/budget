"""Transaction overview: filter, sort, edit and delete."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import TX_EXCHANGE, TX_EXPENDITURE, TX_INCOME, TX_TYPES, Transaction
from budget_app.services import accounts as acc_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.reports import ADV_REPAY_NAME
from budget_app.ui.layout import card, date_input, error, frame, parse_date, success
from budget_app.ui.widgets.grid import confirm, make_grid, num_col
from budget_app.ui.widgets.transaction_form import TransactionForm

COLUMNS = [
    {"field": "date", "headerName": "Date", "sort": "desc", "width": 115},
    {"field": "type", "headerName": "Type", "width": 140},
    num_col("amount", "Amount", width=120),
    {"field": "currency", "headerName": "Cur.", "width": 75},
    num_col("amount_chf", "Amount CHF", width=130),
    {"field": "category", "headerName": "Category", "width": 260},
    {"field": "from", "headerName": "From", "width": 170},
    {"field": "to", "headerName": "To", "width": 170},
    {"field": "advance", "headerName": "Advance", "width": 160},
    {"field": "comment", "headerName": "Comment", "width": 220},
    {"field": "fx_date", "headerName": "FX date", "width": 110},
]


def _row(t: Transaction, accs: dict[str, str], income_cats: dict[str, str]) -> dict:
    if t.type == TX_EXPENDITURE:
        category = get_coicop().label(t.category)
    elif t.type == TX_INCOME:
        category = ADV_REPAY_NAME if t.is_advance else income_cats.get(t.category, "")
    else:
        category = ""
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
        "_search": " ".join([category, accs.get(t.from_account, ""), to, t.debtor, t.comment]).lower(),
        "_accounts": [t.from_account, t.to_account, t.advance_account],
        "_type": t.type,
    }


def page() -> None:
    with frame("Transactions", "/transactions") as ctx:
        with card():
            with ui.row().classes("w-full items-start gap-2 flex-wrap"):
                d_from = date_input("From date").classes("w-40")
                d_to = date_input("To date").classes("w-40")
                a_min = ui.number("Min amount").props("dense outlined").classes("w-32")
                a_max = ui.number("Max amount").props("dense outlined").classes("w-32")
                acc = ui.select(
                    {a.id: acc_svc.account_label(a) for a in acc_svc.accounts(ctx)},
                    label="Account (from/to)",
                    with_input=True,
                    clearable=True,
                )
                acc.props("dense outlined").classes("w-56")
                typ = ui.select(TX_TYPES, label="Type", clearable=True).props("dense outlined").classes("w-44")
                text = ui.input("Search").props("dense outlined clearable").classes("w-56")
            grid = make_grid(COLUMNS)
            with ui.row().classes("items-center gap-2"):
                ui.button("Edit selected", icon="edit", on_click=lambda: edit_selected())  # noqa: PLW0108 - defined below
                ui.button("Delete selected", icon="delete", color="negative", on_click=lambda: delete_selected())  # noqa: PLW0108 - defined below
                count = ui.label().classes("text-sm text-slate-500")

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
            count.set_text(f"{len(rows)} transactions")

        for el in (d_from, d_to, a_min, a_max, acc, typ, text):
            el.on_value_change(apply_filters)
        apply_filters()

        def open_editor(tx_id: str) -> None:
            tx = ctx.store.transactions.get(tx_id)
            if tx is None:
                return

            def saved(_: Transaction) -> None:
                dlg.close()
                apply_filters()

            with ui.dialog() as dlg, ui.card().classes("min-w-[56rem] max-w-[90vw]"):
                ui.label("Edit transaction").classes("text-lg font-semibold")
                TransactionForm(ctx, editing=tx, on_saved=saved)
                ui.button("Cancel", on_click=dlg.close).props("flat")
            dlg.open()

        async def edit_selected() -> None:
            rows = await grid.get_selected_rows()
            if len(rows) != 1:
                error("Select exactly one transaction to edit")
                return
            open_editor(rows[0]["id"])

        async def delete_selected() -> None:
            rows = await grid.get_selected_rows()
            if not rows:
                error("Select at least one transaction")
                return

            def do() -> None:
                try:
                    n = tx_svc.delete(ctx, [r["id"] for r in rows])
                    success(f"Deleted {n} transaction(s)")
                except ValidationError as exc:
                    error(str(exc))
                apply_filters()

            confirm(f"Delete {len(rows)} transaction(s)?", do)

        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
