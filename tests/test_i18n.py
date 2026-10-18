"""Every text marked for translation has a German translation with the same placeholders."""

import ast
import csv
import json
import string
from pathlib import Path

import pytest

from budget_app import i18n
from budget_app.config import COICOP_FILE, RESOURCES_DIR
from budget_app.domain import months
from budget_app.domain.coicop import get_coicop

SRC = Path(__file__).resolve().parents[1] / "src" / "budget_app"


def marked_texts() -> tuple[set[str], dict[str, str]]:
    """Texts in t()/N_() calls, and singular -> plural for tn() calls, across the sources."""
    single: set[str] = set()
    plural: dict[str, str] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if node.func.id in ("t", "N_") and args:
                single.add(args[0])
            elif node.func.id == "tc" and len(args) >= 2:
                single.add(f"{args[0]}|{args[1]}")
            elif node.func.id == "tn" and len(args) >= 2:
                plural[args[0]] = args[1]
    return single, plural


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.fixture
def german():
    yield json.loads((RESOURCES_DIR / "i18n" / "de.json").read_text(encoding="utf-8"))
    i18n.set_language("en")


def test_every_marked_text_is_translated(german):
    single, plural = marked_texts()
    missing = sorted((single | set(plural)) - set(german))
    assert not missing, f"{len(missing)} texts have no German translation: {missing[:20]}"
    for text in single:
        assert isinstance(german[text], str), text
        assert fields(german[text]) == fields(text), f"placeholders differ: {text!r}"
    for one, other in plural.items():
        forms = german[one]
        assert isinstance(forms, list) and len(forms) == 2, one
        assert fields(forms[0]) | fields(forms[1]) <= fields(one) | fields(other) | {"n"}, one


def test_no_unused_translations(german):
    single, plural = marked_texts()
    unused = sorted(set(german) - single - set(plural))
    assert not unused, f"translations nobody uses: {unused[:20]}"


def test_every_coicop_name_is_translated():
    names = json.loads((RESOURCES_DIR / "i18n" / "coicop_de.json").read_text(encoding="utf-8"))
    with COICOP_FILE.open(encoding="utf-8") as f:
        missing = {r["name"] for r in csv.DictReader(f)} - set(names)
    assert not missing


def test_switching_language(german):
    assert i18n.set_language("de") == "de"
    assert months.label("2026-03") == "März 2026"
    assert get_coicop().label("01.1") == "01.1 Nahrungsmittel"
    assert get_coicop().label("TAX") == german["Taxes"]
    assert i18n.tn("{n} transaction", "{n} transactions", 2) == german["{n} transaction"][1].format(n=2)
    i18n.set_language("en")
    assert months.label("2026-03") == "Mar 2026"
    assert i18n.t("Taxes") == "Taxes"


def test_unknown_language_falls_back_to_the_system_or_english(monkeypatch):
    monkeypatch.setattr(i18n, "_system_codes", lambda: ["fr_CH"])
    assert i18n.set_language("") == "en"
    monkeypatch.setattr(i18n, "_system_codes", lambda: ["de-CH", "en"])
    assert i18n.set_language("") == "de"
    i18n.set_language("en")
