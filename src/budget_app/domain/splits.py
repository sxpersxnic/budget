"""Split transactions (F11, decision 14): the parts an expenditure's amount is counted in.

The item lines of an expenditure may each have a category, and tags, of their own. Reports, budgets, charts and
tag totals then count each such line in its category and the rest in the transaction's category. This module is
the one place that decides how much goes where; `services.aggregates.transaction_parts` hands it a stored
transaction with its lines, and every sum by category or tag goes through that.

**The rule** (architecture §4.3):

* A *split line* is an item line with a category other than the transaction's, or with tags of its own. Lines
  without either simply belong to the rest.
* A split line counts its total in the transaction's currency, and in CHF the way the transaction is converted
  (its own rate, `amount_chf / amount`), rounded to the cent. Split lines with the same category and tags form
  one part.
* The *rest* (the amount minus the split lines: lines without a category, rounding, deposits, anything not
  itemised) stays in the transaction's category with the transaction's tags. It takes whatever is needed for the
  parts to add up to `amount` and `amount_chf` exactly. When nothing is left of the amount, the CHF rounding goes
  to the largest split part instead, so that no category gets a stray cent.
* A negative split line (a discount, or a deposit returned, for a product of that category) lowers its category
  and leaves the rest higher by as much; the transaction's total never changes. A category may therefore get a
  negative part, like a refund. Split lines together may not exceed the amount (`overflow`): saving refuses it.
* An advance payment (a share paid for others) is shared proportionally: every part gives the same fraction,
  advance / amount, to *Advance payments* (`ADV`), rounded to the cent per part, the rounding again taken by the
  rest. *Advance payments* thus gets exactly the advance share (in CHF: at the transaction's rate, to the cent),
  and each category its own share. Repayments and settlement only look at the advance share, so they don't change.

Only expenditure is split: income, transfers, exchanges and revaluations ignore item categories.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from budget_app.domain import tags as tag_codec
from budget_app.domain.coicop import ADV

CENT = 100  # the original currency, in cents
MICRO = 1_000_000  # CHF, as stored (`amount_chf` keeps 6 decimals)
_CENT_IN_MICRO = MICRO // CENT


@dataclass(frozen=True)
class Line:
    """What a split needs to know of an item line."""

    total: float  # quantity × unit price, in the transaction's currency (negative for a discount)
    category: str = ""  # empty: the transaction's
    tags: str = ""  # tag ids separated by "|"


@dataclass(frozen=True)
class Part:
    """A share of a transaction counted in one category, with the tags it counts for."""

    category: str
    amount: float  # in the transaction's currency
    amount_chf: float
    tags: str = ""  # tag ids separated by "|": the line's own, then the transaction's

    @property
    def tag_ids(self) -> list[str]:
        return tag_codec.split(self.tags)


def is_split(line: Line, category: str) -> bool:
    """Whether a line counts apart from the rest: its own category, or tags of its own."""
    return bool(line.category and line.category != category) or bool(tag_codec.split(line.tags))


def split_total(lines: Iterable[Line], category: str) -> float:
    """The sum of the split lines, in the transaction's currency."""
    return sum(_units(line.total, CENT) for line in lines if is_split(line, category)) / CENT


def overflow(amount: float, lines: Iterable[Line], category: str) -> float:
    """How much the split lines exceed the amount (0 when they fit)."""
    over = sum(_units(line.total, CENT) for line in lines if is_split(line, category)) - _units(amount, CENT)
    return max(over, 0) / CENT


def rest(amount: float, lines: Iterable[Line], category: str) -> float:
    """What stays in the transaction's own category, in its currency (before an advance share)."""
    return round(amount - split_total(lines, category), 2)


def parts(
    amount: float,
    amount_chf: float,
    category: str,
    lines: Iterable[Line] = (),
    advance: float = 0.0,
    tags: str = "",
) -> list[Part]:
    """The parts of an expenditure: (category, amount, amount_chf, tags), adding up to the transaction exactly.

    The rest in `category` comes first, then the split lines in their order, then the advance share (`ADV`).
    Parts that are zero in both currencies are left out.
    """
    total, total_chf, adv = _units(amount, CENT), _units(amount_chf, MICRO), _units(advance, CENT)
    split = [line for line in lines if is_split(line, category)]
    if total <= 0 or (not split and adv <= 0):
        return [Part(category, amount, amount_chf, tags)]
    own_tags = tag_codec.split(tags)
    rest_key = (category, tag_codec.join(own_tags))
    # per (category, tags): [cents, micro CHF]; the rest first
    buckets: dict[tuple[str, str], list[int]] = {rest_key: [0, 0]}
    for line in split:
        key = (line.category or category, tag_codec.join([*tag_codec.split(line.tags), *own_tags]))
        buckets.setdefault(key, [0, 0])[0] += _units(line.total, CENT)
    for key, bucket in buckets.items():
        if key != rest_key:
            bucket[1] = _cents(bucket[0], total_chf, total)
    buckets[rest_key][0] += total - sum(b[0] for b in buckets.values())
    # the rest takes the rounding; when nothing is left of it, the largest split part does
    taker = rest_key if buckets[rest_key][0] else max(buckets, key=lambda k: abs(buckets[k][0]))
    buckets[taker][1] += total_chf - sum(b[1] for b in buckets.values())

    shares = {key: [0, 0] for key in buckets}  # the advance share of each part
    if adv > 0:
        adv = min(adv, total)
        adv_chf = total_chf if adv == total else _cents(total_chf, adv, total)
        for key, (cents, micro) in buckets.items():
            shares[key] = [cents, micro] if adv == total else [_scale(cents, adv, total), _cents(micro, adv, total)]
        shares[taker][0] += adv - sum(s[0] for s in shares.values())
        shares[taker][1] += adv_chf - sum(s[1] for s in shares.values())

    out = []
    for (code, part_tags), (cents, micro) in buckets.items():
        share = shares[(code, part_tags)]
        if cents - share[0] or micro - share[1]:
            out.append(Part(code, (cents - share[0]) / CENT, (micro - share[1]) / MICRO, part_tags))
    if adv > 0:
        out.append(Part(ADV, adv / CENT, adv_chf / MICRO, tag_codec.join(own_tags)))
    return out


# ------------------------------------------------------------------ whole units
def _units(value: float, unit: int) -> int:
    return round(value * unit)


def _scale(value: int, num: int, den: int) -> int:
    """value × num / den in whole numbers, rounded half away from zero (den > 0, num ≥ 0)."""
    q = (2 * abs(value) * num + den) // (2 * den)
    return q if value >= 0 else -q


def _cents(value: int, num: int, den: int) -> int:
    """value × num / den, an amount in micro CHF, rounded to the cent (half away from zero)."""
    return _scale(value, num, den * _CENT_IN_MICRO) * _CENT_IN_MICRO
