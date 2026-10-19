"""Interface language: English (source), German, French and Italian.

Texts are written in English in the code and wrapped in `t()`; `resources/i18n/<lang>.json` maps
each English text to that language (`coicop_<lang>.json` does the same for COICOP names). `tn()`
handles singular/plural. `N_()` only marks texts that are translated later (for example in
constant tables), so the completeness test can find them.

The language is process-wide: the app has one user, and changing it reloads the page.
"""

from __future__ import annotations

import contextlib
import json
import locale
import os
import subprocess  # nosec B404 - fixed command to read the macOS language
import sys
from datetime import date
from functools import cache

from budget_app.config import RESOURCES_DIR

LANGUAGES = {"en": "English", "de": "Deutsch", "fr": "Français", "it": "Italiano"}
DEFAULT = "en"
# Quasar language packs and browser locales; Swiss variants match the app's number format
QUASAR = {"en": "en-GB", "de": "de-CH", "fr": "fr", "it": "it"}
BROWSER_LOCALE = {"en": "en-GB", "de": "de-CH", "fr": "fr-CH", "it": "it-CH"}
MONTHS = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "de": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."],
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
    "it": ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"],
}
MONTHS_LONG = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "it": [
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ],
}

_current = DEFAULT


@cache
def catalog(lang: str) -> dict[str, str | list[str]]:
    """English text -> translation (a [singular, plural] pair for `tn` texts)."""
    path = RESOURCES_DIR / "i18n" / f"{lang}.json"
    if lang == DEFAULT or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def language() -> str:
    return _current


def set_language(lang: str) -> str:
    """Switch the language ('' = follow the system). Returns the language now in use."""
    global _current  # noqa: PLW0603 - one interface language per process
    _current = lang if lang in LANGUAGES else system_language()
    try:  # Quasar components (date picker) and the html lang attribute follow the app language
        from nicegui import app  # noqa: PLC0415 - optional at import time (tests run without a UI)

        app.config.language = QUASAR[_current]  # type: ignore[assignment]
    except (ImportError, AttributeError):
        pass
    return _current


def system_language() -> str:
    """The operating system's interface language if supported, else English."""
    for code in _system_codes():
        short = code.replace("-", "_").split("_")[0].lower()
        if short in LANGUAGES:
            return short
    return DEFAULT


def _system_codes() -> list[str]:
    codes = [os.environ.get(v, "") for v in ("LC_ALL", "LC_MESSAGES", "LANG")]
    if sys.platform == "darwin":  # apps started from the Finder have no LANG
        try:
            out = subprocess.run(
                ["/usr/bin/defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout
            codes += [c.strip(' "(),') for c in out.split() if c.strip(' "(),')]
        except (OSError, subprocess.SubprocessError):
            pass
    with contextlib.suppress(ValueError):
        codes.append(locale.getlocale()[0] or "")
    return [c for c in codes if c and c not in ("C", "POSIX", "C.UTF-8")]


def t(text: str, **values: object) -> str:
    """Translate `text`; `{name}` placeholders are filled from `values`."""
    found = catalog(_current).get(text, text)
    out = found if isinstance(found, str) else found[0]
    return out.format(**values) if values else out


def tc(context: str, text: str, **values: object) -> str:
    """Translate `text` in a context, for English words with several translations.

    The catalog key is "context|text", e.g. "heading|Income" -> "Einnahmen" while "Income" as a
    transaction type stays "Einnahme". Falls back to plain `t(text)`.
    """
    found = catalog(_current).get(f"{context}|{text}")
    if not isinstance(found, str):
        return t(text, **values)
    return found.format(**values) if values else found


def tn(singular: str, plural: str, n: int, **values: object) -> str:
    """Translate with the right plural form; `{n}` is available as a placeholder."""
    found = catalog(_current).get(singular)
    forms = found if isinstance(found, list) else [singular, plural]
    return forms[0 if n == 1 else 1].format(n=n, **values)


def N_(text: str) -> str:
    """Mark `text` for translation without translating it now."""
    return text


# ------------------------------------------------------------------ dates
def month_label(year: int, month: int, long: bool = False) -> str:
    names = (MONTHS_LONG if long else MONTHS)[_current]
    return f"{names[month - 1]} {year}"


def date_label(d: date) -> str:
    """9 Oct 2026 / 9. Okt. 2026 / 9 oct. 2026 / 9 ott 2026"""
    name = MONTHS[_current][d.month - 1]
    return f"{d.day}. {name} {d.year}" if _current == "de" else f"{d.day} {name} {d.year}"


def browser_locale() -> str:
    return BROWSER_LOCALE[_current]
