"""COICOP 2018 classification (4 levels) plus app-specific pseudo-categories, and the UN's notes (F40)."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path

from budget_app import i18n
from budget_app.config import COICOP_FILE, NOTES_FILE, RESOURCES_DIR
from budget_app.i18n import N_, t

TAX = "TAX"
ADV = "ADV"
PSEUDO_NAMES = {TAX: N_("Taxes"), ADV: N_("Advance payments")}


@dataclass
class Category:
    code: str
    name: str
    level: int
    parent: str = ""
    children: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """The name in the interface language (COICOP names have their own translation file)."""
        if self.code in PSEUDO_NAMES:
            return t(self.name)
        return coicop_names(i18n.language()).get(self.name, self.name)

    @property
    def label(self) -> str:
        if self.code in PSEUDO_NAMES:
            return self.display_name
        return f"{self.code} {self.display_name}"


class Coicop:
    def __init__(self, rows: list[tuple[str, str, int, str]]):
        self.categories: dict[str, Category] = {}
        for code, parent, level, name in rows:
            self.categories[code] = Category(code, name, level, parent)
        for cat in self.categories.values():
            if cat.parent:
                self.categories[cat.parent].children.append(cat.code)
        for code in (TAX, ADV):
            self.categories[code] = Category(code, PSEUDO_NAMES[code], 1)

    @property
    def divisions(self) -> list[str]:
        return [c.code for c in self.categories.values() if c.level == 1 and c.code not in PSEUDO_NAMES]

    def get(self, code: str) -> Category | None:
        return self.categories.get(code)

    def label(self, code: str) -> str:
        cat = self.categories.get(code)
        return cat.label if cat else code

    def ancestors(self, code: str) -> list[str]:
        """The code itself followed by all of its parents up to the division."""
        out = []
        while code:
            out.append(code)
            cat = self.categories.get(code)
            code = cat.parent if cat else ""
        return out

    def children(self, code: str) -> list[str]:
        cat = self.categories.get(code)
        return list(cat.children) if cat else []

    def selectable(self) -> dict[str, str]:
        """Options for an expenditure category dropdown: every COICOP level plus taxes."""
        opts = {c.code: (" " * (c.level - 1)) + c.label for c in self.categories.values() if c.code not in PSEUDO_NAMES}
        opts[TAX] = t(PSEUDO_NAMES[TAX])
        return opts


def fold(text: str) -> str:
    """`text` for searching: case and accents ignored ("Café" and "cafe" match)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@cache
def coicop_names(lang: str) -> dict[str, str]:
    """English COICOP name -> name in `lang` (empty for English)."""
    path = RESOURCES_DIR / "i18n" / f"coicop_{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache(maxsize=1)
def get_coicop() -> Coicop:
    with COICOP_FILE.open(newline="", encoding="utf-8") as f:
        rows = [(r["code"], r["parent"], int(r["level"]), r["name"]) for r in csv.DictReader(f)]
    return Coicop(rows)


# ---------------------------------------------------------------- notes (F40)
NOTE_FIELDS = ("includes", "also_includes", "excludes")
SEARCHED_FIELDS = ("includes", "also_includes")  # what a category does *not* cover (excludes) is shown, not searched
_PUNCTUATION = ".,;:!?()[]{}\"'“”‘’«»*/…–—"


@dataclass(frozen=True)
class Notes:
    """What a category includes, also includes and excludes, one item per entry (the UN's COICOP 2018 notes).

    `translated` is True for the app's own, unofficial translation; False for the UN's English text."""

    includes: tuple[str, ...]
    also_includes: tuple[str, ...]
    excludes: tuple[str, ...]
    translated: bool = False

    def items(self, name: str) -> tuple[str, ...]:
        return getattr(self, name)

    def as_dict(self) -> dict[str, list[str]]:
        return {name: list(self.items(name)) for name in NOTE_FIELDS}


def notes_file(lang: str) -> Path:
    """`resources/coicop_notes.json` holds the English notes, `coicop_notes_<lang>.json` a translation."""
    return NOTES_FILE if lang == i18n.DEFAULT else NOTES_FILE.with_name(f"{NOTES_FILE.stem}_{lang}.json")


@cache
def _notes_data(lang: str) -> tuple[dict[str, dict[str, str]], bool]:
    """(code -> {field: items joined by newlines}, whether translated), read on first use.

    A language whose file is missing or still marked `_untranslated` gets the English notes."""
    path = notes_file(lang)
    if lang != i18n.DEFAULT and not path.exists():
        return _notes_data(i18n.DEFAULT)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if lang != i18n.DEFAULT and raw.get("_untranslated"):
        return _notes_data(i18n.DEFAULT)
    return {code: entry for code, entry in raw.items() if not code.startswith("_")}, lang != i18n.DEFAULT


def notes_translated(lang: str) -> bool:
    """Whether the notes in `lang` are the app's own translation (otherwise they are the UN's English)."""
    return _notes_data(lang)[1]


@cache
def notes(code: str, lang: str) -> Notes | None:
    """The notes of `code` in `lang`, English where the translation lacks a text; None when the UN gives none."""
    english = _notes_data(i18n.DEFAULT)[0].get(code)
    if not english or not any(english.values()):
        return None
    entries, translated = _notes_data(lang)
    entry = entries.get(code, {})
    texts = {name: entry.get(name) or english[name] for name in NOTE_FIELDS}
    items = {name: tuple(texts[name].split("\n")) if texts[name] else () for name in NOTE_FIELDS}
    translated = translated and any(entry.get(name) for name in NOTE_FIELDS)
    return Notes(items["includes"], items["also_includes"], items["excludes"], translated)


def _words(text: str) -> list[str]:
    """The folded words of `text`, without punctuation, numbers (such as codes) and single letters."""
    out = []
    for raw in fold(text).split():
        word = raw.strip(_PUNCTUATION)
        if len(word) > 1 and any(c.isalpha() for c in word):
            out.append(word)
    return out


@cache
def note_words(code: str, lang: str) -> str:
    """What `code` includes and also includes, in `lang` and in English: folded words, each once.

    Searches match them after the names and search words. *Excludes* is left out: a category is never found
    by what it does not cover."""
    found: dict[str, None] = {}
    for language in dict.fromkeys((lang, i18n.DEFAULT)):
        note = notes(code, language)
        if note is None:
            continue
        for name in SEARCHED_FIELDS:
            for item in note.items(name):
                found.update(dict.fromkeys(_words(item)))
    return " ".join(found)


def match_words(query: str, primary: str, secondary: str = "") -> tuple[bool, bool]:
    """For the folded `query`: (every word occurs in `primary` or `secondary`, every word occurs in `primary`).

    The first says whether a category matches, the second whether it matches by name (listed first)."""
    words = query.split()
    return all(w in primary or w in secondary for w in words), all(w in primary for w in words)


@cache
def notes_payload(lang: str) -> dict:
    """The notes of every category in `lang` for the browser: {"translated": bool, "notes": {code: {field: [...]}}}."""
    found = {code: n.as_dict() for code in get_coicop().categories if (n := notes(code, lang))}
    return {"translated": notes_translated(lang), "notes": found}


@cache
def notes_version(lang: str) -> str:
    """A short hash of `notes_payload(lang)`: the browser may cache the notes until it changes."""
    text = json.dumps(notes_payload(lang), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:12]
