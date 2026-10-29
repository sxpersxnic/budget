"""Tags (F10): labels across categories, their totals and their budgets.

Transactions, templates and recurring series name their tags by id (`domain.tags`). The form and bulk edit hand
over *entries*: the id of a known tag, or the name of a tag that does not exist yet. `encode` turns entries into
a tags column without writing anything (a new name stays pending, `domain.tags.PENDING` + name), so checking a
transaction never adds a tag; `store` creates the pending tags when the record is saved, the way payees are only
added when a transaction is stored (F09).

Deleting a tag takes it off every transaction, item line, template and series, in the same undoable step; merging
moves the others' records to the tag kept. Renaming changes `tags.csv` only.

**Totals and budgets** count money from `tagged_amounts`: the own share of tagged expenditure as *spent* (the part
paid for others is an advance, which comes back) and tagged income other than repayments as *received*. Transfers,
exchanges and revaluations carry tags but move no money in or out. A tag's budget compares its *spent* within the
budget's period (open ends allowed) with the budget, converted at the latest rate like budget entries; income does
not reduce it. A split line (F11) counts its part for its own tags as well as for the transaction's.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, Protocol

from budget_app.domain.coicop import ADV
from budget_app.domain.models import TAG_COLOURS, TAG_DEFAULT_COLOUR, TX_EXPENDITURE, TX_INCOME, Tag, Transaction
from budget_app.domain.splits import Line
from budget_app.domain.tags import PENDING, clean_name, join, key, split
from budget_app.i18n import N_, t
from budget_app.services.aggregates import part_value, split_lines, transaction_parts, tx_value
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded
from budget_app.storage.csv_store import CsvTable

SPENT, RECEIVED = "spent", "received"
# the form of `models.new_id()`: an entry like this that names no tag is a reference left by an undo, not a name
_ID = re.compile(r"[0-9a-f]{12}")


# ------------------------------------------------------------------ lookups
def all_tags(ctx: Context) -> list[Tag]:
    return sorted(ctx.store.tags.all(), key=lambda g: (key(g.name), g.id))


def by_id(ctx: Context) -> dict[str, Tag]:
    return {g.id: g for g in ctx.store.tags.all()}


def find(ctx: Context, name: str) -> Tag | None:
    """The tag called `name`, ignoring case, accents and spacing."""
    k = key(name)
    if not k:
        return None
    return next((g for g in ctx.store.tags.all() if key(g.name) == k), None)


def tags_of(ctx: Context, column: str) -> list[Tag]:
    """The stored tags a tags column names, by name (unknown and pending entries left out)."""
    known = by_id(ctx)
    return sorted((known[i] for i in split(column) if i in known), key=lambda g: (key(g.name), g.id))


def colour(tag: Tag) -> str:
    """The tag's palette colour; anything unknown shows as the default."""
    return tag.colour if tag.colour in TAG_COLOURS else TAG_DEFAULT_COLOUR


# ------------------------------------------------------------------ entries: ids or new names
def encode(ctx: Context, entries: Iterable[str]) -> str:
    """A tags column for `entries` (tag ids, or names), each tag once; writes nothing.

    A name of a stored tag becomes its id, a new name stays pending until `store`.
    """
    known = by_id(ctx)
    keys = {key(g.name): g.id for g in known.values()}
    out: list[str] = []
    for entry in entries:
        if entry in known:
            out.append(entry)
            continue
        if _ID.fullmatch(entry):  # a tag that no longer exists
            continue
        name = clean_name(entry)
        k = key(name)
        if not k:
            continue
        if k not in keys:
            keys[k] = PENDING + name  # the same new name twice is one new tag
        out.append(keys[k])
    return join(out)


@recorded(N_("Save a tag"))
def store(ctx: Context, column: str) -> str:
    """`column` with its pending tags created; returns the column of ids."""
    out = []
    for entry in split(column):
        if not entry.startswith(PENDING):
            out.append(entry)
            continue
        name = entry[len(PENDING) :]
        existing = find(ctx, name)
        out.append(existing.id if existing else ctx.store.tags.add(Tag(name=clean_name(name))).id)
    return join(out)


def ensure(ctx: Context, entries: Iterable[str]) -> str:
    """The tags column for `entries`, with new names added as tags (for records that are saved now)."""
    return store(ctx, encode(ctx, entries))


def options(ctx: Context) -> dict[str, str]:
    """Every tag for a dropdown: id -> name, by name."""
    return {g.id: g.name for g in all_tags(ctx)}


# ------------------------------------------------------------------ add, rename, recolour, merge, delete
def _check_name(ctx: Context, name: str, tag_id: str = "") -> str:
    name = clean_name(name)
    if not key(name):
        raise ValidationError(t("Name is required"))
    other = find(ctx, name)
    if other is not None and other.id != tag_id:
        raise ValidationError(t("A tag called “{name}” already exists", name=other.name))
    return name


def _check_colour(value: str) -> str:
    if value not in TAG_COLOURS:
        raise ValidationError(t("Choose one of the colours"))
    return value


def _get(ctx: Context, tag_id: str) -> Tag:
    tag = ctx.store.tags.get(tag_id)
    if tag is None:
        raise ValidationError(t("Tag not found"))
    return tag


@recorded(N_("Save a tag"))
def add(ctx: Context, name: str, colour_key: str = TAG_DEFAULT_COLOUR) -> Tag:
    return ctx.store.tags.add(Tag(name=_check_name(ctx, name), colour=_check_colour(colour_key)))


@recorded(N_("Rename a tag"))
def rename(ctx: Context, tag_id: str, new_name: str) -> Tag:
    """Rename a tag; the records name it by id, so nothing else changes."""
    tag = _get(ctx, tag_id)
    tag.name = _check_name(ctx, new_name, tag.id)
    return ctx.store.tags.update(tag)


@recorded(N_("Change a tag’s colour"))
def recolour(ctx: Context, tag_id: str, colour_key: str) -> Tag:
    tag = _get(ctx, tag_id)
    tag.colour = _check_colour(colour_key)
    return ctx.store.tags.update(tag)


class _Tagged(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Any]]
    id: str
    tags: str


def _rewrite_in[T: _Tagged](table: CsvTable[T], replace: dict[str, str]) -> list[T]:
    """Replace tag ids by others (or drop them, for "") in every record of `table`; returns the records changed."""
    changed = []
    for rec in table.all():
        ids = split(rec.tags)
        if not any(i in replace for i in ids):
            continue
        copy = table.get(rec.id)
        if copy is not None:
            copy.tags = join(replace.get(i, i) for i in ids)
            changed.append(copy)
    if changed:
        table.update_many(changed)
    return changed


def _rewrite(ctx: Context, replace: dict[str, str]) -> int:
    """Rewrite the tags of every transaction, item line (F11), template, series and tax section (F20); returns the
    number of transactions changed, on themselves or on one of their lines."""
    changed = {tx.id for tx in _rewrite_in(ctx.store.transactions, replace)}
    changed |= {item.transaction_id for item in _rewrite_in(ctx.store.transaction_items, replace)}
    _rewrite_in(ctx.store.templates, replace)
    _rewrite_in(ctx.store.recurring, replace)
    _rewrite_in(ctx.store.tax_sections, replace)  # the tags linked to the tax summary's sections (F20)
    return len(changed)


@recorded(N_("Merge tags"))
def merge(ctx: Context, tag_ids: list[str], keep_id: str) -> Tag:
    """Merge tags into the one with `keep_id`: the others' transactions, templates and series get it instead, and
    the others are deleted. The tag kept keeps its name, colour and budget."""
    keep = ctx.store.tags.get(keep_id)
    others = [g for i in dict.fromkeys(tag_ids) if i != keep_id and (g := ctx.store.tags.get(i)) is not None]
    if keep is None or not others:
        raise ValidationError(t("Select at least two tags to merge"))
    _rewrite(ctx, {g.id: keep.id for g in others})
    ctx.store.tags.delete([g.id for g in others])
    return keep


@recorded(N_("Delete tags"))
def delete(ctx: Context, tag_ids: list[str]) -> int:
    """Delete tags and take them off every transaction, template and series (one step to undo).

    Returns the number of transactions that lost a tag."""
    ids = [i for i in tag_ids if ctx.store.tags.get(i) is not None]
    if not ids:
        raise ValidationError(t("Tag not found"))
    n = _rewrite(ctx, dict.fromkeys(ids, ""))
    ctx.store.tags.delete(ids)
    return n


@recorded(N_("Set a tag budget"))
def set_budget(
    ctx: Context,
    tag_id: str,
    amount: float | None,
    currency: str = "",
    start: date | None = None,
    end: date | None = None,
) -> Tag:
    """Give a tag a budget for its spending between `start` and `end` (either may be open); no amount removes it."""
    tag = _get(ctx, tag_id)
    if not amount:
        tag.budget, tag.budget_currency, tag.budget_start, tag.budget_end = 0.0, "", "", ""
        return ctx.store.tags.update(tag)
    if amount < 0:
        raise ValidationError(t("Amount must be a positive number"))
    if not currency:
        raise ValidationError(t("Currency is required"))
    if start and end and end < start:
        raise ValidationError(t("The end must not be before the start"))
    tag.budget, tag.budget_currency = round(float(amount), 2), currency
    tag.budget_start = start.isoformat() if start else ""
    tag.budget_end = end.isoformat() if end else ""
    return ctx.store.tags.update(tag)


# ------------------------------------------------------------------ money per tag
@dataclass
class TaggedAmount:
    """Money that counts for a tag: what was spent or received, in the view currency."""

    tag_id: str
    date: date
    kind: str  # SPENT or RECEIVED
    value: float
    transaction_id: str


def transaction_amounts(
    ctx: Context, tx: Transaction, view_cur: str, lines: list[Line] | None = None
) -> list[tuple[str, str, float]]:
    """(tag id, kind, value) a transaction counts for its tags: the own share of an expenditure, per part (a split
    line counts for its own tags and the transaction's, F11), income other than a repayment for the transaction's
    tags; nothing for transfers, exchanges and revaluations."""
    if tx.type == TX_EXPENDITURE:
        out = []
        for part in transaction_parts(ctx, tx, lines):
            value = part_value(ctx, tx, part, view_cur)
            if part.category != ADV and value:
                out += [(tag_id, SPENT, value) for tag_id in part.tag_ids]
        return out
    if tx.type == TX_INCOME and not tx.is_advance:
        value = tx_value(ctx, tx, view_cur)
        return [(tag_id, RECEIVED, value) for tag_id in split(tx.tags)]
    return []


def transaction_tags(tx: Transaction, lines: list[Line] | None = None) -> list[str]:
    """Every tag id a transaction carries: its own, then those of its split lines (expenditure only, F11)."""
    line_tags = [i for line in lines or [] for i in split(line.tags)] if tx.type == TX_EXPENDITURE else []
    return split(join([*split(tx.tags), *line_tags]))


def _from_transactions(ctx: Context, view_cur: str) -> Iterator[TaggedAmount]:
    lines = split_lines(ctx)
    for tx in ctx.store.transactions.all():
        own = lines.get(tx.id, [])
        if not tx.tags and not own:
            continue
        for tag_id, kind, value in transaction_amounts(ctx, tx, view_cur, own):
            yield TaggedAmount(tag_id, tx.date, kind, value, tx.id)


def _within(d: date, start: date | None, end: date | None) -> bool:
    return (start is None or d >= start) and (end is None or d <= end)


def tagged_amounts(
    ctx: Context, view_cur: str, start: date | None = None, end: date | None = None
) -> Iterator[TaggedAmount]:
    """Every amount that counts for a tag, dated within `start` and `end` (each optional).

    A transaction with two tags counts fully for each. A split line's tags (F11) count that line's part on top
    of the transaction's tags, so a tag on one line of a receipt counts only that line.
    """
    for amount in _from_transactions(ctx, view_cur):
        if _within(amount.date, start, end):
            yield amount


@dataclass
class TagTotal:
    count: int = 0  # transactions with the tag
    spent: float = 0.0
    received: float = 0.0
    last: date | None = None


def totals(ctx: Context, view_cur: str, start: date | None = None, end: date | None = None) -> dict[str, TagTotal]:
    """Per tag id: how many transactions carry it (on the transaction or on one of its lines), and what was spent
    and received, within the period."""
    out: dict[str, TagTotal] = defaultdict(TagTotal)
    lines = split_lines(ctx)
    for tx in ctx.store.transactions.all():
        if (tx.tags or tx.id in lines) and _within(tx.date, start, end):
            for tag_id in transaction_tags(tx, lines.get(tx.id)):
                total = out[tag_id]
                total.count += 1
                total.last = tx.date if total.last is None or tx.date > total.last else total.last
    for amount in tagged_amounts(ctx, view_cur, start, end):
        if amount.kind == SPENT:
            out[amount.tag_id].spent += amount.value
        else:
            out[amount.tag_id].received += amount.value
    return dict(out)


@dataclass
class BudgetStatus:
    """A tag's spending within its budget's period against the budget, both in the view currency."""

    budget: float
    actual: float
    currency: str
    start: date | None
    end: date | None

    @property
    def remaining(self) -> float:
        return self.budget - self.actual

    @property
    def ratio(self) -> float:
        return self.actual / self.budget if self.budget else 0.0

    @property
    def over(self) -> bool:
        return self.actual > self.budget + 0.005


def budget_status(ctx: Context, tag: Tag, view_cur: str) -> BudgetStatus | None:
    """Actual against budget for a tag with a budget (None without): the own share of tagged expenditure within
    the budget's period. Income does not count against it."""
    if not tag.has_budget:
        return None
    start, end = tag.start_date, tag.end_date
    actual = sum(a.value for a in tagged_amounts(ctx, view_cur, start, end) if a.tag_id == tag.id and a.kind == SPENT)
    budget = ctx.convert_on(tag.budget, tag.budget_currency, view_cur, None)
    return BudgetStatus(round(budget, 2), round(actual, 2), view_cur, start, end)
