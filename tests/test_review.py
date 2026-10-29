"""Year in review (F19): the period and its arithmetic, the years offered, a year's figures against the year before
(split lines, advances and repayments, the biggest payments, wealth), the current year so far, another view
currency, and the Excel and PDF exports."""

import io
from datetime import date

import pandas as pd
import pypdfium2 as pdfium
import pytest
from PIL import Image

from budget_app.domain import review as rules
from budget_app.domain.coicop import ADV
from budget_app.domain.review import Period
from budget_app.services import items as items_svc
from budget_app.services import review as review_svc
from budget_app.services import review_export
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.aggregates import ADV_REPAY
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput

TODAY = date(2026, 10, 18)
D = date


def spend(ctx, setup, day: date, amount: float, code: str = "01.1", **kw):
    base = dict(
        date=day, type="expenditure", amount=amount, currency="CHF", category=code, from_account=setup["chf"].id
    )
    return tx_svc.save(ctx, TransactionInput(**(base | kw)))


def earn(ctx, setup, day: date, amount: float, **kw):
    base = dict(
        date=day, type="income", amount=amount, currency="CHF", category=setup["salary"].id, to_account=setup["chf"].id
    )
    return tx_svc.save(ctx, TransactionInput(**(base | kw)))


@pytest.fixture
def year_2025(ctx, setup):
    """2025 against 2024: salaries, rent, groceries with a split receipt, an advance and its repayment, balances."""
    for month in (3, 9):
        earn(ctx, setup, D(2024, month, 25), 5000)
        earn(ctx, setup, D(2025, month, 25), 6000)
        spend(ctx, setup, D(2024, month, 1), 1800, "04.1", payee="Immo Muster AG")
        spend(ctx, setup, D(2025, month, 1), 1900, "04.1", payee="Immo Muster AG")
    spend(ctx, setup, D(2024, 5, 10), 300, "01.1", payee="Migros")
    receipt = spend(ctx, setup, D(2025, 5, 10), 500, "01.1", payee="Migros")
    items_svc.replace(ctx, receipt.id, [ItemInput("Food", 1, 380), ItemInput("Detergent", 1, 120, "05.6")])
    # a dinner, half of it for a friend who pays back in July
    dinner = spend(
        ctx, setup, D(2025, 6, 6), 240, "11.1", payee="Hirschen", is_advance=True, advance_value=50, debtor="Clara"
    )
    earn(ctx, setup, D(2025, 7, 1), 120, is_advance=True, settles_id=dinner.id, advance_account=dinner.advance_account)
    spend(ctx, setup, D(2025, 8, 20), 2400, "05.3", payee="Fust", comment="Washing machine")
    spend(ctx, setup, D(2025, 12, 31), 60, "09.4")
    spend(ctx, setup, D(2026, 1, 1), 999, "09.4")  # next year
    for month, amount in (("2023-12", 9000), ("2024-12", 10000), ("2025-12", 14000)):
        wealth_svc.upsert_entry(ctx, setup["chf"].id, month, amount)
    return setup


# ------------------------------------------------------------------ domain
def test_a_past_year_is_january_to_december():
    p = rules.period(2025, TODAY)
    assert p == Period(2025, "2025-01", "2025-12", True)
    assert p.count == 12 and p.months[0] == "2025-01" and p.months[-1] == "2025-12"
    assert p.before == "2024-12"
    assert p.previous() == Period(2024, "2024-01", "2024-12", True)


def test_the_current_year_runs_to_the_current_month():
    p = rules.period(2026, TODAY)
    assert p == Period(2026, "2026-01", "2026-10", False)
    assert p.count == 10
    assert p.previous() == Period(2025, "2025-01", "2025-10", True)  # the same months a year earlier


def test_a_later_year_has_no_period():
    with pytest.raises(ValueError, match="2027"):
        rules.period(2027, TODAY)


@pytest.mark.parametrize(
    ("income", "expenditure", "rate"),
    [(0, 500, None), (0.004, 0, None), (1000, 750, 0.25), (1000, 1200, -0.2), (1000, 0, 1.0)],
)
def test_savings_rate(income, expenditure, rate):
    assert rules.savings_rate(income, expenditure) == pytest.approx(rate) if rate is not None else rate is None


def test_share():
    assert rules.share(25, 100) == 0.25
    assert rules.share(25, 0) == 0.0
    assert rules.share(25, -100) == 0.0


# ------------------------------------------------------------------ the years
def test_years_offered_and_the_default(ctx, setup):
    assert review_svc.years(ctx, TODAY) == [2026]
    assert review_svc.default_year(ctx, TODAY) == 2026  # last year has nothing, so this one
    spend(ctx, setup, D(2024, 3, 1), 10)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2025-06", 100)
    spend(ctx, setup, D(2027, 1, 1), 10)  # dated in the future: not a year to review yet
    assert review_svc.years(ctx, TODAY) == [2026, 2025, 2024]
    assert review_svc.default_year(ctx, TODAY) == 2025


# ------------------------------------------------------------------ a complete year
def test_totals_and_the_change_against_the_year_before(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert rev.period.complete and rev.currency == "CHF"
    assert rev.income.amount == pytest.approx(12120) and rev.income.previous == pytest.approx(10000)
    # rent 3'800 + receipt 500 + dinner 240 (120 of it advanced) + 2'400 + 60
    assert rev.expenditure.amount == pytest.approx(7000) and rev.expenditure.previous == pytest.approx(3900)
    assert rev.saved.amount == pytest.approx(5120) and rev.saved.previous == pytest.approx(6100)
    assert rev.savings_rate == pytest.approx(5120 / 12120)
    assert rev.previous_savings_rate == pytest.approx(0.61)
    assert rev.income.change.ratio == pytest.approx(0.212)
    assert rev.has_data


def test_spending_lines_are_the_top_level_rows_in_coicop_order(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    lines = {line.key: line for line in rev.expenditure_lines}
    assert list(lines) == ["01", "04", "05", "09", "11", ADV]
    assert lines["01"].amount == pytest.approx(380) and lines["01"].previous == pytest.approx(300)  # the split line
    assert lines["05"].amount == pytest.approx(2520) and lines["05"].previous == 0  # 120 detergent + 2'400
    assert lines["05"].change.ratio is None  # new
    assert lines["11"].amount == pytest.approx(120) and lines[ADV].amount == pytest.approx(120)
    assert lines["04"].label.startswith("04 ") and lines[ADV].label == "Advance payments"
    assert lines["04"].change.ratio == pytest.approx(3800 / 3600 - 1)


def test_income_lines_name_the_categories_and_the_repayments(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert [(line.label, line.amount, line.previous) for line in rev.income_lines] == [
        ("Salary", 12000, 10000),
        ("Advance repayments", 120, 0),
    ]
    assert rev.income_lines[1].key == ADV_REPAY


def test_the_biggest_payments_count_the_own_share(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert [(b.name, b.amount, b.category) for b in rev.biggest] == [
        ("Fust", 2400, "05.3"),
        ("Immo Muster AG", 1900, "04.1"),
        ("Immo Muster AG", 1900, "04.1"),
        ("Migros", 500, "01.1"),  # the whole receipt, whatever its lines' categories
        ("Hirschen", 120, "11.1"),  # the half paid for Clara is not the user's spending
    ]
    assert rev.biggest[1].date == D(2025, 3, 1) and rev.biggest[2].date == D(2025, 9, 1)  # the earlier first on a tie
    assert rev.biggest[0].transaction_id in {tx.id for tx in ctx.store.transactions.all()}


def test_months_wealth_and_the_previous_change(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert [m.month for m in rev.monthly] == [f"2025-{n:02d}" for n in range(1, 13)]
    by_month = {m.month: m for m in rev.monthly}
    assert by_month["2025-03"].income == 6000 and by_month["2025-03"].expenditure == 1900
    assert by_month["2025-03"].saved == 4100
    assert by_month["2025-07"].income == 120 and by_month["2025-02"].expenditure == 0
    assert rev.wealth_start == 10000 and rev.wealth_end == 14000 and rev.wealth_month == "2025-12"
    assert rev.wealth_change == 4000 and rev.previous_wealth_change == 1000


def test_a_year_without_balances_before_it(ctx, setup):
    spend(ctx, setup, D(2025, 2, 2), 50)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2025-12", 500)
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert rev.wealth_start is None and rev.wealth_end == 500
    assert rev.wealth_change is None and rev.previous_wealth_change is None


def test_an_empty_year_has_no_data(ctx, setup):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert not rev.has_data
    assert rev.expenditure_lines == [] and rev.income_lines == [] and rev.biggest == []
    assert len(rev.monthly) == 12 and all(m.income == 0 and m.expenditure == 0 for m in rev.monthly)


# ------------------------------------------------------------------ the current year
def test_the_current_year_so_far_against_the_same_months(ctx, setup):
    spend(ctx, setup, D(2026, 3, 1), 100)
    spend(ctx, setup, D(2026, 10, 17), 200)
    spend(ctx, setup, D(2025, 2, 1), 400)
    spend(ctx, setup, D(2025, 11, 1), 1000)  # November: not among the months compared
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2025-12", 1000)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 1250)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-10", 9999)  # this month isn't over
    rev = review_svc.review(ctx, 2026, "CHF", TODAY)
    assert not rev.period.complete and rev.period.last == "2026-10"
    assert rev.expenditure.amount == 300 and rev.expenditure.previous == 400
    assert len(rev.monthly) == 10
    assert rev.wealth_month == "2026-09" and rev.wealth_end == 1250 and rev.wealth_change == 250


def test_in_january_no_month_of_the_year_is_over(ctx, setup):
    spend(ctx, setup, D(2026, 1, 5), 100)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2025-12", 1000)
    rev = review_svc.review(ctx, 2026, "CHF", D(2026, 1, 10))
    assert rev.period.months == ["2026-01"]
    assert rev.wealth_start == 1000 and rev.wealth_end is None and rev.wealth_month == ""
    assert rev.has_data


def test_another_view_currency_uses_each_transactions_rate(ctx, setup):
    spend(ctx, setup, D(2026, 10, 16), 90)  # 0.90 CHF per EUR that day
    spend(ctx, setup, D(2025, 10, 16), 95)  # before the first rate: the earliest one, 0.95
    rev = review_svc.review(ctx, 2026, "EUR", TODAY)
    assert rev.currency == "EUR"
    assert rev.expenditure.amount == pytest.approx(100) and rev.expenditure.previous == pytest.approx(100)
    assert rev.biggest[0].amount == pytest.approx(100)


# ------------------------------------------------------------------ exports
def test_labels_and_texts(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    assert review_export.period_label(rev) == "2025"
    assert review_export.comparison_label(rev) == "compared with 2024"
    now = review_svc.review(ctx, 2026, "CHF", TODAY)
    assert review_export.period_label(now) == "2026 so far (January to October)"
    assert review_export.comparison_label(now) == "compared with the same months of 2025"
    assert review_export.percent(None) == "–"
    assert review_export.percent(0.234) == "23 %"
    assert review_export.percent(-0.1) == "−10 %"
    assert review_export.file_name(2025, "pdf") == "Year in review 2025.pdf"


def test_frames_and_excel(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    frames = review_export.frames(rev)
    assert list(frames) == ["Summary", "Months", "Expenditure", "Income", "Biggest Expenses"]
    summary = frames["Summary"].set_index("Item")
    assert list(summary.columns) == ["2025", "2024", "Change"]
    assert summary.loc["Income", "2025"] == 12120 and summary.loc["Income", "Change"] == "+21 %"
    assert summary.loc["Saved", "Change"] == "−16 %"
    assert summary.loc["Savings rate", "2025"] == "42 %" and summary.loc["Savings rate", "2024"] == "61 %"
    assert summary.loc["Wealth at the start", "2025"] == 10000
    assert summary.loc["Wealth at the end (Dec 2025)", "2025"] == 14000
    assert summary.loc["Wealth change", "2025"] == 4000 and summary.loc["Wealth change", "2024"] == 1000
    exp = frames["Expenditure"].set_index("Category")
    assert exp.loc["05 Household and furniture", "Change"] == "new"
    assert exp.loc["01 Food and drinks", "Change"] == "+27 %"
    assert list(frames["Biggest Expenses"].columns) == ["Date", "Payee", "Category", "Amount (CHF)"]
    assert frames["Biggest Expenses"].iloc[0].tolist() == [
        "2025-08-20",
        "Fust",
        "05.3 Household appliances",
        2400,
    ]
    assert frames["Months"].iloc[2].tolist() == ["2025-03", 6000, 1900, 4100]
    sheets = pd.read_excel(io.BytesIO(review_export.xlsx(rev)), sheet_name=None)
    assert list(sheets) == ["Summary", "Months", "Expenditure", "Income", "Biggest Expenses"]
    assert sheets["Income"]["Category"].tolist() == ["Salary", "Advance repayments"]


def test_change_texts():
    from budget_app.domain.trends import Change  # noqa: PLC0415

    assert review_export.change_text(Change(0, 0)) == ""
    assert review_export.change_text(Change(10, 0)) == "new"
    assert review_export.change_text(Change(100, 100)) == "0 %"
    assert review_export.change_text(Change(90, 100)) == "−10 %"
    assert review_export.change_text(Change(0, 100)) == "−100 %"


def pdf_text(data: bytes) -> list[str]:
    doc = pdfium.PdfDocument(data)
    return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 12), (10, 132, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_pdf_with_and_without_the_chart(ctx, year_2025):
    rev = review_svc.review(ctx, 2025, "CHF", TODAY)
    plain = review_export.pdf(rev, today=D(2026, 1, 4))
    text = "\n".join(pdf_text(plain))
    assert "Year in review 2025" in text and "compared with 2024" in text and "made on 4 Jan 2026" in text
    assert "Amounts in CHF" in text
    assert "Spending by Category" in text and "Biggest Expenses" in text and "Month by Month" in text
    assert "12'120.00" in text and "Washing machine" not in text and "Fust" in text
    assert "Savings rate" in text and "42 %" in text and "Page 1" in text
    assert "Income and Expenditure" not in text
    with_chart = review_export.pdf(rev, png(), today=D(2026, 1, 4))
    assert "Income and Expenditure" in "\n".join(pdf_text(with_chart))
    assert len(with_chart) > len(plain)


def test_pdf_of_an_empty_year_says_so(ctx, setup):
    rev = review_svc.review(ctx, 2026, "CHF", TODAY)
    text = "\n".join(pdf_text(review_export.pdf(rev)))
    assert "2026 so far (January to October)" in text and "Nothing recorded" in text
