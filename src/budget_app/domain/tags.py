"""Tags (F10): how a record's tags are written, and how tag names are compared.

A transaction, template or recurring series stores the ids of its tags in one text column, separated by "|"
(`Transaction.tags`). Ids, not names, so renaming a tag touches one row of `tags.csv` and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterable

from budget_app.domain.coicop import fold

SEP = "|"
# a tag that does not exist yet: checking a transaction names it without writing anything (services.tags.encode)
PENDING = "+"


def split(text: str) -> list[str]:
    """The entries of a tags column, in order."""
    return [part for part in (text or "").split(SEP) if part]


def join(entries: Iterable[str]) -> str:
    """A tags column from entries, each once, in the order given."""
    return SEP.join(dict.fromkeys(e for e in entries if e))


def clean_name(name: str) -> str:
    """A tag name as stored: single spaces, no "|" (it separates the tags of a record)."""
    return " ".join((name or "").replace(SEP, " ").split())


def key(name: str) -> str:
    """A tag name for comparing: case, accents and spacing ignored ("Ferien Itàlia" = "ferien  italia")."""
    return fold(clean_name(name))
