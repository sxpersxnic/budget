"""Settings: budget horizon, display currency, accounts, advance accounts, income categories, data."""

from __future__ import annotations

from datetime import date

from nicegui import events, ui

from budget_app import i18n
from budget_app.config import BASE_CURRENCY
from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR
from budget_app.i18n import t, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import archive
from budget_app.services import budget as budget_svc
from budget_app.services import export as export_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, frame, month_options, save_file, sheet, sheet_buttons, success, undoable
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

        def save():
            try:
                if acc:
                    acc_svc.edit_account(ctx, acc.id, name.value, cur.value)
                else:
                    acc_svc.add_account(ctx, name.value, cur.value, kind)
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
                        if kind == ACCOUNT_REGULAR:
                            last = wealth_svc.balance_entry(ctx, acc.id, "9999-12")
                            ui.label(
                                t(
                                    "{amount} at the end of {month}",
                                    amount=fmt(last.amount, acc.currency),
                                    month=months.label(last.month),
                                )
                                if last
                                else t("No balance recorded")
                            ).classes("caption")
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


def _backup_date(b: archive.SavedBackup) -> str:
    return f"{i18n.date_label(b.created.date())}, {b.created:%H:%M}"


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
        _data_group(ctx)
