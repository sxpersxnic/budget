from budget_app.domain.coicop import ADV, TAX, get_coicop
from budget_app.services.reports import rollup


def test_coicop_has_four_levels():
    c = get_coicop()
    assert len(c.divisions) == 15
    levels = [cat.level for cat in c.categories.values() if cat.code not in (TAX, ADV)]
    assert {lvl: levels.count(lvl) for lvl in set(levels)} == {1: 15, 2: 63, 3: 186, 4: 338}
    assert c.ancestors("01.1.1.3") == ["01.1.1.3", "01.1.1", "01.1", "01"]
    assert TAX in c.selectable() and ADV not in c.selectable()


def test_rollup_sums_lower_levels_into_parents():
    totals = rollup({"01": 5.0, "01.1": 10.0, "01.1.1.3": 20.0, "01.2": 1.0, TAX: 7.0})
    assert totals["01"] == 36.0
    assert totals["01.1"] == 30.0
    assert totals["01.1.1"] == 20.0
    assert totals[TAX] == 7.0


def test_one_label_everywhere_with_the_official_name_beside_it():
    c = get_coicop()
    food = c.get("01.1")
    assert (food.label, food.display_name) == ("01.1 Groceries", "Groceries")
    assert (food.official_label, food.official_name) == ("01.1 Food", "Food")
    assert c.label("11.1") == "11.1 Eating out" and c.label("NOPE") == "NOPE"
    assert c.label("01.1.1.1") == "01.1.1.1 Cereals"  # no everyday name: the official one
    assert c.tooltip("01.1") == "COICOP 01.1 Food"
    assert c.tooltip("01.1.1.1") == ""  # the label already is the official name
    assert c.tooltip("06") == ""  # the everyday name is the official one: Health
    assert c.tooltip(TAX) == c.tooltip(ADV) == c.tooltip("NOPE") == ""
    assert c.label(TAX) == c.get(TAX).official_label == "Taxes" and c.label(ADV) == "Advance payments"
