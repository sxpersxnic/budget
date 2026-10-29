"""Tax Summary (F20): a year's amounts per typical deduction area, each with its rows and their proofs.

The page lists; it never judges what is deductible (cantonal rules differ, and the note says so). A year picker
(last year by default, the year a declaration is made for), one collapsible section per area with its total in
CHF, the sheet that links tags to sections, and the exports: PDF, CSV and a ZIP with the attachment files.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import app, run, ui

from budget_app import i18n
from budget_app.domain.tax import RULE_PILLAR3A, RULE_SECURITIES, RULE_WITHHOLDING, SECTIONS, Section
from budget_app.i18n import t, tn
from budget_app.services import attachments as attachments_svc
from budget_app.services import tags as tag_svc
from budget_app.services import tax as tax_svc
from budget_app.services import tax_export
from budget_app.services.context import Context, ValidationError
from budget_app.services.tax import CURRENCY, Entry, SectionSummary
from budget_app.ui.layout import error, fmt, frame, named, on_data_changed, save_file, sheet, sheet_buttons, undoable
from budget_app.ui.widgets import attachments as att_ui
from budget_app.ui.widgets.tags import TagSelect, chip
from budget_app.ui.widgets.transaction_form import open_transaction_sheet

ROW_ACTION = "flat dense round size=sm"


def basis(section: Section) -> str:
    """What a section is found by, besides its tags."""
    if section.rule == RULE_PILLAR3A:
        return t("Transfers to pillar 3a accounts")
    if section.rule == RULE_WITHHOLDING:
        return t("Dividends with withholding tax in CHF accounts (Investments)")
    if section.rule == RULE_SECURITIES:
        return t("Dividends (Investments) and income in the categories Dividends and Interest")
    return t("Categories: {codes}", codes=tax_svc.code_labels(section))


def section_caption(s: SectionSummary, year: int) -> str:
    if not s.entries:
        return t("Nothing in {year}", year=str(year))
    parts = [tn("{n} row", "{n} rows", len(s.entries))]
    if s.attachments:
        parts.append(tn("{n} file", "{n} files", s.attachments))
    return " · ".join(parts)


# ------------------------------------------------------------------ the sheet that links tags to sections
def open_links_sheet(ctx: Context, on_saved: Callable[[], None]) -> None:
    current = tax_svc.links(ctx)
    with sheet(t("Tax Sections"), width="34rem") as dlg:
        ui.label(
            t(
                "Link tags to a section: transactions with one of them count there too, on top of the section’s "
                "categories. A tag belongs to one section at most."
            )
        ).classes("caption px-1")
        fields: dict[str, TagSelect] = {}
        with ui.column().classes("w-full gap-4 tax-links"):
            for section in SECTIONS:
                with ui.column().classes("w-full gap-1"):
                    ui.label(t(section.title)).classes("text-[14px] font-medium")
                    ui.label(basis(section)).classes("caption")
                    fields[section.key] = TagSelect(ctx, current[section.key], label=t("Tags")).classes("w-full")

        def save() -> None:
            try:
                tax_svc.set_links(ctx, {key: list(f.value or []) for key, f in fields.items()})
            except ValidationError as exc:
                error(str(exc))
                return
            dlg.close()
            undoable(t("Saved the tax sections"))
            on_saved()

        sheet_buttons(dlg, t("Save"), save)


# ------------------------------------------------------------------ rows
def _files(ctx: Context, entry: Entry) -> None:
    """The proofs of a row: opened by the system viewer in the app window, in a new tab in a browser."""
    for att in entry.attachments:
        icon = att_ui.ICONS.get(attachments_svc.kind(att), "o_draft")
        if app.native.main_window is not None:
            ui.button(att.name, icon=icon, on_click=lambda att=att: _open(ctx, att)).props(
                "flat dense no-caps size=12px"
            ).classes("link-btn -ml-1")
            continue
        with ui.link(target=att_ui.url(att), new_tab=True).classes("caption link-btn flex items-center gap-1"):
            ui.icon(icon, size="16px")
            ui.label(att.name).classes("ellipsis")


def _open(ctx: Context, att) -> None:
    try:
        attachments_svc.open_file(ctx, att)
    except ValidationError as exc:
        error(str(exc))


def _row(ctx: Context, entry: Entry, refresh: Callable[[], None]) -> None:
    tags = tag_svc.by_id(ctx)
    with ui.element("div").classes("group-row tax-row py-2"):
        with ui.column().classes("gap-0.5 flex-1 min-w-0"):
            ui.label(entry.label or "–").classes("w-full truncate")
            ui.label(f"{i18n.date_label(entry.date)} · {entry.category}").classes("caption w-full break-words")
            if entry.tags or entry.attachments:
                with ui.row().classes("items-center gap-x-3 gap-y-1"):
                    for tag_id in entry.tags:
                        if tag_id in tags:
                            chip(tags[tag_id]).tooltip(t("Counts here through this tag"))
                    _files(ctx, entry)
        with ui.column().classes("items-end gap-0 shrink-0"):
            ui.label(fmt(entry.amount, CURRENCY)).classes("num font-medium whitespace-nowrap")
            if entry.tax:
                ui.label(t("tax withheld {amount}", amount=fmt(entry.tax))).classes("caption whitespace-nowrap")
        tx = ctx.store.transactions.get(entry.transaction_id) if entry.transaction_id else None
        if tx is not None:
            named(
                ui.button(icon="o_open_in_new", on_click=lambda tx=tx: open_transaction_sheet(ctx, refresh, editing=tx))
                .props(ROW_ACTION)
                .classes("text-primary row-actions"),
                t("Show the transaction"),
            )


# ------------------------------------------------------------------ the page
def page() -> None:
    with frame(t("Tax Summary"), "/tax") as (ctx, actions):
        years = tax_svc.years(ctx)
        state: dict = {"year": tax_svc.default_year(), "open": set()}

        async def export(kind: str) -> None:
            year = state["year"]
            summary = tax_svc.summary(ctx, year)
            try:
                if kind == "pdf":
                    data = tax_export.summary_pdf(summary)
                elif kind == "csv":
                    data = tax_export.summary_csv(summary)
                else:
                    proofs = await run.io_bound(tax_export.proofs_zip, ctx, summary)
                    if proofs is None:  # pragma: no cover - the app is shutting down
                        return
                    data = proofs.data
                    if proofs.skipped:
                        error(f"{tax_export.skipped_message(proofs)}: {proofs.skipped[0]}")
            except ValidationError as exc:
                error(str(exc))
                return
            await save_file(data, tax_export.file_name(year, kind))

        with actions:
            ui.select(
                {y: str(y) for y in years},
                value=state["year"],
                label=t("Year"),
                on_change=lambda e: (state.update(year=e.value), content.refresh()),
            ).props("dense outlined options-dense").classes("w-28")
            ui.button(t("Sections…"), icon="o_tune", on_click=lambda: open_links_sheet(ctx, refresh)).props(
                "flat no-caps"
            ).classes("text-primary")
            with (
                named(ui.button(icon="ios_share").props("flat round").classes("text-primary"), t("Export")),
                ui.menu().props("auto-close"),
            ):
                ui.menu_item(t("Export as PDF…"), on_click=lambda: export("pdf"))
                ui.menu_item(t("Export as CSV…"), on_click=lambda: export("csv"))
                ui.menu_item(t("Proofs as ZIP…"), on_click=lambda: export("zip"))
        with ui.element("div").classes("callout tax-note"):  # the notes that must be read stay above the amounts
            ui.icon("o_info")
            with ui.column().classes("gap-0.5"):
                ui.label(tax_export.note())
                ui.label(tax_export.overlap_note()).classes("caption")
                ui.label(t("Nothing is filed or sent; the exports are for you or your tax adviser.")).classes("caption")

        def toggled(key: str, value: bool) -> None:
            if value:
                state["open"].add(key)
            else:
                state["open"].discard(key)

        @ui.refreshable
        def content() -> None:
            year = state["year"]
            summary = tax_svc.summary(ctx, year)
            tags = tag_svc.by_id(ctx)
            with ui.column().classes("w-full gap-0"):
                header = t("{year} · amounts in CHF", year=str(year))
                if summary.overlapping:
                    n = summary.overlapping
                    header += " · " + tn(
                        "{n} transaction is in more than one section",
                        "{n} transactions are in more than one section",
                        n,
                    )
                ui.label(header).classes("group-header")
                with ui.element("div").classes("group"):
                    for s in summary.sections:
                        _section(s, year, tags)

        def _section(s: SectionSummary, year: int, tags) -> None:
            key = s.section.key
            with (
                ui.expansion(value=key in state["open"], on_value_change=lambda e, k=key: toggled(k, e.value))
                .classes("w-full tax-section")
                .props("dense") as exp
            ):
                with exp.add_slot("header"), ui.row().classes("w-full items-center gap-3 no-wrap py-1"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(t(s.section.title)).classes("font-medium")
                        ui.label(t(s.section.note)).classes("caption")
                    with ui.column().classes("items-end gap-0 shrink-0"):
                        ui.label(fmt(s.total if s.entries else None, CURRENCY)).classes(
                            "num font-semibold whitespace-nowrap"
                        )
                        ui.label(section_caption(s, year)).classes("caption whitespace-nowrap")
                if s.tags:
                    with ui.element("div").classes("group-row tax-tags"):
                        ui.label(t("Also by tag")).classes("caption")
                        for tag_id in s.tags:
                            chip(tags[tag_id])
                if not s.entries:
                    with ui.element("div").classes("group-row"):
                        ui.label(t("Nothing in {year}", year=str(year))).classes("text-2")
                for entry in s.entries:
                    _row(ctx, entry, refresh)
                if s.tax_total:
                    with ui.element("div").classes("group-row"):
                        ui.label(t("Tax withheld")).classes("flex-1 text-2")
                        ui.label(fmt(s.tax_total, CURRENCY)).classes("num")

        def refresh() -> None:
            content.refresh()

        content()
        on_data_changed(refresh)
