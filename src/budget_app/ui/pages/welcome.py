"""Welcome (F30): the first-start screen. Language, display currency, where the data is kept, and two ways in.

The Overview opens it while nothing is recorded yet and it has not been passed (`demo.first_run`). It has no
sidebar: twelve places mean nothing before the first record. It stays reachable at `/welcome`.
"""

from __future__ import annotations

from nicegui import run, ui

from budget_app import i18n
from budget_app.i18n import t
from budget_app.services import demo as demo_svc
from budget_app.services.context import Context, ValidationError, get_ctx
from budget_app.ui import theme
from budget_app.ui.layout import error
from budget_app.ui.widgets.selects import currency_select

SAMPLE_TARGET = "/budget"  # where the sample set is seen best: a month's budget against its spending


async def load_sample(ctx: Context, button: ui.button) -> None:
    """Write the sample set (a few seconds) and open the Budget page on it."""
    button.props(add="loading")
    try:
        await run.io_bound(demo_svc.load, ctx)
    except ValidationError as exc:
        button.props(remove="loading")
        error(str(exc))
        return
    ui.navigate.to(SAMPLE_TARGET)


def _row(title: str, caption: str) -> None:
    with ui.column().classes("gap-0 flex-1 min-w-0"):
        ui.label(title)
        ui.label(caption).classes("caption")


def page() -> None:
    ctx = get_ctx()
    theme.apply_colors()
    ui.page_title(f"{t('Welcome')} – Budget")
    empty = demo_svc.is_empty(ctx)
    with ui.column().classes("page narrow"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("savings", size="26px").classes("text-primary")
            ui.label("Budget").classes("text-[15px] font-semibold")
        with ui.column().classes("gap-1"):
            ui.label(t("Welcome")).classes("large-title")
            ui.label(
                t(
                    "Your budget stays on this computer: plain CSV files in your data folder, CHF as the base "
                    "currency, ECB exchange rates and COICOP 2018 categories. Nothing is uploaded."
                )
            ).classes("footnote")

        with ui.column().classes("w-full gap-0"):
            ui.label(t("Before You Start")).classes("group-header")
            with ui.element("div").classes("group"):
                with ui.element("div").classes("group-row"):
                    _row(t("Language"), t("The interface language. Amounts keep the Swiss format."))

                    def set_language(e) -> None:
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
                    _row(t("Display Currency"), t("Amounts are shown in this currency unless a page says otherwise."))

                    def set_currency(e) -> None:
                        ctx.store.settings.display_currency = e.value
                        ctx.store.save_settings()

                    currency_select(ctx, ctx.store.settings.display_currency, on_change=set_currency, label=None)
                with ui.element("div").classes("group-row"), ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Data Folder"))
                    ui.label(str(ctx.store.data_dir)).classes("caption break-all select-all")
                    ui.label(
                        t(
                            "Your records are kept here as CSV files. To use another folder, start Budget with "
                            "--data-dir or set BUDGET_DATA_DIR."
                        )
                    ).classes("caption")

        with ui.column().classes("w-full gap-0"):
            ui.label(t("Two Ways In")).classes("group-header")
            with ui.element("div").classes("group"):
                with ui.element("div").classes("group-row"):
                    _row(
                        t("Your Own Data"),
                        t(
                            "Add your accounts and income categories in Settings, then record each account’s "
                            "balance on the Budget page."
                        ),
                    )

                    def own_data() -> None:
                        demo_svc.finish_onboarding(ctx)
                        ui.navigate.to("/settings")

                    ui.button(t("Open Settings"), on_click=own_data).props("unelevated no-wrap").classes(
                        "bg-primary text-white"
                    )
                with ui.element("div").classes("group-row"):
                    _row(
                        t("Sample Data"),
                        t(
                            "A year of example records: accounts, a holiday, a renovation, investments, budgets "
                            "and goals. Remove it again under Settings → Data."
                        )
                        if empty
                        else t("Only an empty data folder can take the sample data."),
                    )
                    load = ui.button(t("Load Sample Data")).props("outline no-wrap")
                    load.on_click(lambda: load_sample(ctx, load))
                    load.set_enabled(empty)
