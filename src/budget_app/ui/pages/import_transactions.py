"""Import transactions from a bank or card CSV export (file → columns → review → import) or from a
camt.053 / camt.054 statement (file → review → import: the XML needs no column mapping).

The review step also offers the month-end balances the file shows (F36): from a CSV balance column, or
the camt closing balance. Ticked ones are recorded after the transactions are saved."""

from __future__ import annotations

from nicegui import events, ui

from budget_app.domain import months
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import TX_INCOME
from budget_app.i18n import N_, date_label, t, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import camt, import_balances, importer
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, frame, segmented, success
from budget_app.ui.widgets.grid import date_col, make_grid, num_col
from budget_app.ui.widgets.selects import account_options

DELIMITER_NAMES = {";": N_("Semicolon ;"), ",": N_("Comma ,"), "\t": N_("Tab"), "|": N_("Pipe |")}
STATUS = {
    importer.NEW: N_("New"),
    importer.DUPLICATE: N_("Already imported"),
    importer.POSSIBLE_DUPLICATE: N_("Possible duplicate"),
}
NONE = ""  # select value for "no column"


def _columns(currency: str) -> list[dict]:
    amount = num_col("amount", t("Amount"), width=140)
    amount["cellClassRules"] = {"amt-in": "x > 0"}
    amount[":valueFormatter"] = (
        "p => p.value == null ? '' : (p.value > 0 ? '+' : '') + p.value.toLocaleString('de-CH', "
        f"{{minimumFractionDigits: 2, maximumFractionDigits: 2}}).replace('-', '−') + ' {currency}'"
    )
    return [
        {
            "field": "include",
            "headerName": t("Import"),
            "width": 90,
            "editable": True,
            "cellRenderer": "agCheckboxCellRenderer",
            "cellEditor": "agCheckboxCellEditor",
        },
        date_col("date", t("Date"), width=120),
        {"field": "text", "headerName": t("Description"), "flex": 1, "minWidth": 200},
        amount,
        {"field": "category", "headerName": t("Category"), "width": 260},
        {"field": "status", "headerName": t("Status"), "width": 170, "cellClass": "muted"},
    ]


def _category_options(ctx: Context) -> dict[str, str]:
    """Expenditure categories (COICOP and taxes) followed by income categories."""
    opts = {code: label.strip() for code, label in get_coicop().selectable().items()}
    return opts | {c.id: t("Income · {name}", name=c.name) for c in acc_svc.income_categories(ctx)}


def page() -> None:
    with frame(
        t("Import Transactions"),
        "/import",
        subtitle=t("Bring in a CSV export or a camt statement from your bank or credit card"),
        narrow=False,
    ) as (ctx, _):
        profiles = importer.load_profiles(ctx.store.data_dir)
        # parsed: the CSV file; statement: the camt file (one of them is set); problem: why it can't be imported
        # balances: month-end balances the file offers; balance_problem: why its balances are not offered
        state: dict = {
            "parsed": None,
            "statement": None,
            "rows": [],
            "problem": "",
            "balances": [],
            "balance_problem": "",
        }
        category_labels = _category_options(ctx)

        # ---------------------------------------------------------- 1 · file
        with ui.element("div").classes("surface flex flex-col gap-3"):
            ui.label(t("1 · File")).classes("section-title")
            ui.label(
                t(
                    "Export the transactions from your e-banking as CSV, or as a camt.053 or camt.054 "
                    "statement (XML). Nothing leaves your computer."
                )
            ).classes("caption -mt-2")
            # Quasar's uploader panel is hidden; a plain button opens the system file dialog instead
            upload = (
                ui.upload(auto_upload=True, max_file_size=importer.MAX_BYTES, on_upload=lambda e: on_file(e))  # noqa: PLW0108 - defined below
                .props('accept=".csv,.txt,.xml,text/csv,text/xml,application/xml"')
                .classes("hidden")
            )
            with ui.row().classes("items-center gap-3"):
                ui.button(
                    t("Choose File…"), icon="o_upload_file", on_click=lambda: upload.run_method("pickFiles")
                ).props("unelevated")
                file_info = ui.label().classes("caption")

        # ---------------------------------------------------------- 2 · columns
        with ui.element("div").classes("surface flex flex-col gap-3") as columns_card:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(t("2 · Columns")).classes("section-title")
                ui.space()
                profile_sel = (
                    ui.select({p.name: p.name for p in profiles}, label=t("Saved settings"), clearable=True)
                    .props("dense outlined options-dense")
                    .classes("w-56")
                )
            with ui.row().classes("w-full gap-3 items-start flex-wrap") as account_row:
                account = (
                    ui.select(account_options(ctx), label=t("Account"), with_input=True)
                    .props("dense outlined")
                    .classes("w-64")
                )
                delimiter = (
                    ui.select({k: t(v) for k, v in DELIMITER_NAMES.items()}, label=t("Separator"))
                    .props("dense outlined options-dense")
                    .classes("w-40")
                )
                header_line = ui.number(t("Header on line"), min=1, format="%d").props("dense outlined").classes("w-36")
            with ui.row().classes("w-full gap-3 items-start flex-wrap"):
                date_sel = ui.select({}, label=t("Date column")).props("dense outlined options-dense").classes("w-56")
                date_fmt = (
                    ui.select(importer.DATE_FORMATS, label=t("Date format"))
                    .props("dense outlined options-dense")
                    .classes("w-40")
                )
            with ui.row().classes("w-full gap-3 items-center flex-wrap"):
                amount_mode = segmented({"single": t("One amount column"), "split": t("Debit and credit")}, "single")
                decimal = segmented({".": "1'234.50", ",": "1.234,50"}, ".")
            with ui.row().classes("w-full gap-3 items-start flex-wrap"):
                amount_sel = ui.select({}, label=t("Amount")).props("dense outlined options-dense").classes("w-56")
                debit_sel = (
                    ui.select({}, label=t("Debit (money out)")).props("dense outlined options-dense").classes("w-56")
                )
                credit_sel = (
                    ui.select({}, label=t("Credit (money in)")).props("dense outlined options-dense").classes("w-56")
                )
                balance_sel = (
                    ui.select({}, label=t("Balance column")).props("dense outlined options-dense").classes("w-56")
                )
            text_sel = (
                ui.select({}, label=t("Description"), multiple=True)
                .props("dense outlined options-dense use-chips")
                .classes("w-full max-w-2xl")
            )
            invert = ui.switch(t("Positive amounts are spending (typical for credit card statements)")).props("dense")
            with ui.row().classes("w-full items-center gap-2 pt-1"):
                profile_name = ui.input(placeholder=t("Name, e.g. UBS Private")).props("dense outlined").classes("w-56")
                ui.button(t("Save Settings"), on_click=lambda: save_settings()).props("flat").classes("text-primary")  # noqa: PLW0108 - defined below
                ui.label(t("The next file with the same columns is recognised automatically.")).classes("caption")
        columns_card.set_visibility(False)

        # ---------------------------------------------------------- 3 · review
        with ui.element("div").classes("surface flex flex-col gap-3") as review_card:
            review_title = ui.label(t("3 · Review")).classes("section-title")
            summary = ui.label().classes("caption -mt-2")
            # a camt statement skips the columns step: the account is chosen here (moved from step 2)
            with ui.row().classes("w-full gap-3 items-center flex-wrap") as statement_row:
                account_hint = ui.label().classes("caption")
            statement_row.set_visibility(False)
            with ui.element("div").classes("selection-bar") as bar:
                selected_label = ui.label().classes("font-medium")
                ui.space()
                bulk_cat = (
                    ui.select(category_labels, label=t("Category"), with_input=True)
                    .props("dense outlined options-dense")
                    .classes("w-72 bg-transparent")
                )
                ui.button(t("Apply"), on_click=lambda: apply_category()).props("flat dense").classes("text-primary")  # noqa: PLW0108 - defined below
                ui.button(t("Include"), on_click=lambda: set_include(True)).props("flat dense").classes("text-primary")
                ui.button(t("Exclude"), on_click=lambda: set_include(False)).props("flat dense").classes("text-primary")
            bar.set_visibility(False)
            grid = make_grid(_columns("CHF"), height="55vh")
            with ui.element("div").classes("flex flex-col gap-1") as balances_box:
                ui.label(t("Month-end balances")).classes("font-medium")
                balances_list = ui.element("div").classes("flex flex-col gap-0")
            balances_box.set_visibility(False)
            with ui.row().classes("w-full items-center gap-2"):
                status = ui.label().classes("caption")
                ui.space()
                import_btn = ui.button(t("Import"), on_click=lambda: do_import()).props("unelevated")  # noqa: PLW0108 - defined below
        review_card.set_visibility(False)

        # ---------------------------------------------------------- logic
        busy = {"value": False}

        def column_options() -> dict[str, str]:
            parsed = state["parsed"]
            return {NONE: "—"} | {h: h for h in parsed.headers} if parsed else {}

        def apply_profile(profile: importer.ImportProfile) -> None:
            busy["value"] = True
            try:
                opts = column_options()
                delimiter.value = profile.delimiter if profile.delimiter in DELIMITER_NAMES else ";"
                header_line.value = profile.header_row + 1
                for sel, value in (
                    (date_sel, profile.date_col),
                    (amount_sel, profile.amount_col),
                    (debit_sel, profile.debit_col),
                    (credit_sel, profile.credit_col),
                    (balance_sel, profile.balance_col),
                ):
                    sel.set_options(opts, value=value if value in opts else NONE)
                text_sel.set_options(
                    {h: h for h in state["parsed"].headers},
                    value=[c for c in profile.text_cols if c in opts],
                )
                date_fmt.value = profile.date_format
                amount_mode.value = "single" if profile.amount_col or not profile.debit_col else "split"
                decimal.value = profile.decimal
                invert.value = profile.invert
                if profile.account_id and ctx.store.accounts.get(profile.account_id):
                    account.value = profile.account_id
                profile_name.value = profile.name
            finally:
                busy["value"] = False
            refresh_rows()

        def current_profile() -> importer.ImportProfile:
            split = amount_mode.value == "split"
            return importer.ImportProfile(
                name=(profile_name.value or "").strip(),
                delimiter=delimiter.value or ";",
                encoding=state["parsed"].encoding,
                header_row=int(header_line.value or 1) - 1,
                date_col=date_sel.value or "",
                date_format=date_fmt.value or "%d.%m.%Y",
                amount_col="" if split else amount_sel.value or "",
                debit_col=(debit_sel.value or "") if split else "",
                credit_col=(credit_sel.value or "") if split else "",
                decimal=decimal.value or ".",
                text_cols=list(text_sel.value or []),
                invert=bool(invert.value),
                balance_col=balance_sel.value or "",
                account_id=account.value or "",
                signature=state["parsed"].signature,
            )

        async def on_file(e: events.UploadEventArguments) -> None:
            statement, parsed = None, None
            try:
                data = await e.file.read()
                if camt.looks_like_xml(data):
                    statement = camt.read_statement(e.file.name, data)
                else:
                    parsed = importer.read_file(e.file.name, data)
            except ValidationError as exc:
                error(str(exc))
                return
            finally:
                upload.reset()
            if statement is not None:
                open_statement(statement)
            elif parsed is not None:
                open_csv(parsed)

        def open_statement(statement: camt.Statement) -> None:
            state["parsed"], state["statement"] = None, statement
            period = (
                f"{date_label(statement.start)} – {date_label(statement.end)}"
                if statement.start and statement.end
                else ""
            )
            details = " · ".join(
                p for p in (statement.name, statement.message, camt.format_iban(statement.iban), period) if p
            )
            info = tn("{details} · {n} entry", "{details} · {n} entries", len(statement.rows), details=details)
            if statement.pending:
                info += " · " + tn("{n} pending entry left out", "{n} pending entries left out", statement.pending)
            file_info.set_text(info)
            columns_card.set_visibility(False)
            review_card.set_visibility(True)
            review_title.set_text(t("2 · Review"))
            statement_row.set_visibility(True)
            account.move(statement_row, 0)
            remembered = camt.remembered_account(ctx.store.data_dir, statement.iban)
            known = bool(remembered and ctx.store.accounts.get(remembered))
            account_hint.set_text(
                t("Account remembered from the last import of this IBAN.")
                if known
                else t("The account is remembered for this IBAN after the import.")
            )
            busy["value"] = True
            try:
                account.value = remembered if known else None
            finally:
                busy["value"] = False
            refresh_rows()

        def open_csv(parsed: importer.ParsedFile) -> None:
            if state["statement"] is not None:  # back from a camt file to the CSV steps
                state["statement"], state["problem"] = None, ""
                account.move(account_row, 0)
                statement_row.set_visibility(False)
                review_title.set_text(t("3 · Review"))
            state["parsed"] = parsed
            profile = importer.guess_profile(parsed, profiles)
            n = len(parsed.data)
            if profile.name:
                file_info.set_text(
                    tn(
                        "{name} · {n} line · recognised as “{profile}”",
                        "{name} · {n} lines · recognised as “{profile}”",
                        n,
                        name=parsed.name,
                        profile=profile.name,
                    )
                )
            else:
                file_info.set_text(tn("{name} · {n} line", "{name} · {n} lines", n, name=parsed.name))
            if profile.name:
                profile_sel.value = profile.name
            columns_card.set_visibility(True)
            review_card.set_visibility(True)
            apply_profile(profile)

        def reread() -> None:
            """Separator or header line changed: split the file again and guess the columns anew."""
            if busy["value"] or state["parsed"] is None:
                return
            state["parsed"] = importer.reparse(state["parsed"], delimiter.value or ";", int(header_line.value or 1) - 1)
            guess = importer.guess_profile(state["parsed"])
            guess.delimiter, guess.header_row = state["parsed"].delimiter, state["parsed"].header_row
            guess.account_id, guess.name = account.value or "", profile_name.value or ""
            apply_profile(guess)

        def refresh_rows() -> None:
            if busy["value"] or (state["parsed"] is None and state["statement"] is None):
                return
            if state["statement"] is not None:
                refresh_statement()
                return
            amount_sel.set_visibility(amount_mode.value == "single")
            debit_sel.set_visibility(amount_mode.value == "split")
            credit_sel.set_visibility(amount_mode.value == "split")
            rows = importer.parse_rows(state["parsed"], current_profile())
            if account.value:
                rows = importer.analyse(ctx, rows, account.value)
            state["rows"] = rows
            offer_balances(import_balances.from_rows(rows))
            render()

        def refresh_statement() -> None:
            statement = state["statement"]
            problem = camt.currency_problem(ctx, statement, account.value) if account.value else ""
            rows = statement.fresh_rows()
            if account.value and not problem:
                rows = importer.analyse(ctx, rows, account.value)
            elif problem:
                error(problem)
            state["rows"], state["problem"] = rows, problem
            offer_balances(import_balances.BalanceOffer() if problem else import_balances.from_statement(statement))
            render()

        def offer_balances(offer: import_balances.BalanceOffer) -> None:
            state["balances"] = import_balances.compare(ctx, offer.candidates, account.value or "")
            state["balance_problem"] = offer.problem

        def render() -> None:
            acc = ctx.store.accounts.get(account.value) if account.value else None
            currency = state["statement"].currency if state["statement"] else acc.currency if acc else ""
            grid.options["columnDefs"] = _columns(currency)
            grid.options["rowData"] = [
                {
                    "id": str(r.index),
                    "include": r.include,
                    "date": r.date.isoformat() if r.date else "",
                    "text": r.text,
                    "amount": r.amount,
                    "category": (
                        t("{category} (suggested)", category=category_labels.get(r.category, ""))
                        if r.suggested and r.category
                        else category_labels.get(r.category, "")
                    ),
                    "status": r.error or (t(STATUS[r.status]) if r.status in STATUS else ""),
                }
                for r in state["rows"]
            ]
            visible_rows = min(len(state["rows"]), 12)
            grid.style(f"height: {visible_rows * 40 + 100}px; min-height: 180px")  # fit short files
            grid.update()
            bar.set_visibility(False)
            render_balances(currency)
            update_status()

        def render_balances(currency: str) -> None:
            candidates, problem = state["balances"], state["balance_problem"]
            balances_box.set_visibility(bool(candidates or problem))
            balances_list.clear()
            with balances_list:
                if problem:
                    ui.label(problem).classes("caption")
                for c in candidates:
                    with ui.row().classes("items-center gap-x-2 gap-y-0"):
                        text = t(
                            "Balance at end of {month}: {amount}",
                            month=months.label(c.month),
                            amount=fmt(c.amount, currency),
                        )
                        ui.checkbox(text, value=c.include, on_change=lambda e, c=c: tick_balance(c, e.value)).props(
                            "dense"
                        )
                        notes = []
                        if not c.complete:
                            notes.append(
                                t("the file ends on {date} — not a month-end balance", date=date_label(c.date))
                            )
                        if c.replaces:
                            notes.append(t("replaces {amount}", amount=fmt(c.recorded, currency)))
                        elif c.unchanged:
                            notes.append(t("already recorded"))
                        if notes:
                            ui.label(" · ".join(notes)).classes("caption")

        def tick_balance(candidate: import_balances.BalanceCandidate, value: bool) -> None:
            candidate.include = bool(value)
            update_status()

        def update_status() -> None:
            rows = state["rows"]
            counts = {s: sum(1 for r in rows if r.status == s) for s in STATUS}
            errors = sum(1 for r in rows if r.status == importer.ERROR)
            parts = [tn("{n} new", "{n} new", counts[importer.NEW])]
            if counts[importer.DUPLICATE]:
                parts.append(tn("{n} already imported", "{n} already imported", counts[importer.DUPLICATE]))
            if counts[importer.POSSIBLE_DUPLICATE]:
                n = counts[importer.POSSIBLE_DUPLICATE]
                parts.append(tn("{n} possible duplicate", "{n} possible duplicates", n))
            if errors:
                parts.append(tn("{n} unreadable", "{n} unreadable", errors))
            summary.set_text(
                t(
                    "{counts}. Duplicates and unreadable lines are left out; tick them to import anyway.",
                    counts=" · ".join(parts),
                )
            )
            chosen = [r for r in rows if r.include]
            missing = [r for r in chosen if not importer.valid_category(ctx, r.type, r.category)]
            balances = [c for c in state["balances"] if c.include]
            if balances:
                import_btn.set_text(
                    t(
                        "Import {transactions} and {balances}",
                        transactions=tn("{n} Transaction", "{n} Transactions", len(chosen)),
                        balances=tn("{n} Balance", "{n} Balances", len(balances)),
                    )
                )
            else:
                import_btn.set_text(tn("Import {n} Transaction", "Import {n} Transactions", len(chosen)))
            if not account.value:
                status.set_text(t("Choose the account this file belongs to."))
            elif state["problem"]:
                status.set_text(state["problem"])
            elif missing:
                status.set_text(
                    tn(
                        "{n} row needs a category: select rows, choose a category and click Apply.",
                        "{n} rows need a category: select rows, choose a category and click Apply.",
                        len(missing),
                    )
                )
            else:
                status.set_text("")
            import_btn.set_enabled(
                bool(account.value and (chosen or balances) and not missing and not state["problem"])
            )

        async def selected_rows() -> list[importer.ImportRow]:
            ids = {int(r["id"]) for r in await grid.get_selected_rows()}
            return [r for r in state["rows"] if r.index in ids]

        async def selection_changed() -> None:
            rows = await selected_rows()
            bar.set_visibility(bool(rows))
            selected_label.set_text(tn("{n} selected", "{n} selected", len(rows)))

        async def apply_category() -> None:
            if not bulk_cat.value:
                error(t("Choose a category first"))
                return
            rows = await selected_rows()
            fitting = [r for r in rows if importer.valid_category(ctx, r.type, bulk_cat.value)]
            for r in fitting:
                r.category, r.suggested = bulk_cat.value, False
            skipped = len(rows) - len(fitting)
            if skipped:
                if importer.valid_category(ctx, TX_INCOME, bulk_cat.value):
                    error(
                        tn(
                            "{n} row skipped: that category is for money coming in only",
                            "{n} rows skipped: that category is for money coming in only",
                            skipped,
                        )
                    )
                else:
                    error(
                        tn(
                            "{n} row skipped: that category is for spending only",
                            "{n} rows skipped: that category is for spending only",
                            skipped,
                        )
                    )
            render()

        async def set_include(value: bool) -> None:
            for r in await selected_rows():
                if r.status != importer.ERROR:
                    r.include = value
            render()

        def cell_changed(e: events.GenericEventArguments) -> None:
            data = e.args.get("data") or {}
            row = next((r for r in state["rows"] if str(r.index) == data.get("id")), None)
            if row is not None and e.args.get("colId") == "include":
                row.include = bool(e.args.get("newValue")) and row.status != importer.ERROR
                update_status()

        def save_settings() -> None:
            if state["parsed"] is None:
                return
            profile = current_profile()
            if not profile.name:
                error(t("Enter a name for these settings"))
                return
            importer.save_profile(ctx.store.data_dir, profile)
            profiles[:] = importer.load_profiles(ctx.store.data_dir)
            profile_sel.set_options({p.name: p.name for p in profiles}, value=profile.name)
            success(t("Saved “{name}”", name=profile.name))

        def choose_profile() -> None:
            chosen = next((p for p in profiles if p.name == profile_sel.value), None)
            if chosen and state["parsed"] is not None:
                if chosen.delimiter != state["parsed"].delimiter or chosen.header_row != state["parsed"].header_row:
                    state["parsed"] = importer.reparse(state["parsed"], chosen.delimiter, chosen.header_row)
                apply_profile(chosen)

        def do_import() -> None:
            try:
                if state["statement"] is not None:
                    saved = camt.commit(ctx, state["statement"], state["rows"], account.value)
                else:
                    saved = importer.commit(ctx, state["rows"], account.value)
                entries = import_balances.save(ctx, account.value, state["balances"])
            except ValidationError as exc:
                error(str(exc))
                return
            message = tn("Imported {n} transaction", "Imported {n} transactions", len(saved))
            if entries:
                message += " · " + tn("{n} month-end balance recorded", "{n} month-end balances recorded", len(entries))
            success(message)
            ui.navigate.to("/transactions")

        for el in (
            date_sel,
            date_fmt,
            amount_mode,
            decimal,
            amount_sel,
            debit_sel,
            credit_sel,
            balance_sel,
            text_sel,
            invert,
        ):
            el.on_value_change(refresh_rows)
        account.on_value_change(refresh_rows)
        delimiter.on_value_change(reread)
        header_line.on_value_change(reread)
        profile_sel.on_value_change(choose_profile)
        grid.on("selectionChanged", selection_changed)
        grid.on("cellValueChanged", cell_changed)
