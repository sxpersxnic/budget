"""Categorization rules (F07): which rule applies to a transaction.

A rule matches when its text, folded (case and accents ignored, runs of spaces as one), occurs anywhere in the
folded payee or in the folded bank text / note: "migros" matches "Migros", "MIGROS ZUERICH HB" and
"Einkauf Migros Basel". When several rules match, the one with the **longest** text wins, being the most
specific ("migros restaurant" before "migros"); between equally long texts the **newest** rule wins, the one
added last.
"""

from __future__ import annotations

from collections.abc import Sequence

from budget_app.domain.coicop import fold
from budget_app.domain.models import Rule


def match_key(text: str) -> str:
    """`text` as rules compare it: folded, with single spaces."""
    return " ".join(fold(text or "").split())


def by_priority(rules: Sequence[Rule]) -> list[Rule]:
    """The rules in the order they are tried: longest text first, then the newest (`rules` is oldest first)."""
    order = sorted(enumerate(rules), key=lambda pair: (-len(match_key(pair[1].match)), -pair[0]))
    return [rule for _, rule in order]


def first_match(ordered: Sequence[Rule], type_: str, payee: str, text: str) -> Rule | None:
    """The first rule of `ordered` (see `by_priority`) for `type_` whose text occurs in the payee or the text."""
    haystacks = [h for h in (match_key(payee), match_key(text)) if h]
    for rule in ordered:
        needle = match_key(rule.match)
        if rule.type == type_ and needle and any(needle in h for h in haystacks):
            return rule
    return None


def best(rules: Sequence[Rule], type_: str, payee: str, text: str) -> Rule | None:
    """The rule that applies to a transaction of `type_` with this payee and text, or None."""
    return first_match(by_priority(rules), type_, payee, text)
