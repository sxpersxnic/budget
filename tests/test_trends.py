"""Trends and unusual spending (F18): changes against another month, and the rules of *Notable this month*."""

from datetime import date

import pytest

from budget_app.domain import trends as rules
from budget_app.domain.coicop import ADV, TAX
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services import items as items_svc
from budget_app.services import reports, trends
from budget_app.services import transactions as tx_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput

TODAY = date(2026, 10, 18)  # history: April to September 2026
HISTORY = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]


def spend(ctx, setup, day: date, amount: float, code: str = "01.1", **kw):
    inp = TransactionInput(
        date=day, type=TX_EXPENDITURE, amount=amount, currency="CHF", category=code, from_account=setup["chf"].id
    )
    for key, value in kw.items():
        setattr(inp, key, value)
    return tx_svc.save(ctx, inp)


def budget(ctx, code: str, amount: float, month: str = "2026-10", currency: str = "CHF") -> None:
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month=month, end_month=month, type="expenditure", currency=currency, amount=amount, category=code
        ),
    )


def usual_months(ctx, setup, months: list[str] = HISTORY) -> None:
    """Rent once and groceries four times a month: 1'900 + 4 × 100 = 2'300 CHF a usual month."""
    for m in months:
        y, mo = map(int, m.split("-"))
        spend(ctx, setup, date(y, mo, 1), 1900, "04.1")
        for d in (3, 10, 17, 24):
            spend(ctx, setup, date(y, mo, d), 100, "01.1.1")


def kinds(result: trends.Notable) -> list[tuple[str, str]]:
    return [(i.kind, i.category) for i in result.items]


# ------------------------------------------------------------------ the rules (domain)
def test_a_change_has_a_difference_and_a_ratio_unless_there_is_nothing_to_compare_with():
    c = rules.Change(120, 100)
    assert (c.delta, c.ratio, c.empty) == (20, pytest.approx(0.2), False)
    assert rules.Change(50, 0).ratio is None
    assert rules.Change(0, -40).ratio == pytest.approx(1.0)  # a refund last month: compared with its size
    assert rules.Change(0, 0.001).empty


def test_percent_rounds_and_caps():
    assert rules.percent(0.123) == "12 %"
    assert rules.percent(-0.5) == "50 %"
    assert rules.percent(23.5) == ">999 %"


def test_above_usual_needs_both_the_ratio_and_the_amount():
    assert rules.median([]) == 0
    assert rules.median([3, 1, 2, 10]) == 2.5
    assert rules.above_usual(400, 200, 100)
    assert not rules.above_usual(290, 200, 100)  # not 50 % above
    assert not rules.above_usual(60, 20, 100)  # tripled, but only 40 more: a small category
    assert rules.above_usual(150, 0, 100)  # nothing usually


def test_a_large_payment_is_judged_by_its_category_when_it_has_a_history():
    assert not rules.is_large(240, 400, [], 250)  # below the minimum
    assert rules.is_large(300, 1000, [], 250)  # a quarter of a usual month, in a category without history
    assert not rules.is_large(300, 1300, [10, 12], 250)  # too few payments to judge the category
    assert not rules.is_large(300, 0, [], 250)  # no usual month yet
    assert not rules.is_large(1900, 2300, [1900, 1900, 1900], 250)  # the rent is big but usual
    assert rules.is_large(400, 9999, [90, 100, 110], 250)  # four times the usual grocery bill


def test_budget_rules_exceeded_and_pace():
    assert rules.exceeded(112, 100, 10)
    assert not rules.exceeded(105, 100, 10)
    assert not rules.exceeded(50, 0, 10)
    assert rules.pace(100, 10, 31) == pytest.approx(310)
    assert rules.pace(5, 0, 30) == 150  # day 0 counts as the first
    assert rules.about_to_exceed(300, 600, 10, 31, 3, 10) == pytest.approx(930)
    assert rules.about_to_exceed(300, 600, 6, 31, 3, 10) is None  # too early in the month
    assert rules.about_to_exceed(300, 600, 10, 31, 2, 10) is None  # too few payments to show a pace
    assert rules.about_to_exceed(600, 600, 10, 31, 3, 10) is None  # used up, not about to exceed
    assert rules.about_to_exceed(195, 600, 10, 31, 3, 10) is None  # 604.50: less than 10 over
    assert rules.about_to_exceed(10, 0, 10, 31, 3, 10) is None  # no budget


# ------------------------------------------------------------------ the Budget page's comparison
def test_reference_months():
    assert trends.reference_month("2026-01", trends.COMPARE_MONTH) == "2025-12"
    assert trends.reference_month("2026-01", trends.COMPARE_YEAR) == "2025-01"
    assert trends.reference_month("2026-01", trends.COMPARE_BUDGET) is None


def test_compare_with_the_month_before_counts_parts_and_adds_lines_of_that_month_only(ctx, setup):
    receipt = spend(ctx, setup, date(2026, 9, 5), 100, "01.1")  # 30 of it is a present (F11)
    items_svc.replace(ctx, receipt.id, [ItemInput("Food", 1, 70), ItemInput("Game", 1, 30, "09.2.1")])
    spend(ctx, setup, date(2026, 9, 6), 50, "06.1")  # only in September
    spend(ctx, setup, date(2026, 10, 5), 80, "01.1")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=6000,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    rep = reports.month_report(ctx, "2026-10", "CHF", TODAY)
    assert trends.compare(ctx, rep, trends.COMPARE_BUDGET) is None
    cmp = trends.compare(ctx, rep, trends.COMPARE_MONTH)
    assert cmp is not None and cmp.month == "2026-09"
    assert cmp.expenditure["01.1"] == pytest.approx(70)  # the present counts in 09.2.1
    assert cmp.expenditure["09"] == pytest.approx(30)
    assert cmp.total_expenditure == pytest.approx(150)
    assert cmp.total_income == pytest.approx(6000)
    food = rep.expenditure_tree["01.1"]
    assert cmp.change(food).ratio == pytest.approx(80 / 70 - 1)
    health = rep.expenditure_tree["06.1"]  # a line of its own, empty this month
    assert (health.actual, health.budget, cmp.change(health).ratio) == (0, 0, pytest.approx(-1))
    assert [line.key for line in rep.income_lines] == [setup["salary"].id]
    assert cmp.change(rep.income_lines[0], income=True).reference == 6000
    assert cmp.change(rep.total_expenditure).reference == pytest.approx(150)
    assert cmp.change(rep.total_income, income=True).reference == pytest.approx(6000)


def test_compare_in_another_currency_and_with_the_year_before(ctx, setup):
    spend(ctx, setup, date(2025, 10, 5), 90, "01.1")
    spend(ctx, setup, date(2026, 10, 5), 90, "01.1")
    rep = reports.month_report(ctx, "2026-10", "EUR", TODAY)
    cmp = trends.compare(ctx, rep, trends.COMPARE_YEAR)
    assert cmp is not None and cmp.month == "2025-10"
    assert cmp.expenditure["01.1"] == pytest.approx(rep.expenditure_tree["01.1"].actual * 0.94 / 0.95)


# ------------------------------------------------------------------ notable this month
def test_nothing_recorded_means_nothing_notable(ctx, setup):
    result = trends.notable(ctx, "CHF", TODAY)
    assert (result.items, result.has_data, result.month) == ([], False, "2026-10")


def test_a_short_history_still_shows_budgets_but_no_comparisons(ctx, setup):
    usual_months(ctx, setup, ["2026-08", "2026-09"])
    spend(ctx, setup, date(2026, 10, 2), 3000, "07.1")  # would be large and above usual with a history
    budget(ctx, "07.1", 2000)
    result = trends.notable(ctx, "CHF", TODAY)
    assert (result.history_months, result.short_history) == (2, True)
    assert kinds(result) == [(trends.OVER_BUDGET, "07.1")]


def test_a_quiet_month_has_nothing_notable(ctx, setup):
    usual_months(ctx, setup, [*HISTORY, "2026-10"])
    spend(ctx, setup, date(2025, 1, 5), 5000, "07.1")  # long before the six months: no part of the usual level
    result = trends.notable(ctx, "CHF", TODAY)
    assert (result.items, result.history_months, result.short_history) == ([], 6, False)


def test_a_category_well_above_its_usual_level(ctx, setup):
    usual_months(ctx, setup)
    for m in HISTORY:
        y, mo = map(int, m.split("-"))
        spend(ctx, setup, date(y, mo, 12), 120, "11.1")
        spend(ctx, setup, date(y, mo, 13), 20, "09.4")
    for d in (2, 5, 8, 11):
        spend(ctx, setup, date(2026, 10, d), 90, "11.1")  # 360 against a usual 120
        spend(ctx, setup, date(2026, 10, d), 15, "09.4")  # 60 against 20: tripled, but small
    result = trends.notable(ctx, "CHF", TODAY)
    assert kinds(result) == [(trends.ABOVE_USUAL, "11.1")]
    item = result.items[0]
    assert (item.amount, item.reference) == (pytest.approx(360), pytest.approx(120))


def test_spending_the_budget_covers_is_planned_not_notable(ctx, setup):
    usual_months(ctx, setup)
    for d in (2, 5, 8):
        spend(ctx, setup, date(2026, 10, d), 200, "07.3")
    budget(ctx, "07.3", 1100)  # and the pace (1'033.33 by the end of the month) stays within it
    assert trends.notable(ctx, "CHF", TODAY).items == []


def test_a_large_payment_and_the_rent_that_is_not(ctx, setup):
    usual_months(ctx, setup)
    spend(ctx, setup, date(2026, 10, 1), 1900, "04.1", payee="Immo AG")
    spend(ctx, setup, date(2026, 10, 8), 1290, "05.3.1.2", payee="Fust")
    spend(ctx, setup, date(2026, 10, 9), 450, "01.1.1", comment="Party")  # 4.5 times the usual groceries
    result = trends.notable(ctx, "CHF", TODAY)
    # one payment explains 05.3 being above its usual level, so it shows once, as the payment
    assert kinds(result) == [(trends.LARGE, "05.3"), (trends.LARGE, "01.1")]
    washer, party = result.items
    assert (washer.amount, washer.reference, washer.day, washer.name) == (1290, 2300, date(2026, 10, 8), "Fust")
    assert party.name == "Party"


def test_a_split_line_and_an_advance_count_as_their_parts(ctx, setup):
    usual_months(ctx, setup)
    # 1'600 at the supermarket: 1'400 of it is a television (F11), and half of everything was paid for a friend
    tx = spend(
        ctx,
        setup,
        date(2026, 10, 4),
        1600,
        "01.1.1",
        is_advance=True,
        advance_mode="amount",
        advance_value=800,
        debtor="Sam",
    )
    items_svc.replace(ctx, tx.id, [ItemInput("Food", 1, 200), ItemInput("TV", 1, 1400, "08.1.3")])
    result = trends.notable(ctx, "CHF", TODAY)
    assert kinds(result) == [(trends.LARGE, "08.1")]  # the food's 100 is a usual grocery bill
    assert result.items[0].amount == pytest.approx(700)  # the TV, less its half of the advance
    assert all(i.category != ADV for i in result.items)


def test_budgets_exceeded_and_on_pace_to_be(ctx, setup):
    usual_months(ctx, setup)
    for d in (2, 6, 9, 13):  # 4 × 100 = 400 groceries by the 18th: on pace for 688.89
        spend(ctx, setup, date(2026, 10, d), 100, "01.1.1")
    budget(ctx, "01.1", 600)
    spend(ctx, setup, date(2026, 10, 3), 260, TAX)
    budget(ctx, TAX, 200)
    spend(ctx, setup, date(2026, 10, 1), 1900, "04.1")  # paid once: no pace, and the budget isn't exceeded
    budget(ctx, "04.1", 1900)
    result = trends.notable(ctx, "CHF", TODAY)
    assert kinds(result) == [(trends.OVER_BUDGET, TAX), (trends.PACE, "01.1")]
    tax, food = result.items
    assert (tax.amount, tax.reference) == (260, 200)
    assert (food.amount, food.reference) == (400, 600)
    assert food.projected == pytest.approx(400 / 18 * 31)


def test_of_a_budget_and_one_inside_it_the_more_specific_is_listed(ctx, setup):
    usual_months(ctx, setup)
    spend(ctx, setup, date(2026, 10, 2), 300, "07.3.1")
    budget(ctx, "07", 150)  # 250 with the budget inside it: exceeded too
    budget(ctx, "07.3", 100)
    result = trends.notable(ctx, "CHF", TODAY)
    assert kinds(result) == [(trends.OVER_BUDGET, "07.3")]
    assert result.items[0].reference == 100


def test_the_list_is_ordered_and_short(ctx, setup):
    usual_months(ctx, setup)
    codes = ["03.1", "05.1", "07.1", "09.1", "12.1", "13.1"]
    for n, code in enumerate(codes):
        spend(ctx, setup, date(2026, 10, 2), 600 + 100 * n, code, payee=f"Shop {n}")
    budget(ctx, "13.1", 100)
    result = trends.notable(ctx, "CHF", TODAY)
    assert len(result.items) == rules.MAX_ITEMS
    assert result.items[0].kind == trends.OVER_BUDGET
    assert [i.amount for i in result.items[1:]] == [1000, 900, 800, 700]  # the biggest payments first


def test_thresholds_follow_the_view_currency(ctx, setup):
    usual_months(ctx, setup)
    spend(ctx, setup, date(2026, 10, 2), 98, "09.4")  # 104.26 EUR: above 100, but below 100 CHF (111.11 EUR)
    spend(ctx, setup, date(2026, 10, 2), 110, "11.1")  # 117.02 EUR
    result = trends.notable(ctx, "EUR", TODAY)
    assert kinds(result) == [(trends.ABOVE_USUAL, "11.1")]
    assert result.items[0].amount == pytest.approx(110 / 0.94)  # in EUR at the payment's own rate
    assert result.currency == "EUR"


def test_a_group_stands_for_its_codes():
    assert trends.group_of("01.1.1.1") == "01.1"
    assert trends.group_of("01.1") == "01.1"
    assert trends.group_of("01") == "01"
    assert trends.group_of(TAX) == TAX
