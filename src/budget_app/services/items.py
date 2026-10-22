"""Product lists of transactions: the items on a receipt, each with quantity and unit price.

Items never change the transaction amount: their total is compared with it, because receipts often include
deposits, rounding or items the user doesn't list.

**Split lines** (F11, decision 14): an item of an expenditure may have a category and tags of its own. Reports
then count its total there and the rest in the transaction's category (`domain.splits`); lines with a category
may not add up to more than the amount. Income, transfers, exchanges and revaluations are not split: their
lines keep no category or tags.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from budget_app.domain import splits
from budget_app.domain import tags as tag_codec
from budget_app.domain.coicop import TAX, get_coicop
from budget_app.domain.models import TX_EXPENDITURE, TransactionItem
from budget_app.i18n import N_, t
from budget_app.services import tags as tag_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded


@dataclass
class ItemInput:
    name: str = ""
    quantity: float | None = 1.0
    unit_price: float | None = None
    category: str = ""  # F11: empty keeps the transaction's category
    tags: list[str] = field(default_factory=list)  # F11: tag ids, or names of new tags (added when stored)

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
        if item.category and item.category != TAX and get_coicop().get(item.category) is None:
            raise ValidationError(t("Item {n}: select an expenditure category", n=n))
    return rows


def lines(inputs: list[ItemInput]) -> list[splits.Line]:
    """The filled-in rows as `splits.Line`s (tag entries as they are: only their presence matters here)."""
    return [splits.Line(i.total, i.category, tag_codec.join(i.tags)) for i in inputs if not i.is_blank]


def check_split(inputs: list[ItemInput], amount: float | None, category: str, currency: str = "") -> None:
    """Refuse split lines (an own category or tags) of an expenditure that add up to more than its amount."""
    if amount is None:
        return
    rows = lines(inputs)
    over = splits.overflow(amount, rows, category)
    if over > 0:
        raise ValidationError(
            t(
                "The items with their own category or tags add up to {total}, {over} more than the amount",
                total=f"{splits.split_total(rows, category):,.2f} {currency}".strip(),
                over=f"{over:,.2f} {currency}".strip(),
            )
        )


@recorded(N_("Save items"))
def replace(ctx: Context, transaction_id: str, inputs: list[ItemInput]) -> list[TransactionItem]:
    """Make `inputs` the product list of the transaction (an empty list removes it).

    Lines keep their category and tags only on an expenditure (F11); new tag names become tags here.
    """
    rows = validate(inputs)
    tx = ctx.store.transactions.get(transaction_id)
    if tx is None:
        raise ValidationError(t("Transaction not found"))
    split = tx.type == TX_EXPENDITURE
    if split:
        check_split(rows, tx.amount, tx.category, tx.currency)
    old = [i.id for i in items_for(ctx, transaction_id)]
    new = [
        TransactionItem(
            transaction_id=transaction_id,
            name=item.name.strip(),
            quantity=round(float(item.quantity or 1), 3),
            unit_price=round(float(item.unit_price or 0), 2),
            position=pos,
            category=item.category if split and item.category != tx.category else "",
            tags=tag_svc.ensure(ctx, item.tags) if split and item.tags else "",
        )
        for pos, item in enumerate(rows)
    ]
    ctx.store.transaction_items.delete(old)
    return ctx.store.transaction_items.add_many(new) if new else []
