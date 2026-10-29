"""Categorization rules (F07): "Always categorize like this", stored in `rules.csv`.

A rule is created only when the user asks for it (from a transaction or on the Rules page), never from a
confirmed suggestion (decision 8). Which rule applies to a transaction is decided in `domain/rules.py`;
`categorize.py` turns it into a suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass

from budget_app.domain import everyday
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.domain.models import ACCOUNT_REGULAR, TX_EXPENDITURE, TX_INCOME, Rule, Transaction
from budget_app.domain.payees import normalize
from budget_app.domain.rules import match_key
from budget_app.i18n import N_, t
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

RULE_TYPES = (TX_EXPENDITURE, TX_INCOME)
MIN_MATCH = 2  # a single letter would match almost every transaction


@dataclass
class RuleInput:
    match: str = ""
    type: str = TX_EXPENDITURE
    category: str = ""
    account: str = ""


def all_rules(ctx: Context) -> list[Rule]:
    """Every rule by type (expenditure first), then by text."""
    return sorted(
        ctx.store.rules.all(),
        key=lambda r: (RULE_TYPES.index(r.type) if r.type in RULE_TYPES else 9, match_key(r.match), r.id),
    )


def valid_category(ctx: Context, type_: str, category: str) -> bool:
    if type_ == TX_EXPENDITURE:
        return category == TAX or (category != ADV and get_coicop().get(category) is not None)
    return type_ == TX_INCOME and ctx.store.income_categories.get(category) is not None


def valid_account(ctx: Context, account_id: str) -> bool:
    acc = ctx.store.accounts.get(account_id) if account_id else None
    return acc is not None and acc.kind == ACCOUNT_REGULAR


def category_label(ctx: Context, type_: str, category: str) -> str:
    """The category's label ("01.1 Groceries") for expenditure, the category's name for income."""
    if type_ == TX_EXPENDITURE:
        return everyday.label(category)
    cat = ctx.store.income_categories.get(category)
    return cat.name if cat else ""


def same_text(ctx: Context, match: str, type_: str, exclude_id: str = "") -> Rule | None:
    """The rule with the same text (compared folded) for the same type, other than `exclude_id`."""
    k = match_key(match)
    return next(
        (r for r in ctx.store.rules.all() if r.id != exclude_id and r.type == type_ and match_key(r.match) == k), None
    )


def validate(ctx: Context, inp: RuleInput) -> RuleInput:
    """The input cleaned up; ValidationError naming the first problem."""
    match = " ".join((inp.match or "").split())
    if not match:
        raise ValidationError(t("Enter the text to look for"))
    if len(match_key(match)) < MIN_MATCH:
        raise ValidationError(t("The text needs at least two characters"))
    if inp.type not in RULE_TYPES:
        raise ValidationError(t("A rule is for expenditure or income"))
    if not valid_category(ctx, inp.type, inp.category):
        raise ValidationError(t("Choose a category"))
    if inp.account and not valid_account(ctx, inp.account):
        raise ValidationError(t("Account not found"))
    return RuleInput(match=match, type=inp.type, category=inp.category, account=inp.account or "")


@recorded(N_("Save a categorization rule"))
def save(ctx: Context, inp: RuleInput, rule_id: str = "", replace: bool = False) -> Rule:
    """Add a rule, or change the one with `rule_id`.

    Another rule with the same text and type is replaced when `replace` is set (it takes the new values, and
    the edited rule, if any, goes); otherwise that is an error.
    """
    clean = validate(ctx, inp)
    editing = ctx.store.rules.get(rule_id) if rule_id else None
    if rule_id and editing is None:
        raise ValidationError(t("Rule not found"))
    same = same_text(ctx, clean.match, clean.type, exclude_id=rule_id)
    if same is not None and not replace:
        raise ValidationError(t("A rule for “{match}” already exists", match=same.match))
    target = same or editing
    if same is not None and editing is not None:
        ctx.store.rules.delete([editing.id])
    if target is None:
        return ctx.store.rules.add(
            Rule(match=clean.match, type=clean.type, category=clean.category, account=clean.account)
        )
    target.match, target.type, target.category, target.account = clean.match, clean.type, clean.category, clean.account
    return ctx.store.rules.update(target)


@recorded(N_("Delete categorization rules"))
def delete(ctx: Context, rule_ids: list[str]) -> int:
    return ctx.store.rules.delete([i for i in rule_ids if ctx.store.rules.get(i) is not None])


def prefill(type_: str, category: str, payee: str, note: str) -> RuleInput:
    """A rule that categorizes like this: the payee (or, without one, the name found in the note) and the category.

    The account stays empty: most rules should not move a transaction to another account.
    """
    return RuleInput(match=payee.strip() or normalize(note), type=type_, category=category)


def from_transaction(tx: Transaction) -> RuleInput:
    return prefill(tx.type, tx.category, tx.payee, tx.comment)
