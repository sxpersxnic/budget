"""The Exchange Rates chart on the Overview (F38)."""

import io

import pandas as pd
import pytest
from nicegui import ui
from nicegui.testing import User
from test_overview_page import card, control, downloaded, menu_item, open_overview, set_value, traces, values


async def test_exchange_rates_chart_shows_the_account_currencies(user: User, ctx, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Exchange Rates")
    chart = card(user, "Exchange Rates")
    # the demo has a EUR account; the display currency is CHF
    assert control(user, chart, "Currencies").value == ["EUR"]
    [line] = traces(user, chart)
    assert line["name"] == "EUR"
    assert values(line["y"])[-1] == pytest.approx(0.90)  # the latest fixed rate: 1 EUR = 0.90 CHF


async def test_month_end_index_and_more_currencies(user: User, ctx, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Exchange Rates")
    chart = card(user, "Exchange Rates")
    await set_value(user, control(user, chart, "Currencies"), ["EUR", "USD", "CHF"])  # CHF is the view: skipped
    assert [t["name"] for t in traces(user, chart)] == ["EUR", "USD"]
    step = next(t for t in user.find(kind=ui.toggle).elements if "month" in t.options)
    await set_value(user, step, "month")
    assert len(values(traces(user, chart)[0]["y"])) == 3  # one value per month with ECB dates (Aug, Sep, Oct)

    index = next(s for s in user.find(kind=ui.switch).elements if "Index" in s.text)
    await set_value(user, index, True)
    for line in traces(user, chart):
        assert values(line["y"])[0] == pytest.approx(100)  # every line starts at 100

    await menu_item(user, "Export Data as CSV…", within=chart)
    df = pd.read_csv(io.BytesIO((await downloaded(user)).content))
    assert list(df.columns) == ["date", "EUR", "USD"]
