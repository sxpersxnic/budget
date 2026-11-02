"""The marks of well-known payees: found by name or by the brand a name holds, drawn readably on their colour."""

import json
import re

from budget_app.domain import logos
from budget_app.domain.payees import PAYEES_FILE


def test_every_mark_names_a_known_brand_and_is_one_plain_path() -> None:
    data = json.loads(logos.LOGOS_FILE.read_text(encoding="utf-8"))
    payees = json.loads(PAYEES_FILE.read_text(encoding="utf-8"))
    known = set(payees["brands"]) | set(payees["weak_brands"])
    assert len(data) == 35 and set(data) <= known
    for name, mark in data.items():
        assert re.fullmatch(r"[0-9A-F]{6}", mark["hex"]), name
        assert re.fullmatch(r"[0-9MmZzLlHhVvCcSsQqTtAaEe.,\s-]+", mark["path"]), name  # path data, nothing else


def test_brands_of_another_company_with_the_same_name_have_no_mark() -> None:
    for name in ("Coop", "Globus", "TCS", "Neon", "CSS"):
        assert logos.logo_for(name) is None, name


def test_a_payee_finds_its_mark_by_name_or_by_the_brand_in_its_name() -> None:
    assert logos.logo_for("IKEA").name == "IKEA"
    assert logos.logo_for("ikea") == logos.logo_for("IKEA")  # case and accents don't matter
    assert logos.logo_for("IKEA Spreitenbach").name == "IKEA"  # a renamed payee
    assert logos.logo_for("McDonald's").name == "McDonald's"
    assert logos.logo_for("Sandro M.") is None and logos.logo_for("") is None


def test_a_light_brand_colour_gets_a_dark_mark() -> None:
    shell, apple = logos.logo_for("Shell"), logos.logo_for("Apple")
    assert shell.light and not apple.light
    assert 'fill="#1d1d1f"' in shell.svg() and 'fill="#fff"' in apple.svg()
    assert apple.svg().startswith("<svg") and 'aria-label="Apple"' in apple.svg()
