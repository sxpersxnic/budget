"""COICOP notes (F40): the UN's includes / also includes / excludes, our translations, lookup and search."""

import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

from budget_app import i18n
from budget_app.config import COICOP_FILE
from budget_app.domain import coicop
from budget_app.domain import everyday as ev
from budget_app.domain.coicop import NOTE_FIELDS, TAX, fold

TRANSLATED = [lang for lang in i18n.LANGUAGES if lang != i18n.DEFAULT]
# Codes for which the UN gives neither includes, also includes nor excludes: most divisions and groups (the
# publication describes them in an introduction only), some classes and subclasses, and all of divisions 14
# (non-profit institutions) and 15 (general government).
_EMPTY = (
    "01 01.1.1 01.1.2 01.1.3.5 01.1.3.6 01.1.4 01.1.4.2 01.1.5 01.1.6 01.1.6.6 01.1.7 01.1.8 01.1.9 01.3 02 02.1 02.2 "
    "02.3 02.3.0.2 02.4 03 03.1 03.2 04 04.2 04.3 04.4 04.5 05 05.1 05.2 05.3 05.4 05.5 05.6 06 07 07.2 07.3 07.4 08 "
    "08.1 08.2 08.3 09 09.1 09.2 09.3 09.4 09.5 09.6 09.7 09.8 10 10.1 10.2 10.3 10.4 10.5 11.1 11.2 12 12.1 12.2 13 "
    "13.1 13.2 13.3 13.9"
)
KNOWN_EMPTY = set(_EMPTY.split())


def app_codes() -> list[str]:
    with COICOP_FILE.open(encoding="utf-8") as f:
        return [r["code"] for r in csv.DictReader(f)]


def load(lang: str) -> dict:
    return json.loads(coicop.notes_file(lang).read_text(encoding="utf-8"))


def entries(raw: dict) -> dict[str, dict[str, str]]:
    return {code: entry for code, entry in raw.items() if not code.startswith("_")}


@pytest.fixture
def german():
    i18n.set_language("de")
    yield
    i18n.set_language("en")


# ------------------------------------------------------------------ the data files
def test_every_coicop_code_has_notes_from_the_un() -> None:
    raw = load("en")
    notes = entries(raw)
    assert list(notes) == app_codes()  # every code, nothing else, in COICOP (= sorted) order
    for code, entry in notes.items():
        assert set(entry) == set(NOTE_FIELDS), code
        assert all(isinstance(v, str) and v == v.strip() for v in entry.values()), code
    empty = {code for code, entry in notes.items() if not any(entry.values())}
    assert empty == KNOWN_EMPTY | {c for c in notes if c.startswith(("14", "15"))}
    assert len(notes) - len(empty) == 440
    source = raw["_source"]
    assert source["url"].startswith("https://unstats.un.org/") and source["retrieved"] == "2026-10-21"
    assert "COICOP" in source["publication"] and "Series M No. 99" in source["publication"]


def test_items_are_one_per_line_without_the_sources_bullets() -> None:
    for entry in entries(load("en")).values():
        for text in entry.values():
            for item in text.split("\n") if text else ():
                assert item and item == item.strip() and not item.startswith("*") and "_x000D_" not in item
                assert "  " not in item and "\xa0" not in item


@pytest.mark.parametrize("lang", ["en", *TRANSLATED])
def test_the_files_are_sorted_and_formatted_like_the_translations(lang: str) -> None:
    text = coicop.notes_file(lang).read_text(encoding="utf-8")
    raw = json.loads(text)
    keys = [k for k in raw if not k.startswith("_")]
    assert keys == sorted(keys)
    assert all(k.startswith("_") for k in list(raw)[: len(raw) - len(keys)])  # metadata first
    assert text == json.dumps(raw, ensure_ascii=False, indent=1) + "\n"


def test_the_german_translation_is_complete() -> None:
    raw = load("de")
    assert not raw.get("_untranslated") and raw["_translation"] == {"language": "de", "unofficial": True}
    english, german = entries(load("en")), entries(raw)
    assert list(german) == list(english)
    for code, entry in english.items():
        for name, text in entry.items():
            translated = german[code][name]
            assert bool(translated) == bool(text), (code, name)
            if not text:
                continue
            lines, ours = text.split("\n"), translated.split("\n")
            assert len(ours) == len(lines), (code, name)  # item by item
            for item, item_de in zip(lines, ours, strict=True):
                assert item_de.strip() == item_de and item_de, (code, name)
                codes = re.findall(r"\d+(?:\.\d+)+", item)
                assert re.findall(r"\d+(?:\.\d+)+", item_de) == codes, (code, item)  # references kept
            assert "ß" not in translated  # Swiss German


@pytest.mark.parametrize("lang", ["fr", "it"])
def test_french_and_italian_wait_for_their_translation(lang: str) -> None:
    """Until translated, the file is an English copy marked `_untranslated`; the app then shows English."""
    raw = load(lang)
    english = entries(load("en"))
    if raw.get("_untranslated"):
        assert entries(raw) == english
        assert not coicop.notes_translated(lang)
    else:  # translated: same codes, text wherever the UN has some
        assert list(entries(raw)) == list(english)
        for code, entry in english.items():
            for name, text in entry.items():
                assert bool(raw[code][name]) == bool(text), (code, name)
        assert coicop.notes_translated(lang)


def test_the_conversion_script_cleans_the_un_text() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "coicop_notes.py"
    spec = importlib.util.spec_from_file_location("coicop_notes_script", path)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.clean("* wheat_x000D_\n* rice,\xa0 including  parboiled rice_x000D_\n\n") == (
        "wheat\nrice, including parboiled rice"
    )
    assert script.clean(None) == ""
    assert list(script.ordered({"_m": 1}, {"02": {}, "01": {}})) == ["_m", "01", "02"]


# ------------------------------------------------------------------ lookup
def test_notes_of_a_category() -> None:
    cereals = coicop.notes("01.1.1.1", "en")
    assert cereals is not None and not cereals.translated
    assert cereals.includes[:3] == ("wheat", "rice, including parboiled rice", "sorghum")
    assert "bulgur" in cereals.also_includes
    assert cereals.excludes[0] == "flour of cereals (01.1.1.2)"
    assert cereals.as_dict()["includes"][0] == "wheat"
    assert coicop.notes("01", "en") is None  # the UN gives none
    assert coicop.notes(TAX, "en") is None and coicop.notes("99.9", "en") is None


def test_german_notes_are_marked_as_our_translation() -> None:
    cereals = coicop.notes("01.1.1.1", "de")
    assert cereals is not None and cereals.translated
    assert cereals.includes[:3] == ("Weizen", "Reis, einschliesslich Parboiled-Reis", "Sorghum")
    assert cereals.excludes[0] == "Mehl (01.1.1.2)"
    assert coicop.notes_translated("de") and not coicop.notes_translated("en")


def test_an_untranslated_or_missing_language_shows_english(tmp_path: Path, monkeypatch) -> None:
    untranslated = tmp_path / "coicop_notes_xx.json"
    untranslated.write_text(json.dumps({"_untranslated": True, "01.1.1.1": {"includes": "Weizen"}}), "utf-8")
    partial = tmp_path / "coicop_notes_yy.json"
    partial.write_text(json.dumps({"01.1.1.1": {"includes": "blé", "also_includes": "", "excludes": ""}}), "utf-8")
    real = coicop.notes_file
    monkeypatch.setattr(
        coicop, "notes_file", lambda lang: tmp_path / f"coicop_notes_{lang}.json" if lang != "en" else real(lang)
    )
    try:
        assert coicop.notes("01.1.1.1", "xx") == coicop.notes("01.1.1.1", "en")  # marked untranslated
        assert coicop.notes("01.1.1.1", "zz") == coicop.notes("01.1.1.1", "en")  # no file at all
        partly = coicop.notes("01.1.1.1", "yy")
        assert partly is not None and partly.translated and partly.includes == ("blé",)
        assert partly.excludes == coicop.notes("01.1.1.1", "en").excludes  # English where the text is missing
        rice = coicop.notes("01.1.1.4", "yy")  # a code the translation lacks: English, not marked
        assert rice is not None and not rice.translated
    finally:
        coicop._notes_data.cache_clear()
        coicop.notes.cache_clear()


def test_notes_for_the_browser() -> None:
    payload = coicop.notes_payload("de")
    assert payload["translated"] is True
    assert payload["notes"]["01.1.1.1"]["includes"][0] == "Weizen"
    assert "01" not in payload["notes"] and len(payload["notes"]) == 440
    assert coicop.notes_payload("fr")["translated"] is coicop.notes_translated("fr")
    assert re.fullmatch(r"[0-9a-f]{12}", coicop.notes_version("de"))
    assert coicop.notes_version("de") != coicop.notes_version("en")


# ------------------------------------------------------------------ search
def test_note_words_leave_out_excludes_codes_and_punctuation() -> None:
    words = coicop.note_words("01.1.1.1", "en").split()
    assert {"wheat", "teff", "bulgur", "job’s"} <= set(words)
    assert len(words) == len(set(words))  # each once
    assert "flour" not in words and "planting" not in words  # only in excludes
    assert not [w for w in words if not any(c.isalpha() for c in w) or len(w) < 2]
    german = coicop.note_words("01.1.1.1", "de").split()
    assert {"weizen", "hirse", "wheat"} <= set(german)  # the interface language and English
    assert coicop.note_words("01", "en") == "" and coicop.note_words(TAX, "de") == ""


@pytest.mark.parametrize(("query", "code"), [("teff", "01.1.1.1"), ("quinoa", "01.1.1.1"), ("funicular", "07.3.6")])
def test_a_word_only_in_the_notes_finds_the_category(query: str, code: str) -> None:
    options = ev.category_options()
    hit = next(o for o in options if o.code == code)
    assert query not in hit.search and query in hit.note_search
    assert code in [o.code for o in ev.search(options, query)]


def test_german_note_words_find_the_category(german) -> None:
    found = [o.code for o in ev.search(ev.category_options(), "hirse")]
    assert found == ["01.1.1.1"]
    assert [o.code for o in ev.search(ev.category_options(), "teff")] == ["01.1.1.1"]  # English notes still count


def test_matches_by_name_come_before_matches_by_notes() -> None:
    options = ev.category_options()
    found = [o.code for o in ev.search(options, "bread")]
    assert found[0] == "01.1.1.3"  # "Bread and bakery products"
    by_name = [o for o in options if all(w in o.search for w in fold("bread").split())]
    assert found[: len(by_name)] == [o.code for o in by_name]
    assert len(found) > len(by_name)  # more come from the notes, after
    first, rest = ev.search(options, "train sbb"), ev.search(options, "train")
    assert [o.code for o in first] == ["07.3.1"]
    assert ev.matches(rest[0], "train")


def test_a_word_only_in_excludes_does_not_find_the_category() -> None:
    options = {o.code: o for o in ev.category_options()}
    railway = options["07.3.1"]  # excludes "funicular transport (07.3.6.0)"
    assert "funicular" in " ".join(coicop.notes("07.3.1", "en").excludes)
    assert not ev.matches(railway, "funicular")
    assert "07.3.1" not in [o.code for o in ev.search(options.values(), "funicular")]
    # every category: a word that only its excludes mention never finds it
    checked = 0
    for code, option in options.items():
        note = coicop.notes(code, "en")
        if note is None:
            continue
        for word in {w for item in note.excludes for w in coicop._words(item)}:
            if len(word) > 3 and word not in option.search and word not in option.note_search:
                assert not ev.matches(option, word), (code, word)
                checked += 1
    assert checked > 500
