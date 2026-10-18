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
