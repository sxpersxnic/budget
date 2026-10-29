"""Settings: budget horizon, display currency, accounts, advance accounts, income categories, data."""

from __future__ import annotations

from datetime import date

from nicegui import events, run, ui

from budget_app import i18n
from budget_app.config import BASE_CURRENCY, RELEASES_URL
from budget_app.domain import months
from budget_app.domain.models import (
    ACCOUNT_ADVANCE,
    ACCOUNT_REGULAR,
    ASSET_CASH,
    ASSET_CLASSES,
    HOLDING_CLASSES,
    PROJECTION_BUDGET,
    PROJECTION_HIGHER,
)
from budget_app.i18n import N_, t, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import archive
from budget_app.services import budget as budget_svc
from budget_app.services import demo as demo_svc
from budget_app.services import export as export_svc
from budget_app.services import investments as inv_svc
from budget_app.services import reminders as rem_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import updates as updates_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import (
    error,
    fmt,
    frame,
    month_options,
    refresh_due_badge,
    save_file,
    sheet,
    sheet_buttons,
    success,
    undoable,
)
from budget_app.ui.pages import welcome
from budget_app.ui.pages.investments import open_conversion_sheet
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import currency_select

ROW_ACTION = "flat dense round size=sm"


def _account_sheet(ctx: Context, kind: str, refresh, acc=None) -> None:
    """Add (acc=None) or edit an account."""
    advance = kind == ACCOUNT_ADVANCE
    if acc:
        title = t("Edit Advance Payment Account") if advance else t("Edit Account")
    else:
        title = t("New Advance Payment Account") if advance else t("New Account")
    in_use = acc is not None and acc_svc.account_in_use(ctx, acc.id)
    with sheet(title, width="26rem") as dlg:
        name = ui.input(t("Name"), value=acc.name if acc else "").props("dense outlined autofocus").classes("w-full")
        cur = currency_select(ctx, acc.currency if acc else ctx.store.settings.display_currency)
        if in_use:
            cur.disable()
            ui.label(t("The currency can’t change while transactions or recorded balances use this account.")).classes(
                "caption -mt-2"
            )
        elif not acc:
            ui.label(t("Every transaction on this account will be in this currency.")).classes("caption -mt-2")
        asset_class = None
        if not advance:  # F21: what the account holds; advance-payment accounts are cash
            asset_class = (
                ui.select(
                    {k: t(v) for k, v in ASSET_CLASSES.items()},
                    value=acc.asset_class if acc and acc.asset_class in ASSET_CLASSES else ASSET_CASH,
                    label=t("Asset class"),
                )
                .props("dense outlined options-dense")
                .classes("w-full")
            )
            class_note = ui.label().classes("caption -mt-2")

            def explain() -> None:
                fresh = ctx.store.accounts.get(acc.id) if acc else None
                if fresh is not None and fresh.invest_from and asset_class.value in HOLDING_CLASSES:
                    text = t(
                        "Valued from its holdings from {month} on; the balances recorded before stay as its history.",
                        month=months.label(fresh.invest_from),
                    )
                elif asset_class.value in HOLDING_CLASSES:
                    text = t("Valued from its holdings on the Investments page; no balances to record.")
                else:
                    text = t("Valued from the balances you record at the end of each month.")
                class_note.set_text(text)

            def class_chosen() -> None:
                chosen = asset_class.value
                if acc is not None and inv_svc.needs_conversion(ctx, acc.id, chosen):
                    # an account with history becomes a securities or crypto account through a conversion (decision 13)
                    fresh = ctx.store.accounts.get(acc.id)
                    asset_class.value = fresh.asset_class if fresh else ASSET_CASH  # until the conversion is saved

                    def converted() -> None:  # the conversion saved the class: this sheet is done
                        dlg.close()
                        refresh()

                    open_conversion_sheet(ctx, acc.id, chosen, converted)
                explain()

            asset_class.on_value_change(class_chosen)
            explain()

        def save():
            chosen = asset_class.value if asset_class else ASSET_CASH
            try:
                if acc:
                    acc_svc.edit_account(ctx, acc.id, name.value, cur.value, chosen)
                else:
                    acc_svc.add_account(ctx, name.value, cur.value, kind, chosen)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            refresh()

        sheet_buttons(dlg, t("Save") if acc else t("Add"), save)
        name.on("keydown.enter", save)


def _accounts_group(ctx: Context, kind: str, title: str) -> None:
    @ui.refreshable
    def body():
        rows = acc_svc.accounts(ctx, kind)
        with ui.element("div").classes("group"):
            for acc in rows:
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(acc.name).classes("truncate")
                        if kind == ACCOUNT_REGULAR and acc.holds_securities:
                            ui.label(
                                t(
                                    "{asset_class} · {amount} in holdings",
                                    asset_class=t(ASSET_CLASSES[acc.asset_class]),
                                    amount=fmt(inv_svc.account_value(ctx, acc.id), acc.currency),
                                )
                            ).classes("caption")
                        elif kind == ACCOUNT_REGULAR:
                            last = wealth_svc.balance_entry(ctx, acc.id, "9999-12")
                            balance = (
                                t(
                                    "{amount} at the end of {month}",
                                    amount=fmt(last.amount, acc.currency),
                                    month=months.label(last.month),
                                )
                                if last
                                else t("No balance recorded")
                            )
                            if acc.asset_class != ASSET_CASH:
                                balance = (
                                    f"{t(ASSET_CLASSES.get(acc.asset_class, ASSET_CLASSES[ASSET_CASH]))} · {balance}"
                                )
                            ui.label(balance).classes("caption")
                        else:
                            ui.label(
                                t(
                                    "{amount} outstanding",
                                    amount=fmt(tx_svc.advance_account_balance(ctx, acc.id), acc.currency),
                                )
                            ).classes("caption")
                    ui.label(acc.currency).classes("text-2 text-[13px]")
                    with ui.row().classes("row-actions gap-0 no-wrap"):
                        ui.button(
                            icon="o_edit", on_click=lambda a=acc: _account_sheet(ctx, kind, body.refresh, a)
                        ).props(ROW_ACTION).classes("text-primary").tooltip(t("Edit"))

                        def delete(a=acc):
                            def do():
                                try:
                                    acc_svc.delete_account(ctx, a.id)
                                    undoable(t("Deleted {name}", name=a.name))
                                except ValidationError as exc:
                                    error(str(exc))
                                body.refresh()

                            confirm(
                                t("Delete “{name}”?", name=a.name),
                                do,
                                detail=t(
                                    "Its recorded balances are removed too. "
                                    "Accounts with transactions can’t be deleted."
                                ),
                            )

                        ui.button(icon="o_delete", on_click=delete).props(ROW_ACTION).classes("text-negative").tooltip(
                            t("Delete")
                        )
            with ui.element("div").classes("group-row"):
                ui.button(
                    t("Add Advance Payment Account…") if kind == ACCOUNT_ADVANCE else t("Add Account…"),
                    on_click=lambda: _account_sheet(ctx, kind, body.refresh),
                ).props("flat dense no-caps").classes("link-btn -ml-2")

    with ui.column().classes("w-full gap-0"):
        ui.label(title).classes("group-header")
        body()


def _category_sheet(ctx: Context, refresh, cat=None) -> None:
    with sheet(t("Rename Income Category") if cat else t("New Income Category"), width="24rem") as dlg:
        name = ui.input(t("Name"), value=cat.name if cat else "").props("dense outlined autofocus").classes("w-full")

        def save():
            try:
                if cat:
                    acc_svc.rename_income_category(ctx, cat.id, name.value)
                else:
                    acc_svc.add_income_category(ctx, name.value)
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            refresh()

        sheet_buttons(dlg, t("Save") if cat else t("Add"), save)
        name.on("keydown.enter", save)


def _income_group(ctx: Context) -> None:
    @ui.refreshable
    def body():
        with ui.element("div").classes("group"):
            for cat in acc_svc.income_categories(ctx):
                with ui.element("div").classes("group-row"):
                    ui.label(cat.name).classes("flex-1 truncate")
                    with ui.row().classes("row-actions gap-0 no-wrap"):
                        ui.button(icon="o_edit", on_click=lambda c=cat: _category_sheet(ctx, body.refresh, c)).props(
                            ROW_ACTION
                        ).classes("text-primary").tooltip(t("Rename"))

                        def delete(c=cat):
                            def do():
                                try:
                                    acc_svc.delete_income_category(ctx, c.id)
                                except ValidationError as exc:
                                    error(str(exc))
                                else:
                                    undoable(t("Deleted {name}", name=c.name))
                                body.refresh()

                            confirm(
                                t("Delete “{name}”?", name=c.name),
                                do,
                                detail=t("Categories that are in use can’t be deleted."),
                            )

                        ui.button(icon="o_delete", on_click=delete).props(ROW_ACTION).classes("text-negative").tooltip(
                            t("Delete")
                        )
            with ui.element("div").classes("group-row"):
                ui.button(t("Add Category…"), on_click=lambda: _category_sheet(ctx, body.refresh)).props(
                    "flat dense no-caps"
                ).classes("link-btn -ml-2")

    with ui.column().classes("w-full gap-0"):
        ui.label(t("Income Categories")).classes("group-header")
        body()
        with ui.element("div").classes("group mt-3"), ui.element("div").classes("group-row"):
            with ui.column().classes("gap-0 flex-1 min-w-0"):
                ui.label(t("Categorization Rules"))
                ui.label(t("Which category new and imported transactions are given for a payee or bank text.")).classes(
                    "caption"
                )
            ui.button(t("Rules…"), on_click=lambda: ui.navigate.to("/rules")).props("flat dense no-caps").classes(
                "text-primary"
            )


def _reminders_group(ctx: Context) -> None:
    """F31: which kinds of reminders the Reminders page and its badge show; all in the app, nothing is sent."""

    def toggle(kind: str, on: bool) -> None:
        rem_svc.set_enabled(ctx, kind, on)
        refresh_due_badge()

    with ui.column().classes("w-full gap-0"):
        ui.label(t("Reminders")).classes("group-header")
        with ui.element("div").classes("group"):
            for kind in rem_svc.KINDS:
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t(rem_svc.TITLES[kind]))
                        ui.label(t(rem_svc.DESCRIPTIONS[kind])).classes("caption")
                    ui.switch(
                        value=rem_svc.enabled(ctx, kind), on_change=lambda e, k=kind: toggle(k, bool(e.value))
                    ).props("dense").mark(f"reminder-{kind}")
        ui.label(t("Shown on the Reminders page and counted in the sidebar while the app runs.")).classes(
            "caption px-1 pt-1"
        )


def _restore(ctx: Context, files: dict[str, bytes], source: str) -> None:
    """Ask, then replace every record with `files`; the current ones go to the backups folder first."""

    def do() -> None:
        try:
            safety = archive.restore(ctx, files)
        except ValidationError as exc:
            error(str(exc))
            return
        i18n.set_language(ctx.store.settings.language)
        success(t("Restored. The previous records are kept in {folder}.", folder=safety.name))
        ui.navigate.reload()

    confirm(
        t("Replace all records with {source}?", source=source),
        do,
        action=t("Restore"),
        detail=t("The current records are copied to the backups folder first, so this can be undone."),
    )


def _sample_row(ctx: Context) -> None:
    """F30: load the sample set into an empty folder, or remove it again (the folder is empty afterwards)."""
    with ui.element("div").classes("group-row"):
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            ui.label(t("Sample Data"))
            if ctx.store.settings.sample_data:
                caption = t("This folder holds the sample set. Removing it empties the folder, as on the first start.")
            elif demo_svc.is_empty(ctx):
                caption = t("A year of example records to explore the app with. It can be removed again here.")
            else:
                caption = t("Only an empty data folder can take the sample data.")
            ui.label(caption).classes("caption")
        if ctx.store.settings.sample_data:

            def remove() -> None:
                def do() -> None:
                    try:
                        demo_svc.remove(ctx)
                    except ValidationError as exc:
                        error(str(exc))
                        return
                    ui.navigate.to("/")  # the welcome screen, as on the first start

                confirm(
                    t("Remove the sample data?"),
                    do,
                    action=t("Remove"),
                    detail=t(
                        "Every record, attachment and the change history in this folder is deleted. "
                        "Your language and currency settings stay."
                    ),
                )

            ui.button(t("Remove Sample Data…"), on_click=remove).props("flat dense no-caps").classes("text-negative")
        else:
            load = ui.button(t("Load Sample Data")).props("flat dense no-caps").classes("text-primary")
            load.on_click(lambda: welcome.load_sample(ctx, load))
            load.set_enabled(demo_svc.is_empty(ctx))


def _data_group(ctx: Context) -> None:
    async def export_json() -> None:
        await save_file(export_svc.everything_json(ctx), f"budget-export_{date.today().isoformat()}.json")

    async def back_up() -> None:
        await save_file(archive.create(ctx), archive.file_name())

    async def on_backup_file(e: events.UploadEventArguments) -> None:
        try:
            files = archive.read(await e.file.read())
            archive.check(files)
        except ValidationError as exc:
            error(str(exc))
            return
        finally:
            upload.reset()
        _restore(ctx, files, t("the backup “{name}”", name=e.file.name))

    def restore_saved(b: archive.SavedBackup) -> None:
        try:
            files = archive.files_in(b.path)
        except ValidationError as exc:
            error(str(exc))
            return
        _restore(ctx, files, t("the backup of {date}", date=_backup_date(b)))

    upload = (
        ui.upload(auto_upload=True, max_file_size=archive.MAX_TOTAL_BYTES, on_upload=on_backup_file)
        .props('accept=".zip,application/zip"')
        .classes("hidden")
    )
    with ui.column().classes("w-full gap-0"):
        ui.label(t("Data")).classes("group-header")
        with ui.element("div").classes("group"):
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Back Up"))
                    ui.label(
                        t("All records in one ZIP file. Keep it somewhere safe; it can be restored here.")
                    ).classes("caption")
                ui.button(t("Back Up Now…"), on_click=back_up).props("flat dense no-caps").classes("text-primary")
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Restore"))
                    ui.label(t("Replaces all records with those of a backup file.")).classes("caption")
                ui.button(t("Choose Backup…"), on_click=lambda: upload.run_method("pickFiles")).props(
                    "flat dense no-caps"
                ).classes("text-primary")
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Export Everything"))
                    ui.label(t("All records as one JSON file, for other programs. It can’t be restored.")).classes(
                        "caption"
                    )
                ui.button(t("Export as JSON…"), on_click=export_json).props("flat dense no-caps").classes(
                    "text-primary"
                )
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Change History"))
                    ui.label(t("The last changes to your records, with Undo.")).classes("caption")
                ui.button(t("Show…"), on_click=lambda: ui.navigate.to("/history")).props("flat dense no-caps").classes(
                    "text-primary"
                )
            with ui.element("div").classes("group-row"), ui.column().classes("gap-0 flex-1 min-w-0"):
                ui.label(t("Data Folder"))
                ui.label(str(ctx.store.data_dir)).classes("caption break-all select-all")
            _sample_row(ctx)

    saved = archive.saved_backups(ctx)
    with ui.column().classes("w-full gap-0"):
        ui.label(t("Automatic Backups")).classes("group-header")
        with ui.element("div").classes("group"):
            if not saved:
                with ui.element("div").classes("group-row"):
                    ui.label(t("The app copies your records here before an update or a restore. None so far.")).classes(
                        "text-2"
                    )
            for b in saved:
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(_backup_date(b))
                        ui.label(t("Before a restore") if b.reason == "restore" else t("Before an update")).classes(
                            "caption"
                        )
                    ui.button(t("Restore…"), on_click=lambda b=b: restore_saved(b)).props("flat dense no-caps").classes(
                        "text-primary row-actions"
                    )


def _updates_group(ctx: Context) -> None:
    """F33: the update check, off by default; it says what it does and what it never does."""
    settings = ctx.store.settings

    @ui.refreshable
    def status() -> None:
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            ui.label(t("Newest Release"))
            release = updates_svc.available(ctx)
            if not settings.update_check:
                text = t("Off: the app never asks on its own. The releases page lists the installers.")
            elif not settings.update_checked:
                text = t("Not checked yet.")
            else:
                text = t(
                    "Checked on {date}: the newest release is {version}.",
                    date=i18n.date_label(date.fromisoformat(settings.update_checked)),
                    version=updates_svc.latest_version(ctx),
                )
            ui.label(text).classes("caption")
            if release:
                ui.link(t("Budget {version} is available", version=release.version), release.url, new_tab=True).classes(
                    "caption text-primary"
                )
            else:
                ui.link(t("Releases page"), RELEASES_URL, new_tab=True).classes("caption text-primary")
        check_btn = ui.button(t("Check Now")).props("flat dense no-caps").classes("text-primary")
        check_btn.on_click(lambda: check_now(check_btn))
        check_btn.set_visibility(settings.update_check)

    async def check_now(button: ui.button) -> None:
        button.props(add="loading")
        try:
            await run.io_bound(updates_svc.check, ctx, None, True)
        except updates_svc.UpdateError as exc:
            error(t("Update check failed: {error}", error=exc))
        status.refresh()

    async def toggle(e) -> None:
        if e.value:
            settings.update_check = True
            ctx.store.save_settings()
            status.refresh()
            try:
                await run.io_bound(updates_svc.check, ctx, None, True)  # the user just asked for it
            except updates_svc.UpdateError as exc:
                error(t("Update check failed: {error}", error=exc))
        else:
            updates_svc.forget(ctx)
        status.refresh()

    with ui.column().classes("w-full gap-0"):
        ui.label(t("Updates")).classes("group-header")
        with ui.element("div").classes("group"):
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Check for Updates"))
                    ui.label(
                        t(
                            "Once a day, Budget asks GitHub for the number of the newest release. Nothing is "
                            "downloaded or installed, and the request carries nothing about you or your data. Off by "
                            "default; besides the ECB rates it is the only connection the app makes."
                        )
                    ).classes("caption")
                ui.switch(value=settings.update_check, on_change=toggle).props("dense").mark("update-check")
            with ui.element("div").classes("group-row"):
                status()


def _backup_date(b: archive.SavedBackup) -> str:
    return f"{i18n.date_label(b.created.date())}, {b.created:%H:%M}"


PROJECTION_LABELS = {  # the choice in Settings, in its order (decision NEW-PROJ)
    PROJECTION_BUDGET: N_("Budget only"),
    PROJECTION_HIGHER: N_("Recorded where higher"),
}


def page() -> None:
    with frame(t("Settings"), "/settings", narrow=True) as (ctx, _):
        with ui.column().classes("w-full gap-0"):
            ui.label(t("Budget")).classes("group-header")
            with ui.element("div").classes("group"):
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t("Language"))
                        ui.label(t("The interface language. Amounts keep the Swiss format.")).classes("caption")

                    def set_language(e):
                        ctx.store.settings.language = e.value
                        ctx.store.save_settings()
                        i18n.set_language(e.value)
                        ui.navigate.reload()

                    languages = {
                        "": t("System ({name})", name=i18n.LANGUAGES[i18n.system_language()]),
                        **i18n.LANGUAGES,
                    }
                    ui.select(languages, value=ctx.store.settings.language, on_change=set_language).props(
                        "dense outlined options-dense"
                    ).classes("w-36")
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t("Plan Until"))
                        ui.label(t("Extending it continues every budget entry that runs forever.")).classes("caption")
                    cm = months.current_month()
                    opts = month_options(cm, months.add_months(cm, 12 * 15))
                    horizon = ctx.horizon()
                    opts.setdefault(horizon, months.label(horizon))
                    sel = ui.select(opts, value=horizon).props("dense outlined options-dense").classes("w-36")
                    apply_btn = ui.button(t("Apply")).props("unelevated dense").classes("px-3")
                    apply_btn.set_visibility(False)

                    def apply():
                        added = budget_svc.set_horizon(ctx, sel.value)
                        apply_btn.set_visibility(False)
                        month = months.label(sel.value)
                        success(
                            tn(
                                "Planning until {month}, {n} budget entry added",
                                "Planning until {month}, {n} budget entries added",
                                added,
                                month=month,
                            )
                            if added
                            else t("Planning until {month}", month=month)
                        )

                    apply_btn.on_click(apply)
                    sel.on_value_change(lambda e: apply_btn.set_visibility(e.value != ctx.horizon()))
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t("Projected Wealth"))
                        ui.label(
                            t(
                                "For months without recorded balances: the budgets alone, or per top-level "
                                "category the recorded amount where it exceeds the budget."
                            )
                        ).classes("caption")

                    def set_projection(e):
                        ctx.store.settings.projection = e.value
                        ctx.store.save_settings()
                        success(t("Projected wealth: {rule}", rule=t(PROJECTION_LABELS[e.value])))

                    ui.select(
                        {k: t(v) for k, v in PROJECTION_LABELS.items()},
                        value=ctx.store.settings.projection,
                        on_change=set_projection,
                    ).props("dense outlined options-dense").classes("w-56").mark("projection-rule")
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t("Display Currency"))
                        ui.label(t("Amounts are shown in this currency unless a page says otherwise.")).classes(
                            "caption"
                        )

                    def set_cur(e):
                        ctx.store.settings.display_currency = e.value
                        ctx.store.save_settings()
                        success(t("Showing amounts in {currency}", currency=e.value))

                    currency_select(ctx, ctx.store.settings.display_currency, on_change=set_cur, label=None)
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t("Base Currency"))
                        ui.label(t("Every amount is also stored in this currency. It can’t be changed.")).classes(
                            "caption"
                        )
                    ui.label(BASE_CURRENCY).classes("text-2 pr-2")
        _accounts_group(ctx, ACCOUNT_REGULAR, t("Accounts"))
        _accounts_group(ctx, ACCOUNT_ADVANCE, t("Advance Payment Accounts"))
        _income_group(ctx)
        _reminders_group(ctx)
        _data_group(ctx)
        _updates_group(ctx)
