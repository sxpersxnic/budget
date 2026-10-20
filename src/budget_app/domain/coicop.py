"""COICOP 2018 classification (4 levels) plus app-specific pseudo-categories."""

from __future__ import annotations

import csv
import json
import unicodedata
from dataclasses import dataclass, field
from functools import cache, lru_cache

from budget_app import i18n
from budget_app.config import COICOP_FILE, RESOURCES_DIR
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
