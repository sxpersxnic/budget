"""Categorization rules (F07): every rule with its text, type, category and account; add, edit and delete.

A tab of Transactions, beside Payees (decision 49): one *New Rule* (its sheet chooses the type), the rules grouped by
type. A rule suggests its category for new and imported transactions whose payee or bank text contains its text; the
longest text wins, between equally long ones the newest."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import TX_EXPENDITURE, TX_TYPES, Rule
from budget_app.i18n import t
from budget_app.services import accounts as acc_svc
from budget_app.services import rules as rules_svc
from budget_app.services.context import Context
from budget_app.ui.layout import frame, info, named, on_data_changed, undoable
from budget_app.ui.widgets.empty import empty_state
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.rule_sheet import open_rule_sheet

ROW_ACTION = "flat dense round size=sm"


def caption(ctx: Context, rule: Rule) -> str:
    """What the rule suggests: "Groceries · 01.1 · Bank (CHF)"."""
    if rules_svc.valid_category(ctx, rule.type, rule.category):
        parts = [rules_svc.category_label(ctx, rule.type, rule.category)]
    else:  # an income category deleted since: the rule suggests nothing
        parts = [t("Category no longer exists")]
    if rule.account:
        acc = ctx.store.accounts.get(rule.account)
        parts.append(acc_svc.account_label(acc) if acc else t("Account no longer exists"))
    return " · ".join(parts)


def page() -> None:
    with frame(t("Rules"), "/rules", narrow=True) as (ctx, actions):
        with actions:  # flat: the sidebar's New Transaction is the filled one; while there are none, the empty
            # state offers it
            new = ui.button(t("New Rule"), icon="add", on_click=lambda: add()).props("flat").classes("text-primary")  # noqa: PLW0108 - defined below

        @ui.refreshable
        def rule_list() -> None:
            rules = rules_svc.all_rules(ctx)
            new.set_visibility(bool(rules))
            if not rules:
                with ui.element("div").classes("group"):
                    empty_state(
                        t("No rules yet."),
                        t("A rule gives new and imported transactions their category by payee or bank text."),
                        t("New Rule"),
                        add,
                    )
                return
            # one plain line; how rules compete behind ⓘ (polish before 1.0)
            with ui.row().classes("w-full items-center gap-0 px-1 -mb-2"):
                ui.label(
                    t("New and imported transactions get the category of the rule whose text they contain.")
                ).classes("caption")
                info(
                    t(
                        "A rule looks for its text in the payee and in the bank text or note. When several match, "
                        "the longest text wins. “Always Categorize Like This…” on a transaction makes one too."
                    )
                )
            for kind in rules_svc.RULE_TYPES:
                shown = [r for r in rules if r.type == kind]
                if not shown:
                    continue
                with ui.column().classes("w-full gap-0"):
                    ui.label(t(TX_TYPES[kind])).classes("group-header")
                    with ui.element("div").classes("group"):
                        for rule in shown:
                            _row(rule)

        def _row(rule: Rule) -> None:
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Contains “{match}”", match=rule.match)).classes("truncate")
                    text = ui.label(caption(ctx, rule)).classes("caption")
                    if tip := get_coicop().tooltip(rule.category) if rule.type == TX_EXPENDITURE else "":
                        text.tooltip(tip)  # "COICOP 01.1 Food"
                with ui.row().classes("row-actions gap-0 no-wrap"):
                    named(
                        ui.button(icon="o_edit", on_click=lambda r=rule: open_rule_sheet(ctx, refresh, editing=r))
                        .props(ROW_ACTION)
                        .classes("text-primary"),
                        t("Edit"),
                    )
                    named(
                        ui.button(icon="o_delete", on_click=lambda r=rule: delete(r))
                        .props(ROW_ACTION)
                        .classes("text-negative"),
                        t("Delete"),
                    )

        def refresh() -> None:
            rule_list.refresh()

        def add() -> None:
            open_rule_sheet(ctx, refresh)

        def delete(rule: Rule) -> None:
            def do() -> None:
                rules_svc.delete(ctx, [rule.id])
                undoable(t("Deleted the rule for “{match}”", match=rule.match))
                refresh()

            confirm(
                t("Delete the rule for “{match}”?", match=rule.match),
                do,
                detail=t("Transactions it categorized keep their category."),
            )

        rule_list()
        on_data_changed(refresh)
