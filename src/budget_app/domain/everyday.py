"""Everyday names and search words for the common expenditure categories, and the category picker's options (F08).

COICOP stays what is stored. Every division and group, and the classes people use ("Groceries" for 01.1 Food),
have a short name in each language and search words (shops, brands and the words people type: "Migros", "SBB",
"Miete") in `resources/everyday_categories.json`. A class that only repeats its parent (09.8.0 Package holidays
under 09.8) takes the parent's name; every other category keeps its translated COICOP name. The name is what the
whole app shows (`Category.label`: "01.1 Groceries"); the official name stays on the Categories page and in
tooltips (`Coicop.tooltip`).

The search also finds a category by what its COICOP notes say it includes (F40), after the categories found
by name.

The same search words suggest a category for a payee nobody has categorized yet (F07, `keyword_category`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import cache

from budget_app import i18n
from budget_app.config import RESOURCES_DIR
from budget_app.domain.coicop import ADV, PSEUDO_NAMES, TAX, Category, fold, get_coicop, match_words, note_words
from budget_app.domain.payees import key

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


@cache
def everyday_name(code: str, lang: str) -> str:
    """The everyday name of `code` in `lang`; empty when it has none. A class whose code ends in ".0" and that
    carries its parent's COICOP name (09.8.0 Package holidays under 09.8) takes the parent's everyday name."""
    entry = everyday().get(code)
    if entry is not None:
        return entry.name(lang)
    coicop = get_coicop()
    cat = coicop.get(code)
    parent = coicop.get(cat.parent) if cat and cat.parent and code.endswith(".0") else None
    if cat and parent and parent.name == cat.name:
        return everyday_name(parent.code, lang)
    return ""


@dataclass(frozen=True)
class CategoryOption:
    """One entry of the category picker."""

    code: str
    label: str  # code and everyday name, as everywhere in the app: "01.1 Groceries"
    caption: str  # the official COICOP name under an everyday name; empty where the label shows the official name
    level: int
    search: str  # folded text the search matches: code, names in every language, search words
    recent: bool = False
    note_search: str = ""  # folded words of what the COICOP notes include (F40), matched after `search`


def _option(cat: Category, lang: str) -> CategoryOption:
    official = cat.official_name
    notes = note_words(cat.code, lang)
    name = everyday_name(cat.code, lang)
    if not name:
        search = _search_text((cat.code, official, cat.name))
        return CategoryOption(cat.code, cat.label, "", cat.level, search, note_search=notes)
    caption = "" if cat.code in PSEUDO_NAMES or name == official else official  # Taxes: no official name
    common = everyday().get(cat.code)
    # an inherited name is not searched: typing "Krankenkasse" offers 12.1.2, not 12.1.2.0 below it as well
    names = (*common.names.values(), *common.keywords) if common else ()
    words = (cat.code, *names, official, cat.name)
    return CategoryOption(cat.code, cat.label, caption, cat.level, _search_text(words), note_search=notes)


def _search_text(words: Iterable[str]) -> str:
    return " ".join(dict.fromkeys(fold(w) for w in words))


@cache
def _all_options(lang: str) -> tuple[CategoryOption, ...]:
    """Every COICOP category in COICOP order, then Taxes (advance payments are not chosen by hand)."""
    return tuple(_option(cat, lang) for cat in get_coicop().categories.values() if cat.code != ADV)


@cache
def _by_code(lang: str) -> dict[str, CategoryOption]:
    return {o.code: o for o in _all_options(lang)}


def label(code: str) -> str:
    """The picker's label of one category in the interface language ("01.1 Groceries"); `code` when unknown."""
    option = _by_code(i18n.language()).get(code)
    return option.label if option else code


def category_options(recent: Iterable[str] = ()) -> list[CategoryOption]:
    """The picker's options in the interface language: the `recent` codes first, in their order, then the rest."""
    options = _all_options(i18n.language())
    by_code = {o.code: o for o in options}
    first = [replace(by_code[code], recent=True) for code in dict.fromkeys(recent) if code in by_code]
    shown = {o.code for o in first}
    return first + [o for o in options if o.code not in shown]


def matches(option: CategoryOption, query: str) -> bool:
    """Whether every word of `query` occurs in the option's search text or its note words, ignoring case and
    accents. `resources/category_select.js` filters the dropdown the same way in the browser."""
    return match_words(fold(query), option.search, option.note_search)[0]


def search(options: Iterable[CategoryOption], query: str) -> list[CategoryOption]:
    """The options matching `query`, in their order: those found by name or search word first, then those found
    only by their notes (as `category_select.js` lists them)."""
    folded = fold(query)
    by_name: list[CategoryOption] = []
    by_notes: list[CategoryOption] = []
    for option in options:
        found, named = match_words(folded, option.search, option.note_search)
        if found:
            (by_name if named else by_notes).append(option)
    return by_name + by_notes


# ------------------------------------------------------------------ suggestions
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


@cache
def _keyword_index() -> dict[tuple[str, ...], tuple[str, str]]:
    """Search word, as the words of its payee key -> (code, the word as written), for `keyword_category`.

    A word listed under several codes keeps the most specific one when the others contain it ("SBB": 07.3 and
    07.3.1 → 07.3.1), else the closest category containing them all below the top level ("Swisscom": mobile and
    internet → 08.3 communication services), and is left out when they are unrelated ("Landi": groceries or
    garden). Swiss bank texts often spell umlauts as "ae", "oe", "ue", so "Bäckerei" is found as "Baeckerei" too.
    """
    index = {}
    for words, by_code in _search_words().items():
        if (code := _common(list(by_code))) is not None:
            index[words] = (code, by_code.get(code) or next(iter(by_code.values())))
    return index


def _search_words() -> dict[tuple[str, ...], dict[str, str]]:
    """Search word, as payee key words -> code -> the word as written, for every code that lists it."""
    codes = get_coicop()
    found: dict[tuple[str, ...], dict[str, str]] = defaultdict(dict)
    for code, entry in everyday().items():
        if code != TAX and codes.get(code) is None:
            continue
        for word in entry.keywords:
            for spelling in dict.fromkeys((word, word.translate(_UMLAUTS))):
                if words := tuple(key(spelling).split()):
                    found[words].setdefault(code, word)
    return found


def _common(codes: list[str]) -> str | None:
    """The most specific of `codes` when it lies inside all the others, else the deepest category containing them
    all below the top level, else None."""
    deepest = max(codes, key=lambda c: c.count("."))
    if all(deepest == c or deepest.startswith(c + ".") for c in codes):
        return deepest
    shared: list[str] = []
    for level in zip(*(c.split(".") for c in codes), strict=False):
        if len(set(level)) > 1:
            break
        shared.append(level[0])
    code = ".".join(shared)
    return code if len(shared) >= 2 and get_coicop().get(code) is not None else None


def keyword_category(payee: str) -> tuple[str, str] | None:
    """The category a search word in `payee` points to, and that word as written ("Migros" → 01.1), or None.

    Words are compared whole ("Coopers" is not "Coop"); the longest search word found wins ("Coop Vitality"
    before "Coop"), between equally long ones the first in the payee.
    """
    index = _keyword_index()
    words = key(payee).split()
    best: tuple[str, str] | None = None
    longest = 0
    for start in range(len(words)):
        for end in range(len(words), start + longest, -1):
            if hit := index.get(tuple(words[start:end])):
                best, longest = hit, end - start
                break
    return best
