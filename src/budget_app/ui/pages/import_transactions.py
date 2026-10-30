"""Import Transactions: a bank file in two screens, designed from the user's side (decision 48).

1 · Your file: what the file holds in one sentence ("1'900 transactions from UBS Private, 1 Nov 2023 to 30 Oct
2026"), the account it goes into, and plain checks, each with a way out: money in and out the right way round (an
example of each; *That's Wrong* swaps them), what is already in Budget (skipped; *Show Them* lists each match beside
the transaction it matches, with *Import anyway*), lines that can't be read, and the month-end balances (F36). One
button imports everything new, with a category or without one (those wait under To sort). The column settings of a
CSV file stay folded behind *The Columns Look Wrong…*: a bank seen before needs none, and a camt.053 / camt.054
statement (XML, F02) needs none at all.

2 · A few questions: the account is up to date. The file's payees Budget could not place are asked about once each
(ui/widgets/sort_cards.py, the cards of the To Sort tab), then the categories that were only a guess from a word in
the payee's name (worth a second look); what was sorted from the rules and the history is listed, folded. Whatever
is skipped waits under To Sort.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from nicegui import events, ui

from budget_app.domain import months
from budget_app.i18n import N_, date_label, t, tn
from budget_app.services import camt, import_balances, importer, sorting
from budget_app.services.context import ValidationError
from budget_app.ui.layout import error, fmt, frame, picks_files, refresh_due_badge, segmented, success
from budget_app.ui.widgets.selects import account_options
from budget_app.ui.widgets.sort_cards import answer_label, guess_list, sort_cards

DELIMITER_NAMES = {";": N_("Semicolon ;"), ",": N_("Comma ,"), "\t": N_("Tab"), "|": N_("Pipe |")}
NONE = ""  # select value for "no column"
SHOWN = 50  # matches or unreadable lines listed before "and n more"


def page() -> None:
    with frame(t("Import Transactions"), "/import", narrow=True) as (ctx, _):
        profiles = importer.load_profiles(ctx.store)
        # parsed: the CSV file; statement: the camt file (one of them is set); source: the bank or file named in
        # the headline; problem: why the file can't be imported into the account; balances and balance_problem:
        # the month-end balances the file offers, or why none; open: the checks whose list is shown
        state: dict = {
            "parsed": None,
            "statement": None,
            "source": "",
            "rows": [],
            "problem": "",
            "balances": [],
            "balance_problem": "",
            "open": set(),
        }
        busy = {"value": False}

        # Quasar's uploader panel is hidden; a plain button opens the system file dialog instead
        upload = (
            ui.upload(auto_upload=True, max_file_size=importer.MAX_BYTES, on_upload=lambda e: on_file(e))  # noqa: PLW0108 - defined below
            .props('accept=".csv,.txt,.xml,text/csv,text/xml,application/xml"')
            .classes("hidden")
        )

        # ---------------------------------------------------------- choose a file
        with ui.element("div").classes("surface import-step") as choose_card:
            ui.label(t("Bring in a bank file")).classes("import-headline")
            ui.label(
                t(
                    "Export the transactions from your e-banking as CSV, or as a camt.053 or camt.054 "
                    "statement (XML). Nothing leaves your computer."
                )
            ).classes("caption")
            with ui.row():
                picks_files(ui.button(t("Choose File…"), icon="o_upload_file").props("unelevated"), upload)

        # ---------------------------------------------------------- 1 · your file
        with ui.element("div").classes("surface import-step") as file_card:
            with ui.element("div").classes("import-file"):
                kind = ui.label().classes("import-kind")
                headline = ui.label().classes("import-headline")
            with ui.row().classes("items-center gap-2"):
                ui.label(t("Into")).classes("text-2")
                account = (
                    ui.select(account_options(ctx), label=t("Account"), with_input=True)
                    .props("dense outlined")
                    .classes("w-64")
                )
                account_hint = ui.label().classes("caption")
            problem = ui.label().classes("import-problem")
            checks = ui.element("div").classes("import-checks")
            with ui.row().classes("w-full items-center gap-2"):
                import_btn = ui.button(t("Import"), on_click=lambda: do_import()).props("unelevated")  # noqa: PLW0108 - defined below
                ui.space()
                columns_link = ui.button(t("The Columns Look Wrong…"), on_click=lambda: toggle_columns()).props(  # noqa: PLW0108 - defined below
                    "flat no-caps"
                )
                columns_link.classes("text-primary")
                picks_files(ui.button(t("Choose Another File…")).props("flat no-caps").classes("text-primary"), upload)
        file_card.set_visibility(False)

        # ---------------------------------------------------------- the columns of a CSV file, folded
        with ui.element("div").classes("surface flex flex-col gap-3") as columns_card:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(t("Columns")).classes("section-title")
                ui.space()
                profile_sel = (
                    ui.select({p.name: p.name for p in profiles}, label=t("Saved settings"), clearable=True)
                    .props("dense outlined options-dense")
                    .classes("w-56")
                )
            with ui.row().classes("w-full gap-3 items-start flex-wrap"):
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

        # ---------------------------------------------------------- 2 · a few questions
        done_area = ui.element("div").classes("flex flex-col gap-5 w-full")
        done_area.set_visibility(False)

        # ---------------------------------------------------------- reading the file
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
                known = bool(profile.account_id and ctx.store.accounts.get(profile.account_id))
                if known:
                    account.value = profile.account_id
                account_hint.set_text(t("The account of this bank's last file.") if known else "")
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
            state["open"] = set()
            choose_card.set_visibility(False)
            file_card.set_visibility(True)
            if statement is not None:
                open_statement(statement)
            elif parsed is not None:
                open_csv(parsed)

        def open_statement(statement: camt.Statement) -> None:
            state["parsed"], state["statement"] = None, statement
            state["source"] = camt.format_iban(statement.iban) or statement.name
            kind.set_text("XML")
            columns_card.set_visibility(False)
            columns_link.set_visibility(False)
            remembered = camt.remembered_account(ctx.store, statement.iban)
            known = bool(remembered and ctx.store.accounts.get(remembered))
            account_hint.set_text(
                t("Remembered from the last statement of this IBAN.")
                if known
                else t("It is remembered for this IBAN after the import.")
            )
            busy["value"] = True
            try:
                account.value = remembered if known else None
            finally:
                busy["value"] = False
            refresh_rows()

        def open_csv(parsed: importer.ParsedFile) -> None:
            state["statement"], state["problem"] = None, ""
            state["parsed"] = parsed
            profile = importer.guess_profile(parsed, profiles)
            state["source"] = profile.name or parsed.name
            kind.set_text("CSV")
            columns_link.set_visibility(True)
            if profile.name:
                profile_sel.value = profile.name
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
            statement = state["statement"]
            if statement is not None:
                state["problem"] = camt.currency_problem(ctx, statement, account.value) if account.value else ""
                rows = statement.fresh_rows()
                offer = (
                    import_balances.BalanceOffer() if state["problem"] else import_balances.from_statement(statement)
                )
            else:
                amount_sel.set_visibility(amount_mode.value == "single")
                debit_sel.set_visibility(amount_mode.value == "split")
                credit_sel.set_visibility(amount_mode.value == "split")
                rows = importer.parse_rows(state["parsed"], current_profile())
                offer = None
            if account.value and not state["problem"]:
                rows = importer.analyse(ctx, rows, account.value)
            state["rows"] = rows
            offer = offer or import_balances.from_rows(rows)
            state["balances"] = import_balances.compare(ctx, offer.candidates, account.value or "")
            state["balance_problem"] = offer.problem
            render()

        # ---------------------------------------------------------- 1 · your file
        def render() -> None:
            rows = state["rows"]
            dated = [r.date for r in rows if r.date]
            first, last = (min(dated), max(dated)) if dated else (None, None)
            statement = state["statement"]
            if statement is not None and statement.start and statement.end:  # the period the statement covers
                first, last = statement.start, statement.end
            n = len(rows)
            if first and last:
                headline.set_text(
                    tn(
                        "{n} transaction from {source}, {first} to {last}",
                        "{n} transactions from {source}, {first} to {last}",
                        n,
                        source=state["source"],
                        first=date_label(first),
                        last=date_label(last),
                    )
                )
            else:
                headline.set_text(tn("{n} line in {source}", "{n} lines in {source}", n, source=state["source"]))
            if not account.value:
                problem.set_text(t("Choose the account this file belongs to."))
            else:
                problem.set_text(state["problem"])
            render_checks()
            chosen = sum(1 for r in rows if r.include)
            balances = [c for c in state["balances"] if c.include]
            if balances:
                import_btn.set_text(
                    t(
                        "Import {transactions} and {balances}",
                        transactions=tn("{n} Transaction", "{n} Transactions", chosen),
                        balances=tn("{n} Balance", "{n} Balances", len(balances)),
                    )
                )
            else:
                import_btn.set_text(tn("Import {n} Transaction", "Import {n} Transactions", chosen))
            import_btn.set_enabled(bool(account.value and (chosen or balances) and not state["problem"]))

        def toggle(name: str) -> None:
            state["open"] ^= {name}
            render_checks()

        def check(title: str, detail: str = "", action: str = "", on_click=None, ok: bool = True) -> None:
            with ui.element("div").classes("import-check"):
                ui.label("✓" if ok else "!").classes("import-tick" + ("" if ok else " warn"))
                with ui.element("div").classes("flex flex-col gap-0 min-w-0"):
                    ui.label(title).classes("font-semibold")
                    if detail:
                        ui.label(detail).classes("caption")
                if action:
                    ui.button(action, on_click=on_click).props("flat dense no-caps").classes("text-primary")

        def render_checks() -> None:
            checks.clear()
            rows = state["rows"]
            if not account.value or state["problem"]:
                return
            readable = [r for r in rows if r.status != importer.ERROR and r.amount is not None]
            new = [r for r in readable if r.status in (importer.NEW, importer.POSSIBLE_DUPLICATE)]
            matched = [r for r in rows if r.status == importer.POSSIBLE_DUPLICATE]
            before = [r for r in rows if r.status == importer.DUPLICATE]
            unreadable = [r for r in rows if r.status == importer.ERROR]
            with checks:
                if new:
                    signs_check(new)
                if not new and before:
                    check(t("Everything in this file is already in Budget"), t("Nothing is imported twice."))
                elif before:
                    check(
                        tn("{n} was imported before", "{n} were imported before", len(before)),
                        t("They are skipped."),
                    )
                if matched:
                    matches_check(matched)
                if unreadable:
                    unreadable_check(unreadable)
                if state["statement"] is not None and state["statement"].pending:
                    n = state["statement"].pending
                    check(
                        tn("{n} entry is still pending", "{n} entries are still pending", n),
                        t("The bank hasn't booked them yet; they come with a later statement."),
                    )
                balances_check()

        def signs_check(new: list[importer.ImportRow]) -> None:
            money_in = max((r for r in new if r.amount and r.amount > 0), key=lambda r: r.amount or 0, default=None)
            outs = [r for r in new if r.amount and r.amount < 0]
            common = Counter(r.payee or r.text for r in outs).most_common(1)
            money_out = next((r for r in outs if (r.payee or r.text) == common[0][0]), None) if common else None
            examples = [
                f"{r.payee or r.text} {'+' if (r.amount or 0) > 0 else ''}{fmt(r.amount)}"
                for r in (money_in, money_out)
                if r
            ]
            check(
                t("Money in and out are the right way round"),
                " · ".join(examples),
                t("That's Wrong") if state["statement"] is None else "",
                lambda: invert.set_value(not invert.value),
            )

        def matches_check(matched: list[importer.ImportRow]) -> None:
            check(
                tn("{n} is already in Budget", "{n} are already in Budget", len(matched)),
                t("Same account, day and amount as a transaction you have. Skipped, so nothing counts twice."),
                t("Hide Them") if "matches" in state["open"] else t("Show Them"),
                lambda: toggle("matches"),
            )
            if "matches" not in state["open"]:
                return
            with ui.element("div").classes("import-list"):
                for r in matched[:SHOWN]:
                    have = ctx.store.transactions.get(r.match_id)
                    with ui.element("div").classes("import-pair"):
                        ui.checkbox(
                            t("Import anyway"), value=r.include, on_change=lambda e, r=r: include(r, e.value)
                        ).props("dense")
                        with ui.element("div").classes("flex flex-col gap-0 min-w-0"):
                            when = f"{date_label(r.date)} · " if r.date else ""
                            ui.label(f"{when}{r.payee or r.text} · {fmt(r.amount)}")
                            if have is not None:
                                category = answer_label(ctx, have.type, have.category) if have.category else ""
                                ui.label(
                                    t(
                                        "You have: {what}",
                                        what=" · ".join(p for p in (have.payee or have.comment, category) if p),
                                    )
                                ).classes("caption")
                if len(matched) > SHOWN:
                    ui.label(tn("and {n} more", "and {n} more", len(matched) - SHOWN)).classes("caption")

        def include(row: importer.ImportRow, value: bool) -> None:
            row.include = bool(value)
            render()

        def unreadable_check(unreadable: list[importer.ImportRow]) -> None:
            check(
                tn("{n} line can't be read", "{n} lines can't be read", len(unreadable)),
                t("{reason}. They are left out.", reason=unreadable[0].error),
                t("Hide Them") if "errors" in state["open"] else t("Show Them"),
                lambda: toggle("errors"),
                ok=False,
            )
            if "errors" in state["open"]:
                with ui.element("div").classes("import-list"):
                    for r in unreadable[:SHOWN]:
                        ui.label(
                            t("Line {n}: {error}", n=r.index + 1, error=r.error) + (f" · {r.text}" if r.text else "")
                        ).classes("caption")

        def balances_check() -> None:
            candidates, reason = state["balances"], state["balance_problem"]
            if reason and not candidates:
                check(t("No month-end balances"), reason, ok=False)
                return
            if not candidates:
                return
            ticked = sum(1 for c in candidates if c.include)
            check(
                tn("{n} month-end balance to record", "{n} month-end balances to record", ticked)
                if ticked
                else tn(
                    "{n} month-end balance found, none ticked",
                    "{n} month-end balances found, none ticked",
                    len(candidates),
                ),
                t("Your Wealth page shows the bank's own balance at the end of each month."),
                t("Hide Them") if "balances" in state["open"] else t("Choose…"),
                lambda: toggle("balances"),
            )
            if "balances" not in state["open"]:
                return
            currency = state["statement"].currency if state["statement"] else account_currency()
            with ui.element("div").classes("import-list"):
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

        def account_currency() -> str:
            acc = ctx.store.accounts.get(account.value) if account.value else None
            return acc.currency if acc else ""

        def tick_balance(candidate: import_balances.BalanceCandidate, value: bool) -> None:
            candidate.include = bool(value)
            render()

        def toggle_columns() -> None:
            columns_card.set_visibility(not columns_card.visible)

        def save_settings() -> None:
            if state["parsed"] is None:
                return
            profile = current_profile()
            if not profile.name:
                error(t("Enter a name for these settings"))
                return
            importer.save_profile(ctx.store, profile)
            profiles[:] = importer.load_profiles(ctx.store)
            profile_sel.set_options({p.name: p.name for p in profiles}, value=profile.name)
            success(t("Saved “{name}”", name=profile.name))

        def choose_profile() -> None:
            chosen = next((p for p in profiles if p.name == profile_sel.value), None)
            if chosen and state["parsed"] is not None:
                if chosen.delimiter != state["parsed"].delimiter or chosen.header_row != state["parsed"].header_row:
                    state["parsed"] = importer.reparse(state["parsed"], chosen.delimiter, chosen.header_row)
                apply_profile(chosen)

        # ---------------------------------------------------------- import, then 2 · a few questions
        def do_import() -> None:
            rows = state["rows"]
            try:
                saved, entries = import_balances.import_file(
                    ctx, rows, account.value, state["balances"], state["statement"]
                )
            except ValidationError as exc:
                error(str(exc))
                return
            included = [r for r in rows if r.include]  # saved in this order
            guesses = {tx.id: r.reason for r, tx in zip(included, saved, strict=True) if r.guessed}
            sorted_before = Counter(
                (r.payee or r.text, r.type, r.category) for r in included if r.category and not r.guessed
            )
            # up to date to the file's last day, whether that day's rows were new or already in Budget
            last = max((r.date for r in rows if r.date), default=None)
            if state["statement"] is not None and state["statement"].end:
                last = max(filter(None, (last, state["statement"].end)))
            acc = ctx.store.accounts.get(account.value)
            done(acc.name if acc else "", last, [tx.id for tx in saved], len(entries), guesses, sorted_before)
            refresh_due_badge()  # the payees to sort

        def done(
            account_name: str,
            last: date | None,
            ids: list[str],
            balances: int,
            guesses: dict[str, str],
            sorted_before: Counter,
        ) -> None:
            for card in (choose_card, file_card, columns_card):
                card.set_visibility(False)
            done_area.clear()
            done_area.set_visibility(True)
            with done_area, ui.element("div").classes("surface import-step"):
                with ui.element("div").classes("import-file"):
                    ui.label("✓").classes("import-tick big")
                    ui.label(
                        t("{account} is up to date to {date}", account=account_name, date=date_label(last))
                        if last
                        else t("Imported")
                    ).classes("import-headline")
                summary = tn("{n} transaction imported.", "{n} transactions imported.", len(ids))
                if balances:
                    summary += " " + tn("{n} month-end balance recorded.", "{n} month-end balances recorded.", balances)
                ui.label(summary + " " + t("Undo the whole import from History at any time.")).classes("caption -mt-3")

                @ui.refreshable
                def head() -> None:
                    waiting = len(sorting.groups(ctx, ids))
                    if waiting:
                        ui.label(
                            tn("{n} payee Budget doesn't know yet", "{n} payees Budget doesn't know yet", waiting)
                        ).classes("section-title")
                        ui.label(t("One answer sorts every transaction of a payee.")).classes("caption -mt-3")

                head()
                if sorting.groups(ctx, ids):
                    sort_cards(ctx, ids=ids, on_change=head.refresh)
                guess_list(ctx, guesses)
                if sorted_before:
                    n = sum(sorted_before.values())
                    with (
                        ui.expansion(
                            tn("{n} sorted the way you sorted before", "{n} sorted the way you sorted before", n)
                        )
                        .props("dense")
                        .classes("import-sorted")
                    ):
                        for (name, type_, code), count in sorted_before.most_common(30):
                            ui.label(f"{name} → {answer_label(ctx, type_, code)} · {count}").classes("caption")
                with ui.row().classes("items-center gap-3"):
                    ui.button(t("Show Transactions"), on_click=lambda: ui.navigate.to("/transactions")).props(
                        "unelevated"
                    )
                    if sorting.groups(ctx, ids):
                        ui.label(t("Skip the rest: it waits under To Sort.")).classes("caption")

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
