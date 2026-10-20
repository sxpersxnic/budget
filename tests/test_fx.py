import io
from datetime import date

import pytest

from budget_app.domain.fx import FxError, FxRates

CSV = """Date,USD,CHF,CYP,
2026-10-16,1.25,0.90,N/A,
2026-10-15,1.24,0.91,N/A,
"""


def test_parse_ecb_csv_drops_empty_columns_and_adds_eur():
    fx = FxRates.from_csv(io.StringIO(CSV))
    assert fx.latest_date() == date(2026, 10, 16)
    assert fx.currencies() == ["CHF", "EUR", "USD"]


def test_cross_rate_conversion(rates):
    # 100 USD -> EUR -> CHF on 2026-10-16: 100 / 1.25 * 0.90 = 72
    assert rates.convert(100, "USD", "CHF", date(2026, 10, 16)) == pytest.approx(72.0)
    assert rates.convert(50, "CHF", "CHF") == 50


def test_rate_falls_back_to_previous_business_day(rates):
    assert rates.rate("CHF", date(2026, 10, 1)) == 0.94  # weekend/holiday -> 2026-09-30
    assert rates.rate_date_on_or_before(date(2026, 9, 30)) == date(2026, 9, 30)
    assert rates.rate("CHF", date(2000, 1, 1)) == 0.95  # before file start -> first rate


def test_unknown_currency(rates):
    with pytest.raises(FxError):
        rates.rate("XXX")


def test_history_daily_month_end_and_unknown():
    from datetime import date  # noqa: PLC0415

    import pandas as pd  # noqa: PLC0415

    from budget_app.domain.fx import FxRates  # noqa: PLC0415

    days = [date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30), date(2026, 10, 16)]
    rates = FxRates(
        pd.DataFrame({"CHF": [0.96, 0.95, 0.945, 0.94, 0.90], "USD": [1.1, 1.1, 1.2, 1.2, 1.25]}, index=days)
    )
    daily = rates.history("EUR", "CHF", date(2026, 8, 30), date(2026, 9, 30))
    assert [d for d, _ in daily] == [date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 30)]
    assert daily[0][1] == pytest.approx(0.95)
    month_end = rates.history("USD", "CHF", date(2026, 8, 1), date(2026, 10, 31), month_end=True)
    # the last ECB date of each month; October is not over, so its latest date counts
    assert [d for d, _ in month_end] == [date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 16)]
    assert month_end[1][1] == pytest.approx(0.94 / 1.2)
    assert rates.history("GBP", "CHF", date(2026, 8, 1), date(2026, 10, 31)) == []
    # a month without any ECB date in it is skipped instead of repeating the previous month
    gap = rates.history("EUR", "CHF", date(2026, 7, 1), date(2026, 8, 31), month_end=True)
    assert [d for d, _ in gap] == [date(2026, 8, 31)]
