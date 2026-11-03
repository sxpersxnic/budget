"""The marks of well-known payees (polish before 1.0): Apple, IKEA, Netflix are found at a glance by their logo.

`resources/logos.json` holds, for 35 brands of `resources/payees.json`, the brand colour and the one-path 24×24 mark
from Simple Icons 16.30.0 (https://simpleicons.org, CC0 1.0; the marks themselves are their owners' trademarks and
only name the payee here). Brands Simple Icons draws for another company of the same name are left out (the
British Co-op, the German Globus, Tata's TCS, the Neon database, the CSS language). Every other payee keeps its
initials (ui/widgets/sort_cards.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache

from budget_app.config import RESOURCES_DIR
from budget_app.domain.payees import brand, key

LOGOS_FILE = RESOURCES_DIR / "logos.json"


@dataclass(frozen=True)
class Logo:
    name: str  # the brand, as payees.json names it
    hex: str  # the brand colour, "0058A3"
    path: str  # the SVG path of the mark, in a 24×24 box

    @property
    def light(self) -> bool:
        """Whether the brand colour is light (Shell's yellow), so the mark on it is drawn dark, not white."""
        r, g, b = (int(self.hex[i : i + 2], 16) / 255 for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6

    def svg(self) -> str:
        """The mark on its brand colour, as an inline SVG for a round badge."""
        mark = "#1d1d1f" if self.light else "#fff"
        return (
            f'<svg viewBox="-6 -6 36 36" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{self.name}">'
            f'<rect x="-6" y="-6" width="36" height="36" fill="#{self.hex}"/>'
            f'<path d="{self.path}" fill="{mark}"/></svg>'
        )


@cache
def _logos() -> dict[str, Logo]:
    data = json.loads(LOGOS_FILE.read_text(encoding="utf-8"))
    return {key(name): Logo(name, v["hex"], v["path"]) for name, v in data.items()}


def logo_for(payee: str) -> Logo | None:
    """The mark of `payee` when it is a known brand: by its name ("IKEA"), or by the brand its name holds
    ("IKEA Spreitenbach", a payee the user renamed); None for every other payee."""
    logos = _logos()
    words = key(payee)
    if not words:
        return None
    return logos.get(words) or logos.get(key(brand(words.split())))
