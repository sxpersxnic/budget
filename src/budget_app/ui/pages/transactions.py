"""Transaction overview: filter, sort, edit and delete."""

from __future__ import annotations

from datetime import date

from nicegui import ui

from budget_app.domain import tags as tag_codec
from budget_app.domain.coicop import ADV, get_coicop
from budget_app.domain.models import (
    REVALUATION_KINDS,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    TX_TYPES,
    Tag,
    Transaction,
)
from budget_app.i18n import t, tc, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import aggregates
from budget_app.services import attachments as att_svc
from budget_app.services import items as items_svc
from budget_app.services import payees as payee_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import rules as rules_svc
from budget_app.services import tags as tag_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.reports import ADV_REPAY_NAME
from budget_app.ui.layout import date_input, error, frame, on_data_changed, parse_date, undoable
from budget_app.ui.pages.add_budget import recurrence_label
from budget_app.ui.pages.recurring import open_recurring_sheet
from budget_app.ui.widgets.bulk_edit import open_bulk_edit_sheet
from budget_app.ui.widgets.grid import confirm, date_col, export_menu, make_grid, money_col, num_col
from budget_app.ui.widgets.rule_sheet import always_like_this
from budget_app.ui.widgets.selects import set_options
from budget_app.ui.widgets.tags import CHIPS_RENDERER, TagSelect, grid_cells
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

# the number of attachments beside a paper clip (a count from the server, so plain HTML is safe)
CLIP = "p => p.value ? `<span class='clip-cell'><i class='material-icons'>attach_file</i>${p.value}</span>` : ''"


def _columns() -> list[dict]:
    return [
        date_col("date", t("Date"), sort="desc", width=120),
        {"field": "payee", "headerName": t("Payee"), "width": 180},
        {"field": "category", "headerName": t("Category"), "width": 250},
        {"field": "tags", "headerName": t("Tags"), "width": 170, ":cellRenderer": CHIPS_RENDERER},  # F10
        money_col("amount", t("Amount"), width=150),
        {"field": "items", "headerName": t("Items"), "width": 90, "type": "rightAligned", "cellClass": "muted"},
        {  # F12: a paper clip with the number of files; the header shows the clip, exports the word
            "field": "attachments",
            "headerName": t("Attachments"),
            "headerTooltip": t("Attachments"),
            "headerClass": "clip-header",
            "width": 64,
            ":cellRenderer": CLIP,
        },
        {"field": "from", "headerName": tc("account", "From"), "width": 160},
        {"field": "to", "headerName": tc("account", "To"), "width": 180},
        {"field": "type", "headerName": t("Type"), "width": 130, "cellClass": "muted"},
        num_col("amount_chf", t("In CHF"), width=120, cellClass="muted"),
        {"field": "advance", "headerName": t("Advance"), "width": 150},
        {"field": "comment", "headerName": t("Note"), "width": 200},
        {"field": "recurring", "headerName": t("Repeats"), "width": 140, "cellClass": "muted"},
        date_col("fx_date", t("Rate Date"), width=120, cellClass="muted"),
    ]


def _label(code: str) -> str:
    return get_coicop().label(code)


def split_columns(ctx: Context, tx: Transaction, lines: dict[str, list]) -> tuple[list[str], list[str]]:
    """The categories a split expenditure counts in, its own first (none when it isn't split), and the tags of its
    lines (F11)."""
    own = lines.get(tx.id)
    if tx.type != TX_EXPENDITURE or not own:
        return [], []
    codes = [p.category for p in aggregates.transaction_parts(ctx, tx, own) if p.category != ADV]
    codes = list(dict.fromkeys([tx.category, *codes] if tx.category in codes else codes))
    return (codes if len(codes) > 1 else []), [i for line in own for i in tag_codec.split(line.tags)]


def _type_label(tx_type: str) -> str:
    return t(TX_TYPES[tx_type]) if tx_type in TX_TYPES else tx_type


def _row(
    tx: Transaction,
    accs: dict[str, str],
    income_cats: dict[str, str],
    item_counts: dict,
    item_names: dict,
    received: float | None = None,
    repeats: dict[str, str] | None = None,
    tags: dict[str, Tag] | None = None,
    attachments: tuple[dict[str, int], dict[str, str]] = ({}, {}),
    split: tuple[list[str], list[str]] = ([], []),
) -> dict:
    """`split`: the categories a split expenditure counts in (F11), the transaction's first, and its lines' tags."""
    split_codes, line_tags = split
    if tx.type == TX_EXPENDITURE and len(split_codes) > 1:  # "01.1 Food… + 2 more"; the search finds them all
        category = tn(
            "{category} + {n} more", "{category} + {n} more", len(split_codes) - 1, category=_label(split_codes[0])
        )
    elif tx.type == TX_EXPENDITURE:
        category = get_coicop().label(tx.category)
    elif tx.type == TX_INCOME:
        category = t(ADV_REPAY_NAME) if tx.is_advance else income_cats.get(tx.category, "")
    elif tx.type == TX_REVALUATION:
        category = t(REVALUATION_KINDS.get(tx.category, tx.category))
    else:
        category = _type_label(tx.type)
    advance = ""
    if tx.is_advance and tx.type == TX_EXPENDITURE:
        advance = f"{tx.advance_amount:.2f} · {tx.debtor}"
    elif tx.is_advance:
        advance = t("repaid by {debtor}", debtor=tx.debtor)
    to = accs.get(tx.to_account, "")
    if tx.type == TX_EXCHANGE:  # what the bank credited, spread included (F44)
        to += f" (+{received if received is not None else tx.to_amount:.2f} {tx.to_currency})"
    tag_ids = dict.fromkeys([*tag_codec.split(tx.tags), *line_tags])  # the transaction's and its lines' (F11)
    own_tags = sorted((tags[i] for i in tag_ids if tags and i in tags), key=lambda g: tag_codec.key(g.name))
    tag_names = ", ".join(g.name for g in own_tags)
    return {
        "id": tx.id,
        "date": tx.date.isoformat(),
        "type": _type_label(tx.type),
        "amount": tx.amount,
        "currency": tx.currency,
        "amount_chf": round(tx.amount_chf, 2),
        "category": category,
        "payee": tx.payee,
        "from": accs.get(tx.from_account, ""),
        "to": to,
        "advance": advance,
        "comment": tx.comment,
        "fx_date": tx.fx_date.isoformat(),
        "items": item_counts.get(tx.id) or None,
        "attachments": attachments[0].get(tx.id) or None,
        "recurring": (repeats or {}).get(tx.recurring_id, "") if tx.recurring_id else "",
        "tags": tag_names,  # sorting, the column filter and the export; the cell shows `_tags` as chips
        "_tags": grid_cells(own_tags),
        "_search": " ".join(
            [
                tx.payee,
                category,
                *(_label(c) for c in split_codes[1:]),  # every category of a split (F11)
                accs.get(tx.from_account, ""),
                to,
                tx.debtor,
                tx.comment,
                str(tx.amount),
                item_names.get(tx.id, ""),  # find a receipt by what was bought
                tag_names,
                attachments[1].get(tx.id, ""),  # or by the name of its file (F12)
            ]
        ).lower(),
        "_accounts": [tx.from_account, tx.to_account, tx.advance_account],
        # a gain shows like income (signed, green); the type filter uses the stored type
        "_type": "income" if tx.type == TX_REVALUATION and tx.to_account else tx.type,
    }


def page(tag: str = "") -> None:
    """`tag` (a query parameter, from the Tags page) starts the list filtered by that tag."""
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
            payee = ui.select(
                [p.name for p in payee_svc.all_payees(ctx)], label=t("Payee"), with_input=True, clearable=True
            )
            payee.props("dense outlined").classes("w-56")
            typ = (
                ui.select({k: t(v) for k, v in TX_TYPES.items()}, label=t("Type"), clearable=True)
                .props("dense outlined")
                .classes("w-44")
            )
            tag_filter = TagSelect(ctx, [tag] if tag else [], label=t("Tags"), allow_new=False)  # any of them
            tag_filter.classes("w-64")
        filters.set_visibility(bool(tag_filter.value))
        filter_btn.on_click(lambda: filters.set_visibility(not filters.visible))
        with ui.element("div").classes("selection-bar") as bar:
            selected_label = ui.label().classes("font-medium")
            ui.space()
            ui.button(t("Edit"), icon="o_edit", on_click=lambda: edit_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-primary"
            )
            ui.button(
                t("Duplicate"),
                icon="o_content_copy",
                on_click=lambda: duplicate_selected(),  # noqa: PLW0108 - defined below
            ).props("flat dense").classes("text-primary")
            ui.button(t("Make Recurring…"), icon="o_event_repeat", on_click=lambda: make_recurring()).props(  # noqa: PLW0108 - defined below
                "flat dense"
            ).classes("text-primary")
            ui.button(
                t("Always Categorize Like This…"),
                icon="o_rule",
                on_click=lambda: categorize_like_this(),  # noqa: PLW0108 - defined below
            ).props("flat dense").classes("text-primary")
            ui.button(t("Delete"), icon="o_delete", on_click=lambda: delete_selected()).props("flat dense").classes(  # noqa: PLW0108 - defined below
                "text-negative"
            )
        bar.set_visibility(False)
        grid = make_grid(_columns())
        count = ui.label().classes("caption px-1")
        shown: list[dict] = []  # the rows the filters let through, for the export

        def apply_filters() -> None:
            set_options(payee, [p.name for p in payee_svc.all_payees(ctx)])  # with payees added since
            tag_filter.refresh_tags(ctx)  # and tags
            wanted = set(tag_filter.value or [])
            tags = tag_svc.by_id(ctx)
            start, end = parse_date(d_from.value), parse_date(d_to.value)
            q = (text.value or "").lower().strip()
            accs = {a.id: acc_svc.account_label(a) for a in ctx.store.accounts.all()}
            income_cats = {c.id: c.name for c in ctx.store.income_categories.all()}
            item_counts, item_names = items_svc.counts(ctx), items_svc.names_by_transaction(ctx)
            repeats = {r.id: recurrence_label(r.recur_months) for r in ctx.store.recurring.all()}
            attached = (att_svc.counts(ctx), att_svc.names_by_transaction(ctx))
            lines = aggregates.split_lines(ctx)  # F11
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
                if payee.value and tx.payee != payee.value:
                    continue
                split = split_columns(ctx, tx, lines)
                if wanted and wanted.isdisjoint([*tag_codec.split(tx.tags), *split[1]]):
                    continue
                received = tx_svc.received_amount(ctx, tx) if tx.type == TX_EXCHANGE else None
                r = _row(tx, accs, income_cats, item_counts, item_names, received, repeats, tags, attached, split)
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

        for el in (d_from, d_to, a_min, a_max, acc, payee, typ, tag_filter, text):
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
            selected_label.set_text(tn("{n} selected", "{n} selected", len(rows)))

        async def edit_selected() -> None:
            """One row opens the editor, several the bulk edit sheet (F13)."""
            rows = await grid.get_selected_rows()
            if len(rows) == 1:
                open_editor(rows[0]["id"])
            elif rows:
                open_bulk_edit_sheet(ctx, [r["id"] for r in rows], apply_filters)

        async def duplicate_selected() -> None:
            rows = await grid.get_selected_rows()
            if len(rows) != 1:
                error(t("Select exactly one transaction to duplicate"))
                return
            tx = ctx.store.transactions.get(rows[0]["id"])
            if tx is not None:
                prefill = tx_svc.duplicate_input(ctx, tx, date.today())
                open_transaction_sheet(ctx, apply_filters, prefill=prefill, items_from=tx.id)

        async def make_recurring() -> None:
            """A series that continues the selected transaction, which becomes its first booking (F05)."""
            rows = await grid.get_selected_rows()
            tx = ctx.store.transactions.get(rows[0]["id"]) if len(rows) == 1 else None
            if tx is None:
                error(t("Select exactly one transaction to make recurring"))
                return
            if not rec_svc.can_continue(tx):
                error(t("Only expenditure, income and account transfers without an advance payment can repeat"))
                return
            if tx.recurring_id and ctx.store.recurring.get(tx.recurring_id):
                error(t("This transaction already belongs to a recurring transaction"))
                return
            open_recurring_sheet(ctx, apply_filters, prefill=rec_svc.from_transaction(tx), first_booked=tx.id)

        async def categorize_like_this() -> None:
            """A rule that gives transactions like the selected one its category from now on (F07)."""
            rows = await grid.get_selected_rows()
            tx = ctx.store.transactions.get(rows[0]["id"]) if len(rows) == 1 else None
            if tx is None:
                error(t("Select exactly one transaction to categorize like it"))
                return
            if tx.type not in rules_svc.RULE_TYPES or not tx.category:
                error(t("Choose an expenditure or income that has a category"))
                return
            always_like_this(ctx, tx.type, tx.category, tx.payee, tx.comment)

        async def delete_selected() -> None:
            rows = await grid.get_selected_rows()
            if not rows:
                return

            def do() -> None:
                try:
                    n = tx_svc.delete(ctx, [r["id"] for r in rows])
                    undoable(tn("Deleted {n} transaction", "Deleted {n} transactions", n))
                except ValidationError as exc:
                    error(str(exc))
                apply_filters()

            n = len(rows)
            confirm(tn("Delete {n} transaction?", "Delete {n} transactions?", n), do, detail=t("This can’t be undone."))

        grid.on("selectionChanged", selection_changed)
        grid.on("rowDoubleClicked", lambda e: open_editor(e.args["data"]["id"]), ["data"])
