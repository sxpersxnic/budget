"""The sheet for one categorization rule (F07): "Always Categorize Like This" on a transaction, or the Rules page."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, TX_TYPES, Rule
from budget_app.i18n import N_, t
from budget_app.services import rules as rules_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.rules import RuleInput
from budget_app.ui.layout import error, segmented, sheet, undoable
from budget_app.ui.widgets.selects import account_options, expenditure_category_select, income_category_select

ACCOUNT_HINTS = {
    TX_EXPENDITURE: N_("Optional. Suggested as the From account when a transaction has none yet."),
    TX_INCOME: N_("Optional. Suggested as the To account when a transaction has none yet."),
}


def open_rule_sheet(
    ctx: Context,
    on_saved: Callable[[], None] | None = None,
    editing: Rule | None = None,
    prefill: RuleInput | None = None,
    title: str = "",
) -> None:
    """Add a rule (starting from `prefill`) or change one. A rule with the same text and type is named in a
    warning, and saving replaces it."""
    init = (
        RuleInput(editing.match, editing.type, editing.category, editing.account) if editing else prefill or RuleInput()
    )
    title = title or (t("Edit Rule") if editing else t("New Rule"))
    touched = {"value": False}
    with sheet(title, width="30rem") as dlg, ui.column().classes("w-full gap-4"):
        typ = segmented({k: t(TX_TYPES[k]) for k in rules_svc.RULE_TYPES}, init.type, full=True)
        match = ui.input(t("Text contains"), value=init.match).props("dense outlined autofocus").classes("w-full")
        ui.label(t("Looked for in the payee and in the bank text or note, ignoring case and accents.")).classes(
            "caption -mt-3 px-1"
        )
        exp_cat = expenditure_category_select(init.category if init.type == TX_EXPENDITURE else None, ctx=ctx)
        inc_cat = income_category_select(ctx, init.category if init.type == TX_INCOME else None)
        account = (
            ui.select(account_options(ctx), label=t("Account"), value=init.account or None, with_input=True)
            .props("dense outlined clearable")
            .classes("w-full")
        )
        account_hint = ui.label().classes("caption -mt-3 px-1")
        with ui.element("div").classes("callout") as warning:
            ui.icon("o_info")
            warning_text = ui.label()
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Save Rule")).props("unelevated")

    def read() -> RuleInput:
        kind = typ.value
        return RuleInput(
            match=match.value or "",
            type=kind,
            category=(exp_cat.value if kind == TX_EXPENDITURE else inc_cat.value) or "",
            account=account.value or "",
        )

    def same() -> Rule | None:
        inp = read()
        return rules_svc.same_text(ctx, inp.match, inp.type, exclude_id=editing.id if editing else "")

    def changed() -> None:
        kind = typ.value
        exp_cat.set_visibility(kind == TX_EXPENDITURE)
        inc_cat.set_visibility(kind == TX_INCOME)
        account_hint.set_text(t(ACCOUNT_HINTS.get(kind, "")))
        other = same()
        warning.set_visibility(other is not None)
        if other is not None:
            warning_text.set_text(
                t(
                    "A rule for “{match}” already exists ({category}). Saving replaces it.",
                    match=other.match,
                    category=rules_svc.category_label(ctx, other.type, other.category) or "—",
                )
            )
        save_btn.set_text(t("Replace Rule") if other is not None else t("Save Rule"))
        try:
            rules_svc.validate(ctx, read())
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if touched["value"] or editing else "")
            return
        save_btn.enable()
        status.set_text("")

    def save() -> None:
        try:
            rule = rules_svc.save(ctx, read(), editing.id if editing else "", replace=same() is not None)
        except ValidationError as exc:
            error(str(exc))
            return
        dlg.close()
        undoable(t("Saved the rule for “{match}”", match=rule.match))
        if on_saved:
            on_saved()

    save_btn.on_click(save)
    match.on("keydown.enter", lambda: save() if save_btn.enabled else None)
    for el in (typ, match, exp_cat, inc_cat, account):
        el.on_value_change(changed)
    changed()
    touched["value"] = True


def always_like_this(ctx: Context, type_: str, category: str, payee: str, note: str) -> None:
    """The sheet behind "Always Categorize Like This…" on a transaction: a rule for its payee (or the name in its
    note) and its category."""
    prefill = rules_svc.prefill(type_, category, payee, note)
    open_rule_sheet(ctx, prefill=prefill, title=t("Always Categorize Like This"))
