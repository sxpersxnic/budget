"""Everyday names and search words for the common expenditure categories, and the category picker's options (F08).

COICOP stays what is stored. About 80 codes people use every day ("Groceries" for 01.1 Food) have a short
name in each language and search words (shops, brands and the words people type: "Migros", "SBB", "Miete")
in `resources/everyday_categories.json`. Every other category keeps its translated COICOP name.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import cache

from budget_app import i18n
from budget_app.config import RESOURCES_DIR
from budget_app.domain.coicop import ADV, PSEUDO_NAMES, Category, fold, get_coicop

EVERYDAY_FILE = RESOURCES_DIR / "everyday_categories.json"


@dataclass(frozen=True)
class Everyday:
    names: dict[str, str]  # language -> everyday name
    keywords: tuple[str, ...]

    def name(self, lang: str) -> str:
        return self.names.get(lang) or self.names[i18n.DEFAULT]


@cache
def everyday() -> dict[str, Everyday]:
    """COICOP code (or `TAX`) -> its everyday names and search words."""
    raw = json.loads(EVERYDAY_FILE.read_text(encoding="utf-8"))
    return {
        code: Everyday({lang: entry[lang] for lang in i18n.LANGUAGES}, tuple(entry["keywords"]))
        for code, entry in raw.items()
    }


@dataclass(frozen=True)
class CategoryOption:
    """One entry of the category picker."""

    code: str
    label: str  # "Groceries · 01.1" for a common category, "01.1.1 Cereals and cereal products" otherwise
    caption: str  # the official COICOP name under an everyday name; empty otherwise
    level: int
    search: str  # folded text the search matches: code, names in every language, search words
    recent: bool = False


def _option(cat: Category, lang: str) -> CategoryOption:
    official = cat.display_name
    common = everyday().get(cat.code)
    if common is None:
        return CategoryOption(cat.code, cat.label, "", cat.level, _search_text((cat.code, official, cat.name)))
    name = common.name(lang)
    if cat.code in PSEUDO_NAMES:  # Taxes: no code and no official name to show
        label, caption = name, ""
    else:
        label, caption = f"{name} · {cat.code}", official
    words = (cat.code, *common.names.values(), official, cat.name, *common.keywords)
    return CategoryOption(cat.code, label, caption, cat.level, _search_text(words))


def _search_text(words: Iterable[str]) -> str:
    return " ".join(dict.fromkeys(fold(w) for w in words))


@cache
def _all_options(lang: str) -> tuple[CategoryOption, ...]:
    """Every COICOP category in COICOP order, then Taxes (advance payments are not chosen by hand)."""
    return tuple(_option(cat, lang) for cat in get_coicop().categories.values() if cat.code != ADV)


def category_options(recent: Iterable[str] = ()) -> list[CategoryOption]:
    """The picker's options in the interface language: the `recent` codes first, in their order, then the rest."""
    options = _all_options(i18n.language())
    by_code = {o.code: o for o in options}
    first = [replace(by_code[code], recent=True) for code in dict.fromkeys(recent) if code in by_code]
    shown = {o.code for o in first}
    return first + [o for o in options if o.code not in shown]


def matches(option: CategoryOption, query: str) -> bool:
    """Whether every word of `query` occurs in the option's search text, ignoring case and accents.

    `resources/category_select.js` filters the dropdown the same way in the browser."""
    return all(word in option.search for word in fold(query).split())


def search(options: Iterable[CategoryOption], query: str) -> list[CategoryOption]:
    return [o for o in options if matches(o, query)]
