"""Categorization rules (F07): every rule with its text, type, category and account; add, edit and delete.

Reached from the Payees page and Settings. A rule suggests its category for new and imported transactions whose
payee or bank text contains its text; the longest text wins, between equally long ones the newest."""

from __future__ import annotations

from nicegui import ui

from budget_app.domain.models import TX_TYPES, Rule
from budget_app.i18n import t, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import rules as rules_svc
from budget_app.services.context import Context
from budget_app.ui.layout import frame, named, on_data_changed, undoable
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
    with frame(t("Categorization Rules"), "/rules", narrow=True) as (ctx, actions):
        with actions:
            ui.button(t("New Rule"), icon="add", on_click=lambda: open_rule_sheet(ctx, refresh)).props("unelevated")
        ui.label(
            t(
                "A rule suggests its category for new and imported transactions whose payee or bank text contains "
                "its text. The longest text wins. Create one with “Always Categorize Like This…” on a transaction."
            )
        ).classes("caption -mt-2")

        @ui.refreshable
        def rule_list() -> None:
            rules = rules_svc.all_rules(ctx)
            for kind in rules_svc.RULE_TYPES:
                shown = [r for r in rules if r.type == kind]
                with ui.column().classes("w-full gap-0"):
                    ui.label(t(TX_TYPES[kind])).classes("group-header")
                    with ui.element("div").classes("group"):
                        if not shown:
                            with ui.element("div").classes("group-row"):
                                ui.label(t("No rules yet.")).classes("text-2")
                        for rule in shown:
                            _row(rule)
                        with ui.element("div").classes("group-row"):
                            ui.button(
                                t("Add Rule…"),
                                on_click=lambda kind=kind: open_rule_sheet(
                                    ctx, refresh, prefill=rules_svc.RuleInput(type=kind)
                                ),
                            ).props("flat dense no-caps").classes("link-btn -ml-2")
            ui.label(tn("{n} rule", "{n} rules", len(rules))).classes("caption px-1")

        def _row(rule: Rule) -> None:
            with ui.element("div").classes("group-row"):
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    ui.label(t("Contains “{match}”", match=rule.match)).classes("truncate")
                    ui.label(caption(ctx, rule)).classes("caption")
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
