"""Everyday category names and search words (F08): the data file, the picker's options, search and recents."""

import json
from datetime import date

import pytest

from budget_app import i18n
from budget_app.domain import everyday as ev
from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput


@pytest.fixture
def german():
    i18n.set_language("de")
    yield
    i18n.set_language("en")


def codes(options) -> list[str]:
    return [o.code for o in options]


# ------------------------------------------------------------------ the data file
def test_every_entry_has_four_languages_and_search_words() -> None:
    raw = json.loads(ev.EVERYDAY_FILE.read_text(encoding="utf-8"))
    assert 60 <= len(raw) <= 90
    for code, entry in raw.items():
        assert set(entry) == {*i18n.LANGUAGES, "keywords"}, code
        for lang in i18n.LANGUAGES:
            assert isinstance(entry[lang], str) and entry[lang].strip() == entry[lang] and entry[lang], (code, lang)
        words = entry["keywords"]
        assert words and all(isinstance(w, str) and w.strip() == w and w for w in words), code
        assert len(set(words)) == len(words), f"{code}: a search word is listed twice"


def test_every_code_exists_in_coicop() -> None:
    coicop = get_coicop()
    for code in ev.everyday():
        assert coicop.get(code) is not None, code
        assert code != ADV


def test_names_are_unique_and_follow_the_house_style() -> None:
    entries = ev.everyday().values()
    for lang in i18n.LANGUAGES:
        names = [e.names[lang] for e in entries]
        assert len(set(names)) == len(names), f"two categories share an everyday name in {lang}"
    assert not [e.names["de"] for e in entries if "ß" in e.names["de"]]  # Swiss German


def test_taxes_keep_their_name() -> None:
    names = ev.everyday()[TAX].names
    for lang in i18n.LANGUAGES:
        catalog = {} if lang == "en" else json.loads((ev.RESOURCES_DIR / "i18n" / f"{lang}.json").read_text("utf-8"))
        assert names[lang] == catalog.get("Taxes", "Taxes")


def test_the_file_is_sorted_and_formatted_like_the_translations() -> None:
    text = ev.EVERYDAY_FILE.read_text(encoding="utf-8")
    raw = json.loads(text)
    assert list(raw) == sorted(raw)
    assert text == json.dumps(raw, ensure_ascii=False, indent=1) + "\n"


def test_a_missing_name_falls_back_to_english() -> None:
    assert ev.Everyday({"en": "Rent"}, ()).name("de") == "Rent"


# ------------------------------------------------------------------ options
def test_options_show_everyday_names_with_the_code() -> None:
    options = {o.code: o for o in ev.category_options()}
    assert len(options) == len(get_coicop().categories) - 1 and ADV not in options  # every COICOP code and Taxes
    groceries = options["01.1"]
    assert (groceries.label, groceries.caption, groceries.level, groceries.recent) == (
        "Groceries · 01.1",
        "Food",
        2,
        False,
    )
    assert options["08.3.9.2"].label == "Streaming subscriptions · 08.3.9.2"
    other = options["01.1.1"]
    assert (other.label, other.caption) == ("01.1.1 Cereals and cereal products", "")
    assert (options[TAX].label, options[TAX].caption) == ("Taxes", "")


def test_options_keep_the_coicop_order_with_taxes_last() -> None:
    order = codes(ev.category_options())
    assert order[:3] == ["01", "01.1", "01.1.1"]
    assert order[-1] == TAX
    assert order[:-1] == [c for c in get_coicop().categories if c not in (TAX, ADV)]


def test_recent_categories_come_first() -> None:
    options = ev.category_options(["04.1", "01.1", "04.1", "NOPE", TAX])
    assert codes(options[:3]) == ["04.1", "01.1", TAX]
    assert [o.recent for o in options[:4]] == [True, True, True, False]
    assert len(options) == len(ev.category_options())  # nothing twice, nothing lost
    assert codes(options).count("01.1") == 1


def test_german_names(german) -> None:
    options = {o.code: o for o in ev.category_options()}
    assert (options["01.1"].label, options["01.1"].caption) == ("Lebensmittel · 01.1", "Nahrungsmittel")
    assert options["12.1.2"].label == "Krankenkasse · 12.1.2"
    assert options["01.1.1"].label == "01.1.1 Getreide und Getreideerzeugnisse"
    assert options[TAX].label == "Steuern"
    assert "01.1" in codes(ev.search(options.values(), "groceries"))  # the English names still match


# ------------------------------------------------------------------ search
@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("migros", "01.1"),
        ("MIGROS", "01.1"),
        ("netflix", "08.3.9.2"),
        ("spotify", "08.3.9.2"),
        ("sbb", "07.3.1"),
        ("zvv", "07.3"),
        ("krankenkasse", "12.1.2"),
        ("helsana", "12.1.2"),
        ("swisscom", "08.3"),
        ("tankstelle", "07.2.2"),
        ("apotheke", "06.1.1"),
        ("arzt", "06.2"),
        ("kita", "13.3.0.1"),
        ("miete", "04.1"),
        ("nebenkosten", "04.4"),
        ("serafe", "08.3.9.1"),
        ("ikea", "05.1"),
        ("zalando", "03.1"),
        ("galaxus", "08.1.3"),
        ("steuern", TAX),
        ("cafe", "11.1.1"),  # accents do not matter: "café"
        ("Getränke", "01.2"),  # nor do umlauts
        ("public transport", "07.3"),  # every word must match
        ("cereals", "01.1.1"),  # official names
        ("07.3.1", "07.3.1"),  # codes
    ],
)
def test_search_finds(query: str, code: str) -> None:
    assert code in codes(ev.search(ev.category_options(), query))


def test_search_keeps_the_order_and_needs_every_word() -> None:
    options = ev.category_options(["11.1.1"])
    found = codes(ev.search(options, "migros"))
    assert found[0] == "11.1.1" and "01.1" in found  # a recent match first
    assert codes(ev.search(options, "netflix")) == ["08.3.9.2"]
    assert codes(ev.search(options, "train sbb")) == ["07.3.1"]
    assert ev.search(options, "migros netflix") == []
    assert ev.search(options, "") == options


# ------------------------------------------------------------------ recently used
def expense(ctx, setup, day: date, category: str, kind: str = "expenditure") -> None:
    account = {"from_account": setup["chf"].id} if kind == "expenditure" else {"to_account": setup["chf"].id}
    tx_svc.save(ctx, TransactionInput(date=day, type=kind, amount=10, currency="CHF", category=category, **account))


def test_recent_categories_are_the_latest_distinct_expenditures(ctx, setup) -> None:
    assert tx_svc.recent_categories(ctx) == []
    expense(ctx, setup, date(2026, 10, 1), "01.1")
    expense(ctx, setup, date(2026, 10, 3), "04.1")
    expense(ctx, setup, date(2026, 10, 2), "11.1.1")
    expense(ctx, setup, date(2026, 10, 3), "07.3.1")  # same day as the rent, entered later
    expense(ctx, setup, date(2026, 9, 1), "04.1")
    expense(ctx, setup, date(2026, 10, 9), setup["salary"].id, kind="income")
    assert tx_svc.recent_categories(ctx) == ["07.3.1", "04.1", "11.1.1", "01.1"]
    assert tx_svc.recent_categories(ctx, limit=2) == ["07.3.1", "04.1"]


def test_recent_categories_stop_at_ten(ctx, setup) -> None:
    used = [c for c in get_coicop().categories if c.count(".") == 1][:12]
    for day, code in enumerate(used, start=1):
        expense(ctx, setup, date(2026, 9, day), code)
    assert tx_svc.recent_categories(ctx) == used[::-1][:10]
