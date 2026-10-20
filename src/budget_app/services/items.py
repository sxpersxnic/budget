"""Product lists of transactions: the items on a receipt, each with quantity and unit price.

Items are informational: their total is compared with the transaction amount but never changes it,
because receipts often include deposits, rounding or items the user doesn't list.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from budget_app.domain.models import TransactionItem
from budget_app.i18n import N_, t
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded


@dataclass
class ItemInput:
    name: str = ""
    quantity: float | None = 1.0
    unit_price: float | None = None

    @property
    def is_blank(self) -> bool:
        return not self.name.strip() and self.unit_price is None

    @property
    def total(self) -> float:
        return round((self.quantity or 0) * (self.unit_price or 0), 2)


def items_for(ctx: Context, transaction_id: str) -> list[TransactionItem]:
    rows = [i for i in ctx.store.transaction_items.all() if i.transaction_id == transaction_id]
    return sorted(rows, key=lambda i: i.position)


def total(items: list[TransactionItem] | list[ItemInput]) -> float:
    return round(sum(i.total for i in items), 2)


def counts(ctx: Context) -> Counter[str]:
    """Number of items per transaction id."""
    return Counter(i.transaction_id for i in ctx.store.transaction_items.all())


def names_by_transaction(ctx: Context) -> dict[str, str]:
    """All item names of each transaction, for searching transactions by what was bought."""
    out: dict[str, list[str]] = {}
    for i in ctx.store.transaction_items.all():
        out.setdefault(i.transaction_id, []).append(i.name)
    return {k: " ".join(v) for k, v in out.items()}


def known_names(ctx: Context, limit: int = 500) -> list[str]:
    """Item names used before, most frequent first, for autocompletion."""
    return [n for n, _ in Counter(i.name for i in ctx.store.transaction_items.all()).most_common(limit)]


def validate(inputs: list[ItemInput]) -> list[ItemInput]:
    """The filled-in rows (blank rows are ignored); raises for a row that is only half filled in."""
    rows = [i for i in inputs if not i.is_blank]
    for n, item in enumerate(rows, start=1):
        if not item.name.strip():
            raise ValidationError(t("Item {n}: enter a name", n=n))
        if item.unit_price is None:
            raise ValidationError(t("Item {n}: enter a price", n=n))
        if item.quantity is None or item.quantity <= 0:
            raise ValidationError(t("Item {n}: the quantity must be greater than 0", n=n))
    return rows


@recorded(N_("Save items"))
def replace(ctx: Context, transaction_id: str, inputs: list[ItemInput]) -> list[TransactionItem]:
    """Make `inputs` the product list of the transaction (an empty list removes it)."""
    rows = validate(inputs)
    if ctx.store.transactions.get(transaction_id) is None:
        raise ValidationError(t("Transaction not found"))
    old = [i.id for i in items_for(ctx, transaction_id)]
    new = [
        TransactionItem(
            transaction_id=transaction_id,
            name=item.name.strip(),
            quantity=round(float(item.quantity or 1), 3),
            unit_price=round(float(item.unit_price or 0), 2),
            position=pos,
        )
        for pos, item in enumerate(rows)
    ]
    ctx.store.transaction_items.delete(old)
    return ctx.store.transaction_items.add_many(new) if new else []
