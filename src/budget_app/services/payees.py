"""Payees (F09): the table of merchants and counterparties, finding the payee for a bank text, rename, merge, delete.

Transactions, templates and recurring series store the payee's name (`payee`). The payee table (`payees.csv`)
holds each name once, compared by `key` (case, accents and punctuation ignored, so "MIGROS" and "Migros" are one
payee), with its aliases: the cleaned bank texts imported for it and its former names. `resolve` looks a bank
text up there before it normalizes it, so after a rename or a merge the next import of the same text, or of a
text that normalizes to a former name, lands on the payee the user kept.

Renaming and merging rewrite the name on every transaction, template and series in one undoable action.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, Protocol

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Payee
from budget_app.domain.payees import alias_key, key, normalize
from budget_app.i18n import N_, t
from budget_app.services.aggregates import tx_value
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded
from budget_app.storage.csv_store import CsvTable

# the transaction types that have a payee: transfers, exchanges and revaluations stay between own accounts
PAYEE_TYPES = (TX_EXPENDITURE, TX_INCOME)


def clean_name(name: str) -> str:
    return " ".join((name or "").split())


def all_payees(ctx: Context) -> list[Payee]:
    return sorted(ctx.store.payees.all(), key=lambda p: (key(p.name), p.id))


def find(ctx: Context, name: str) -> Payee | None:
    """The payee called `name`, ignoring case, accents and punctuation."""
    k = key(name)
    if not k:
        return None
    return next((p for p in ctx.store.payees.all() if key(p.name) == k), None)


def _index(ctx: Context) -> dict[str, Payee]:
    """Lookup key -> payee: every alias, then every name (a name wins over another payee's alias)."""
    out: dict[str, Payee] = {}
    payees = ctx.store.payees.all()
    for p in payees:
        for alias in p.alias_list:
            out.setdefault(alias, p)
    for p in payees:
        out[key(p.name)] = p
    return out


def resolve(ctx: Context, raw: str) -> str:
    """The payee name for a bank text: a payee that knows the text (or its normalized name) first, else the
    normalized name. Never a second spelling of an existing payee. "" when the text names no payee."""
    index = _index(ctx)
    if (k := alias_key(raw)) and k in index:
        return index[k].name
    name = normalize(raw)
    hit = index.get(key(name)) if name else None
    return hit.name if hit else name


def _drop_alias(ctx: Context, alias: str, keep: Payee) -> None:
    """An alias belongs to one payee only: take it away from the others."""
    for p in ctx.store.payees.all():
        if p.id != keep.id and alias in p.alias_list:
            other = ctx.store.payees.get(p.id)
            if other is not None:
                other.set_aliases([a for a in other.alias_list if a != alias])
                ctx.store.payees.update(other)


def spelling(ctx: Context, name: str) -> str:
    """The stored spelling of the payee `name`, or the name itself when it is new ("" for no payee).

    Saves nothing, so checking a transaction never adds a payee; `ensure` does when it is stored.
    """
    name = clean_name(name)
    if not key(name):
        return ""
    existing = find(ctx, name)
    return existing.name if existing is not None else name


@recorded(N_("Save a payee"))
def ensure(ctx: Context, name: str) -> str:
    """The stored spelling of the payee `name`, which is added when it is new ("" for no payee)."""
    name = clean_name(name)
    if not key(name):
        return ""
    existing = find(ctx, name)
    if existing is not None:
        return existing.name
    payee = ctx.store.payees.add(Payee(name=name))
    _drop_alias(ctx, key(name), payee)  # a former name of another payee now means this one
    return payee.name


@recorded(N_("Save a payee"))
def learn(ctx: Context, pairs: Iterable[tuple[str, str]]) -> None:
    """Remember bank texts for payees: each (raw text, payee name) adds the payee if needed and the text's
    alias to it (taking it from any other payee, so a payee changed in the import preview is learnt)."""
    for raw, name in pairs:
        stored = ensure(ctx, name)
        alias = alias_key(raw)
        payee = find(ctx, stored) if stored else None
        if payee is None or not alias:
            continue
        if alias != key(payee.name) and alias not in payee.alias_list:
            payee.set_aliases([*payee.alias_list, alias])
            ctx.store.payees.update(payee)
        _drop_alias(ctx, alias, payee)


def names(ctx: Context, limit_recent: int = 10) -> list[str]:
    """Every payee name for the form's suggestions: the recently used ones first, then by name."""
    by_date = sorted(
        enumerate(tx for tx in ctx.store.transactions.all() if tx.payee),
        key=lambda pair: (pair[1].date, pair[0]),
        reverse=True,
    )
    recent: list[str] = []
    for _, tx in by_date:
        if tx.payee not in recent:
            recent.append(tx.payee)
            if len(recent) == limit_recent:
                break
    known = {key(n) for n in recent}
    return recent + [p.name for p in all_payees(ctx) if key(p.name) not in known]


@dataclass
class Usage:
    count: int = 0  # transactions
    spent: float = 0.0  # expenditure, in the view currency
    received: float = 0.0  # income, in the view currency
    last: date | None = None


def usage(ctx: Context, view_cur: str) -> dict[str, Usage]:
    """Per payee key: how many transactions name it, what was spent with it and received from it."""
    out: dict[str, Usage] = defaultdict(Usage)
    for tx in ctx.store.transactions.all():
        if not tx.payee:
            continue
        u = out[key(tx.payee)]
        u.count += 1
        if tx.type == TX_EXPENDITURE:
            u.spent += tx_value(ctx, tx, view_cur)
        elif tx.type == TX_INCOME:
            u.received += tx_value(ctx, tx, view_cur)
        u.last = tx.date if u.last is None or tx.date > u.last else u.last
    return dict(out)


class _HasPayee(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]
    id: str
    payee: str


def _rename_in[T: _HasPayee](table: CsvTable[T], old_keys: set[str], new_name: str) -> None:
    changed = []
    for rec in table.all():
        if rec.payee and key(rec.payee) in old_keys and rec.payee != new_name:
            copy = table.get(rec.id)
            if copy is not None:
                copy.payee = new_name
                changed.append(copy)
    if changed:
        table.update_many(changed)


def _rename_everywhere(ctx: Context, old_keys: set[str], new_name: str) -> None:
    """Set `new_name` on every transaction, template and series whose payee has one of `old_keys`."""
    _rename_in(ctx.store.transactions, old_keys, new_name)
    _rename_in(ctx.store.templates, old_keys, new_name)
    _rename_in(ctx.store.recurring, old_keys, new_name)


@recorded(N_("Rename a payee"))
def rename(ctx: Context, payee_id: str, new_name: str) -> Payee:
    """Rename a payee and every record that names it; the old name stays as an alias."""
    payee = ctx.store.payees.get(payee_id)
    if payee is None:
        raise ValidationError(t("Payee not found"))
    new_name = clean_name(new_name)
    if not key(new_name):
        raise ValidationError(t("Name is required"))
    other = find(ctx, new_name)
    if other is not None and other.id != payee.id:
        raise ValidationError(t("A payee called “{name}” already exists. Merge the two instead.", name=other.name))
    old_key = key(payee.name)
    payee.name = new_name
    aliases = [a for a in payee.alias_list if a != key(new_name)]
    if old_key != key(new_name):
        aliases.append(old_key)
    payee.set_aliases(aliases)
    ctx.store.payees.update(payee)
    _rename_everywhere(ctx, {old_key}, new_name)
    return payee


@recorded(N_("Merge payees"))
def merge(ctx: Context, payee_ids: list[str], keep_id: str) -> Payee:
    """Merge payees into the one with `keep_id`: their transactions, templates, series and aliases move to it,
    their names become its aliases, and they are deleted."""
    keep = ctx.store.payees.get(keep_id)
    others = [p for i in dict.fromkeys(payee_ids) if i != keep_id and (p := ctx.store.payees.get(i)) is not None]
    if keep is None or not others:
        raise ValidationError(t("Select at least two payees to merge"))
    old_keys = {key(p.name) for p in others}
    aliases = [*keep.alias_list]
    for p in others:
        aliases += [*p.alias_list, key(p.name)]
    keep.set_aliases([a for a in aliases if a != key(keep.name)])
    ctx.store.payees.delete([p.id for p in others])
    ctx.store.payees.update(keep)
    _rename_everywhere(ctx, old_keys, keep.name)
    return keep


def in_use(ctx: Context, payee: Payee) -> bool:
    k = key(payee.name)
    names = [r.payee for r in ctx.store.transactions.all()]
    names += [r.payee for r in ctx.store.templates.all()] + [r.payee for r in ctx.store.recurring.all()]
    return any(n and key(n) == k for n in names)


@recorded(N_("Delete payees"))
def delete(ctx: Context, payee_ids: list[str]) -> int:
    """Delete payees that no transaction, template or recurring series names."""
    payees = [p for i in payee_ids if (p := ctx.store.payees.get(i)) is not None]
    used = [p for p in payees if in_use(ctx, p)]
    if used:
        raise ValidationError(t("“{name}” is still in use and can’t be deleted", name=used[0].name))
    return ctx.store.payees.delete([p.id for p in payees])
