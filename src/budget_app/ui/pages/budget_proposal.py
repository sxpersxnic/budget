"""*Propose from History…* on the Budget Plan page (F16): a budget per category from the last complete months.

The sheet reads the proposal from `services.budget_history` whenever an input changes and lists one row per
category: a checkbox, the months' values, the budget already planned and the proposed monthly amount, which can be
edited. On a narrow window each row stacks its amount below the category.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.config import BASE_CURRENCY
from budget_app.domain import months, proposal
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME
from budget_app.i18n import N_, t, tc, tn
from budget_app.services import budget_history as hist_svc
from budget_app.services import recurring as rec_svc
from budget_app.services.budget_history import Budgeted, Choice, Options, Proposal, Row
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, fmt, month_options, segmented, sheet, undoable
from budget_app.ui.pages.add_budget import FOREVER, recurrence_label, until_options

LEVEL_NAMES = {1: N_("Division"), 2: N_("Group"), 3: N_("Class")}
# sticky at the sheet's bottom edge, covering its 18 px padding, so no row shows below the buttons
SHEET_FOOTER = (
    "bottom: -18px; margin-bottom: -18px; padding-bottom: 18px; background: inherit;"
    " border-top: 0.5px solid var(--separator)"
)
GROUPS = {BUDGET_EXPENDITURE: "Expenditure", BUDGET_INCOME: "Income"}  # shown with tc("heading", …)


def whole(value: float) -> str:
    """A month's value in whole francs with Swiss thousands ("1'900", "-12")."""
    return f"{round(value):,}".replace(",", "'").replace("-", "−")


def amount_range(b: Budgeted) -> str:
    if round(b.low, 2) == round(b.high, 2):
        return fmt(b.low, BASE_CURRENCY)
    return f"{fmt(b.low)} – {fmt(b.high, BASE_CURRENCY)}"


def span_label(month_list: list[str]) -> str:
    return months.label(month_list[0]) + (f" – {months.label(month_list[-1])}" if len(month_list) > 1 else "")


def rounding_options() -> dict[float, str]:
    return {step: t("To the cent") if step < 1 else f"{whole(step)} {BASE_CURRENCY}" for step in proposal.STEPS}


def basis_caption(prop: Proposal) -> str:
    if not prop.basis:
        return t("There is no complete month with records yet, so there is nothing to propose.")
    n = len(prop.basis)
    if prop.partial:
        return tn(
            "Your records start in {month}, so {n} month counts: {span}. Amounts in CHF.",
            "Your records start in {month}, so {n} months count: {span}. Amounts in CHF.",
            n,
            month=months.label(prop.basis[0]),
            span=span_label(prop.basis),
        )
    return tn(
        "Based on {n} complete month: {span}. Amounts in CHF; a month without spending counts as 0.",
        "Based on {n} complete months: {span}. Amounts in CHF; a month without spending counts as 0.",
        n,
        span=span_label(prop.basis),
    )


def open_proposal_sheet(ctx: Context, on_saved: Callable[[], None]) -> None:
    cm = months.current_month()
    horizon = ctx.horizon()
    widgets: dict[str, tuple[Row, ui.checkbox, ui.number]] = {}
    edited: dict[str, float | None] = {}  # amounts typed by the user, kept while only the period changes
    state: dict[str, Proposal] = {}

    with sheet(t("Propose from History"), width="64rem") as dlg:
        ui.label(
            t("A monthly budget per category from what you spent and received, as a starting point to edit.")
        ).classes("caption -mt-2")
        with ui.row().classes("w-full gap-2 items-center flex-wrap"):
            basis = segmented({n: tn("{n} month", "{n} months", n) for n in proposal.BASES}, 6)
            method = segmented({proposal.AVERAGE: t("Average"), proposal.MEDIAN: t("Median")}, proposal.AVERAGE)
        with ui.row().classes("w-full gap-2 flex-wrap"):
            level = (
                ui.select({k: t(v) for k, v in LEVEL_NAMES.items()}, value=2, label=t("Level"))
                .props("dense outlined options-dense")
                .classes("w-36 max-sm:flex-1")
            )
            rounding = (
                ui.select(rounding_options(), value=10.0, label=t("Round to"))
                .props("dense outlined options-dense")
                .classes("w-36 max-sm:flex-1")
            )
            start = (
                ui.select(month_options(cm, max(horizon, cm)), value=cm, label=t("From"))
                .props("dense outlined options-dense")
                .classes("w-40 max-sm:flex-1")
            )
            end = (
                ui.select(until_options(cm, horizon), value=FOREVER, label=t("Until"))
                .props("dense outlined options-dense")
                .classes("w-40 max-sm:flex-1")
            )
        caption = ui.label().classes("caption px-1")
        table_box = ui.column().classes("w-full gap-3")
        # the buttons stay in view while the rows scroll
        with ui.row().classes("w-full items-center gap-2 pt-3 sticky z-10").style(SHEET_FOOTER):
            status = ui.label().classes("caption flex-1 min-w-[12rem]")
            with ui.row().classes("gap-2 no-wrap ml-auto"):
                ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
                create_btn = ui.button(on_click=lambda: create()).props("unelevated")  # noqa: PLW0108 - defined below

    def options() -> Options:
        return Options(
            basis=int(basis.value),
            method=method.value,
            level=int(level.value),
            step=float(rounding.value),
            start_month=start.value or "",
            end_month="" if end.value == FOREVER else end.value or "",
            forever=end.value == FOREVER,
        )

    def ticked() -> list[tuple[Row, ui.number]]:
        return [(row, amount) for row, box, amount in widgets.values() if box.value]

    def update_status() -> None:
        prop = state["proposal"]
        chosen = ticked()
        n = len(chosen)
        create_btn.set_text(tn("Create Budget for {n} Category", "Create Budget for {n} Categories", n))
        valid = all((amount.value or 0) > 0 for _, amount in chosen)
        create_btn.set_enabled(bool(chosen) and valid and not prop.target_error)
        if prop.target_error:
            status.set_text(prop.target_error)
            return
        if not valid:
            status.set_text(t("Every ticked category needs an amount above 0."))
            return
        spending = sum(a.value or 0 for r, a in chosen if r.type == BUDGET_EXPENDITURE)
        income = sum(a.value or 0 for r, a in chosen if r.type == BUDGET_INCOME)
        replacing = sum(r.conflict for r, _ in chosen)
        values = {"spending": fmt(spending, BASE_CURRENCY), "income": fmt(income, BASE_CURRENCY)}
        if end.value == FOREVER:
            text = t(
                "{spending} spending and {income} income a month from {month}, continued as the plan grows",
                month=months.label(prop.target[0]),
                **values,
            )
        else:
            text = t("{spending} spending and {income} income a month, {span}", span=span_label(prop.target), **values)
        if replacing:
            text += " · " + tn("replaces {n} budget", "replaces {n} budgets", replacing)
        status.set_text(text)

    def history_text(row: Row) -> str:
        return " · ".join(whole(v) for v in row.history)

    def render_row(row: Row) -> None:
        label = hist_svc.category_label(ctx, row.type, row.category)
        with ui.element("div").classes("group-row flex-wrap py-2 gap-y-1").props(f'data-key="{row.key}"'):
            with ui.column().classes("gap-0 flex-1 min-w-[16rem] max-sm:min-w-full"):
                box = ui.checkbox(label, value=row.ticked).props("dense")
                with ui.column().classes("gap-0 pl-7"):
                    monthly = zip(state["proposal"].basis, row.history, strict=True)
                    ui.label(history_text(row)).classes("caption tabular-nums").tooltip(
                        " · ".join(f"{months.label(m)}: {fmt(v)}" for m, v in monthly)
                    )
                    if row.existing is not None:
                        ui.label(
                            t("Has a budget in {n} of these months. Tick to replace it.", n=row.existing.months)
                        ).classes("caption due-soon")
                    for other in row.overlaps:
                        ui.label(
                            t(
                                "Overlaps the budget for {category} ({amount}); both would count.",
                                category=hist_svc.category_label(ctx, row.type, other.category),
                                amount=amount_range(other),
                            )
                        ).classes("caption due-soon")
                    for series in row.recurring:
                        ui.label(
                            t(
                                "Booked by a recurring transaction: {title}, {amount} ({repeat})",
                                title=rec_svc.title(ctx, series),
                                amount=fmt(series.amount, series.currency),
                                repeat=recurrence_label(series.recur_months),
                            )
                        ).classes("caption")
            with ui.row().classes("items-center gap-3 no-wrap max-sm:w-full max-sm:pl-7"):
                now = amount_range(row.existing) if row.existing is not None else "–"
                planned = "" if row.existing is not None else " max-sm:invisible"  # a phone shows only a budget
                with ui.column().classes("gap-0 w-40 items-end max-sm:items-start max-sm:flex-1" + planned):
                    ui.label(t("Budget now")).classes("caption sm:hidden")
                    ui.label(now).classes("tabular-nums" + (" due-soon" if row.existing is not None else " text-3"))
                value = edited.get(row.key, row.proposed)
                amount = (
                    ui.number(value=value, min=0, format="%.2f")
                    .props(f'dense outlined inputmode=decimal suffix="{BASE_CURRENCY}"')
                    .props(f'aria-label="{t("Per month")}"')
                    .classes("w-36")
                )
        widgets[row.key] = (row, box, amount)
        box.on_value_change(update_status)

        def typed(e, key=row.key) -> None:
            edited[key] = e.value
            update_status()

        amount.on_value_change(typed)

    def render() -> None:
        prop = state["proposal"]
        widgets.clear()
        caption.set_text(basis_caption(prop))
        table_box.clear()
        with table_box:
            if prop.basis and not prop.rows:
                ui.label(t("Nothing was spent or received in these months.")).classes("text-2 px-1")
            for type_, heading in GROUPS.items():
                rows = [r for r in prop.rows if r.type == type_]
                if not rows:
                    continue
                with ui.column().classes("w-full gap-0"):
                    with ui.row().classes("w-full items-end no-wrap gap-3 pr-4"):
                        ui.label(tc("heading", heading)).classes("group-header flex-1")
                        ui.label(t("Budget now")).classes("group-header w-40 text-right max-sm:hidden")
                        ui.label(t("Per month")).classes("group-header w-36 max-sm:hidden")
                    with ui.element("div").classes("group"):
                        for row in rows:
                            render_row(row)
        update_status()

    def recalculate(keep_amounts: bool) -> None:
        if not keep_amounts:
            edited.clear()
        state["proposal"] = hist_svc.propose(ctx, options())
        render()

    def period_changed() -> None:
        if start.value:
            opts = until_options(start.value, horizon)
            if end.value not in opts:
                end.value = None
            end.set_options(opts, value=end.value)
        recalculate(keep_amounts=True)

    def create() -> None:
        prop = state["proposal"]
        choices = [Choice(r.type, r.category, a.value, replace=r.conflict) for r, a in ticked()]
        opts = options()
        try:
            entries = hist_svc.create(ctx, opts, choices, hist_svc.note(opts.method, prop.basis))
        except ValidationError as exc:
            error(str(exc))
            return
        dlg.close()
        undoable(tn("Created {n} budget entry", "Created {n} budget entries", len(entries)))
        on_saved()

    for el in (basis, method, level, rounding):
        el.on_value_change(lambda: recalculate(keep_amounts=False))
    for el in (start, end):
        el.on_value_change(period_changed)
    recalculate(keep_amounts=False)
