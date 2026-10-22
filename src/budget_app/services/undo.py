"""Undo and the change history, for the interface (F28). The recording lives in storage/history.py."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from budget_app.i18n import N_, t, tn
from budget_app.services.context import Context, ValidationError
from budget_app.storage.history import Action, UndoConflict

P = ParamSpec("P")
R = TypeVar("R")

UNDO = N_("Undo")
UNNAMED = N_("Other change")


def recorded(label: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Make a service function one undoable action named `label` (an English text marked with N_)."""

    def deco(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            ctx = args[0]
            if not isinstance(ctx, Context):
                raise TypeError(f"{fn.__name__}: the first argument must be the Context")
            with ctx.store.history.action(label):
                return fn(*args, **kwargs)

        return wrapper

    return deco


def latest(ctx: Context) -> Action | None:
    actions = ctx.store.history.actions(limit=1)
    return actions[0] if actions else None


def recent(ctx: Context, limit: int = 100) -> list[Action]:
    return ctx.store.history.actions(limit=limit)


def label(ctx: Context, action: Action) -> str:
    """The action's name in the interface language; an undo names what it undid."""
    if action.undo_of:
        undone = ctx.store.history.get(action.undo_of)
        return t("Undo: {action}", action=label(ctx, undone) if undone else t(UNNAMED))
    return t(action.label or UNNAMED)


def summary(action: Action) -> str:
    n = len(action.changes)
    return tn("{n} change", "{n} changes", n)


def undo(ctx: Context, action_id: str) -> Action:
    """Undo an action; ValidationError when it is unknown, undone already, or changed since."""
    history = ctx.store.history
    act = history.get(action_id)
    if act is None:
        raise ValidationError(t("This change is no longer in the history"))
    if act.undone:
        raise ValidationError(t("This change has already been undone"))
    try:
        return history.undo(action_id, ctx.store.tables(), UNDO)
    except UndoConflict as exc:
        raise ValidationError(t("This change can’t be undone: some of its records were changed afterwards.")) from exc


TABLE_NAMES = {
    "transactions": N_("Transaction"),
    "transaction_items": N_("Item"),
    "account_entries": N_("Balance"),
    "budget_entries": N_("Budget entry"),
    "accounts": N_("Account"),
    "income_categories": N_("Income category"),
    "recurring": N_("Recurring transaction"),
    "payees": N_("Payee"),
    "rules": N_("Categorization rule"),
    "holdings": N_("Holding"),
    "trades": N_("Trade"),
    "prices": N_("Price"),
    "tags": N_("Tag"),
}
OPS = {"add": N_("added"), "update": N_("changed"), "delete": N_("deleted")}


def describe_change(ctx: Context, change) -> str:
    """One line for the history page: what kind of record, which one, and what happened."""
    raw = change.after or change.before or {}
    accounts = {a.id: a.name for a in ctx.store.accounts.all()}
    if change.table == "transactions":
        what = f"{raw.get('date', '')} · {raw.get('amount', '')} {raw.get('currency', '')}"
    elif change.table == "account_entries":
        what = f"{raw.get('month', '')} · {accounts.get(raw.get('account_id', ''), '')} · {raw.get('amount', '')}"
    elif change.table == "recurring":
        what = f"{raw.get('next_due', '')} · {raw.get('amount', '')} {raw.get('currency', '')}"
    elif change.table == "budget_entries":
        what = f"{raw.get('month', '')} · {raw.get('category', '')} · {raw.get('amount', '')} {raw.get('currency', '')}"
    elif change.table == "rules":
        what = f"{raw.get('match', '')} · {raw.get('category', '')}"
    elif change.table == "trades":
        what = f"{raw.get('date', '')} · {raw.get('kind', '')} · {raw.get('quantity', '')} × {raw.get('price', '')}"
    elif change.table == "prices":
        what = f"{raw.get('date', '')} · {raw.get('price', '')}"
    else:
        what = raw.get("name", "")
    kind = t(TABLE_NAMES.get(change.table, change.table))
    return f"{kind} {t(OPS.get(change.op, change.op))}: {what.strip(' ·')}"
