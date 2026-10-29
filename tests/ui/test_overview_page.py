"""The overview page: wealth KPIs and the chart builder with its controls and downloads."""

from __future__ import annotations

import asyncio
import base64
import html
import io

import httpx
import numpy as np
import pytest
from nicegui import ui
from nicegui.element import Element
from nicegui.element_filter import ElementFilter
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from PIL import Image

from budget_app.domain import months
from budget_app.services import reports
from budget_app.services import wealth as wealth_svc
from budget_app.ui import layout
from budget_app.ui.layout import fmt

CM = months.current_month()
PREV = months.previous_month()


# ------------------------------------------------------------------ helpers
def find_all(user: User, kind: type[Element], within: Element | None = None, **props) -> list:
    """Visible elements of `kind` whose props match, in creation order."""
    with user:
        flt = ElementFilter(kind=kind, only_visible=True, local_scope=False)
        if within is not None:
            flt = flt.within(instance=within)
        return sorted((e for e in flt if all(e.props.get(k) == v for k, v in props.items())), key=lambda e: e.id)


async def settle() -> None:
    """Let refreshes and handlers scheduled by an interaction run."""
    for _ in range(3):
        await asyncio.sleep(0.01)


async def click(user: User, element: Element) -> None:
    UserInteraction(user, {element}, None).click()
    await settle()


async def set_value(user: User, element, value) -> None:
    with user:
        element.value = value
    await settle()


async def menu_item(user: User, text: str, within: Element | None = None) -> None:
    """Click a menu item; menus are closed in the simulation, so their items count as hidden."""
    with user:
        flt = ElementFilter(kind=ui.menu_item, local_scope=False)
        if within is not None:
            flt = flt.within(instance=within)
        items = sorted(
            (i for i in flt if any(getattr(e, "text", None) == text for e in i.descendants())), key=lambda i: i.id
        )
    assert items, f"no menu item {text!r}"
    await click(user, items[0])


async def downloaded(user: User, count: int = 1, timeout: float = 5.0) -> httpx.Response:
    """The `count`-th file the page handed to the user (it may have arrived while the click settled)."""
    for _ in range(int(timeout / 0.05)):
        if len(user.download.http_responses) >= count:
            return user.download.http_responses[count - 1]
        await asyncio.sleep(0.05)
    raise AssertionError("no download happened")


def grid(user: User) -> Element:
    return next(e for e in find_all(user, ui.element) if "grid" in e.classes)


def cards(user: User) -> list[Element]:
    """The chart cards in the order they are shown."""
    return list(grid(user).default_slot.children)


def card_title(user: User, card: Element) -> str:
    return next(lb.text for lb in find_all(user, ui.label, card) if "section-title" in lb.classes)


def card(user: User, title: str) -> Element:
    return next(c for c in cards(user) if card_title(user, c) == title)


def plot(user: User, card_el: Element) -> ui.plotly:
    return find_all(user, ui.plotly, card_el)[0]


def traces(user: User, card_el: Element) -> list[dict]:
    return plot(user, card_el).figure["data"]


def values(data) -> list:
    """Trace values as a list; plotly sends numeric arrays base64-encoded."""
    if isinstance(data, dict) and "bdata" in data:
        return np.frombuffer(base64.b64decode(data["bdata"]), dtype=data["dtype"]).tolist()
    return list(data)


def kpi_texts(user: User) -> list[str]:
    return [html.unescape(h.content) for h in find_all(user, ui.html)]


def control(user: User, card_el: Element, label: str):
    return find_all(user, ui.select, card_el, label=label)[0]


async def open_overview(user: User) -> None:
    await user.open("/")
    await user.should_see("Add Chart")


def png_data_url() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (24, 12), (10, 132, 255)).save(buf, format="PNG")  # the 2:1 shape the page asks for
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ KPIs
async def test_kpis_show_current_and_projected_wealth(user: User, demo, ctx) -> None:
    await open_overview(user)
    current = wealth_svc.current_wealth(ctx, "CHF")
    projected = wealth_svc.projected_wealth(ctx, ctx.horizon(), "CHF")
    await user.should_see(f"End of {months.label(PREV)}")
    await user.should_see(f"Projected for {months.label(ctx.horizon())}")
    assert projected > current
    await user.should_see(f"+{fmt(projected - current, 'CHF')} by the end of the plan")
    texts = kpi_texts(user)
    assert f'{fmt(current)}<span class="cur">CHF</span>' in texts
    assert f'{fmt(projected)}<span class="cur">CHF</span>' in texts


async def test_kpis_show_a_shrinking_plan(user: User, demo, ctx) -> None:
    for b in [b for b in ctx.store.budget.all() if b.type == "income" and b.month >= CM]:
        ctx.store.budget.delete([b.id])
    await open_overview(user)
    change = wealth_svc.projected_wealth(ctx, ctx.horizon(), "CHF") - wealth_svc.current_wealth(ctx, "CHF")
    assert change < 0
    await user.should_see(f"{fmt(change, 'CHF')} by the end of the plan")
    sub = next(lb for lb in find_all(user, ui.label) if "by the end of the plan" in lb.text)
    assert "text-neg" in sub.classes


async def test_wealth_error_is_reported(user: User, demo, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("no rates for XYZ")

    monkeypatch.setattr(wealth_svc, "projected_wealth", broken)
    await open_overview(user)
    await user.should_see("Cannot compute wealth: no rates for XYZ")
    await user.should_not_see("by the end of the plan")
    assert len(cards(user)) == 2  # the charts still work


async def test_saved_data_refreshes_kpis_and_charts(user: User, demo, ctx) -> None:
    await open_overview(user)
    wealth_svc.upsert_entry(ctx, demo["chf"].id, PREV, 10_000)
    with user:
        layout.data_changed()
    await settle()
    await user.should_see(
        f"+{fmt(wealth_svc.projected_wealth(ctx, ctx.horizon(), 'CHF') - wealth_svc.current_wealth(ctx, 'CHF'), 'CHF')}"
    )
    recorded = traces(user, card(user, "Wealth Over Time"))[0]
    assert recorded["x"][-1] == PREV
    assert recorded["y"][-1] == pytest.approx(wealth_svc.wealth(ctx, PREV, "CHF"))


# ------------------------------------------------------------------ the default charts
async def test_default_charts(user: User, demo, ctx) -> None:
    await open_overview(user)
    assert [card_title(user, c) for c in cards(user)] == ["Wealth Over Time", "Income and Expenditure"]
    wealth = traces(user, card(user, "Wealth Over Time"))
    assert [tr["name"] for tr in wealth] == ["Recorded", "Projected"]
    first = months.add_months(CM, -6)
    assert wealth[0]["x"][0] == first and wealth[0]["x"][-1] == PREV
    assert wealth[1]["x"][0] == PREV  # the projection continues from the last recorded month
    assert wealth[1]["x"][-1] == ctx.horizon()
    assert "fill" not in wealth[0] or wealth[0]["fill"] is None  # positive wealth: zoomed, not filled
    flows = traces(user, card(user, "Income and Expenditure"))
    assert [(tr["type"], tr["name"]) for tr in flows] == [("bar", "Total expenditure"), ("bar", "Total income")]
    assert values(flows[1]["y"]) == [6200] * 6 + [0]  # nothing booked yet this month
    assert values(flows[0]["y"])[0] == 2660


async def test_charts_without_any_data(user: User) -> None:
    await open_overview(user)
    (projected,) = traces(user, card(user, "Wealth Over Time"))
    assert projected["name"] == "Projected"
    assert set(values(projected["y"])) == {0}
    flows = traces(user, card(user, "Income and Expenditure"))
    assert all(set(values(tr["y"])) == {0} for tr in flows)


async def test_projection_only_when_no_balance_is_recorded(user: User, demo, ctx) -> None:
    ctx.store.account_entries.delete([e.id for e in ctx.store.account_entries.all()])
    await open_overview(user)
    wealth = traces(user, card(user, "Wealth Over Time"))
    assert [tr["name"] for tr in wealth] == ["Projected"]
    assert wealth[0]["x"][0] == months.add_months(CM, -6)  # the budget starts six months ago


async def test_negative_wealth_keeps_the_zero_baseline(user: User, demo, ctx) -> None:
    wealth_svc.upsert_entry(ctx, demo["chf"].id, months.add_months(CM, -6), -9000)
    await open_overview(user)
    recorded = traces(user, card(user, "Wealth Over Time"))[0]
    assert recorded["fill"] == "tozeroy"


# ------------------------------------------------------------------ adding and removing charts
@pytest.mark.parametrize(
    ("title", "hint"),
    [
        ("Wealth Over Time", "Recorded balances, then the budget projection"),
        ("Income and Expenditure", "Monthly totals or chosen categories"),
        ("Change per Account", "How each account moved between two month ends"),
    ],
)
async def test_add_chart(user: User, demo, title: str, hint: str) -> None:
    await open_overview(user)
    await menu_item(user, title)
    shown = cards(user)
    assert len(shown) == 3
    assert card_title(user, shown[0]) == title  # the new chart goes first
    assert "just-added" in shown[0].classes
    await user.should_see(hint)


async def test_waterfall_shows_each_account(user: User, demo, ctx) -> None:
    await open_overview(user)
    await menu_item(user, "Change per Account")
    chart = card(user, "Change per Account")
    start, end = months.add_months(PREV, -1), PREV
    assert control(user, chart, "From end of").value == start
    assert control(user, chart, "To end of").value == end
    (fall,) = traces(user, chart)
    assert fall["type"] == "waterfall"
    x = list(fall["x"])
    assert x[0] == f"Wealth {months.label(start)}" and x[-1] == f"Wealth {months.label(end)}"
    assert sorted(x[1:3]) == ["Bank (CHF)", "Euro account (EUR)"]
    assert list(fall["measure"]) == ["absolute", "relative", "relative", "total"]
    changes = reports.account_changes(ctx, start, end, "CHF")
    y = values(fall["y"])
    assert y[1:3] == [c["change"] for c in changes]
    assert y[0] == pytest.approx(sum(c["start"] for c in changes))
    assert y[-1] == pytest.approx(sum(c["end"] for c in changes))
    assert plot(user, chart).figure["layout"]["yaxis"]["range"][0] > 0  # zoomed in on the changes


async def test_waterfall_months_can_be_given_in_any_order(user: User, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Change per Account")
    chart = card(user, "Change per Account")
    first = months.add_months(CM, -6)
    await set_value(user, control(user, chart, "From end of"), PREV)
    await set_value(user, control(user, chart, "To end of"), first)
    x = list(traces(user, chart)[0]["x"])
    assert x[0] == f"Wealth {months.label(first)}" and x[-1] == f"Wealth {months.label(PREV)}"


async def test_waterfall_from_nothing_starts_at_zero(user: User, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Change per Account")
    chart = card(user, "Change per Account")
    await set_value(user, control(user, chart, "From end of"), months.add_months(CM, -9))
    fall = traces(user, chart)[0]
    assert values(fall["y"])[0] == 0
    assert "range" not in plot(user, chart).figure["layout"].get("yaxis", {})


async def test_remove_chart(user: User, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Remove Chart", within=card(user, "Wealth Over Time"))
    assert [card_title(user, c) for c in cards(user)] == ["Income and Expenditure"]
    # the remaining chart still reacts to the currency
    await set_value(user, find_all(user, ui.select, label="Currency")[0], "EUR")
    assert plot(user, cards(user)[0]).figure["layout"]["yaxis"]["ticksuffix"] == " EUR"


async def test_adding_a_broken_chart_is_reported(user: User, demo, monkeypatch) -> None:
    await open_overview(user)

    def broken(_ctx):
        raise RuntimeError("unknown category list")

    monkeypatch.setattr(reports, "series_keys", broken)
    await menu_item(user, "Income and Expenditure")
    await user.should_see("Chart error: unknown category list")


async def test_a_broken_chart_leaves_no_card_behind(user: User, demo, monkeypatch) -> None:
    await open_overview(user)

    def broken(_ctx):
        raise RuntimeError("unknown category list")

    monkeypatch.setattr(reports, "series_keys", broken)
    await menu_item(user, "Income and Expenditure")
    await user.should_see("Chart error: unknown category list")
    assert len(cards(user)) == 2


# ------------------------------------------------------------------ flow chart controls
async def test_flows_with_budget_as_lines(user: User, demo) -> None:
    await open_overview(user)
    chart = card(user, "Income and Expenditure")
    switch = next(s for s in find_all(user, ui.switch, chart) if s.text == "Budget")
    await click(user, switch)
    bars = traces(user, chart)
    assert [tr["name"] for tr in bars] == [
        "Total expenditure",
        "Total expenditure (budget)",
        "Total income",
        "Total income (budget)",
    ]
    assert [tr["opacity"] for tr in bars] == [1.0, 0.35, 1.0, 0.35]
    assert bars[0]["marker"]["color"] == bars[1]["marker"]["color"]  # a series and its budget share a colour
    style = find_all(user, ui.toggle, chart)[0]
    await set_value(user, style, "line")
    lines = traces(user, chart)
    assert {tr["type"] for tr in lines} == {"scatter"}
    assert [tr["line"]["dash"] for tr in lines] == ["solid", "dot", "solid", "dot"]
    assert values(lines[1]["y"])[-1] == 2750  # this month's budgeted expenditure


async def test_flows_months_and_categories(user: User, demo) -> None:
    await open_overview(user)
    chart = card(user, "Income and Expenditure")
    start, end = control(user, chart, "From"), control(user, chart, "To")
    assert (start.value, end.value) == (months.add_months(CM, -6), CM)
    await set_value(user, start, months.add_months(CM, -2))
    await set_value(user, end, months.add_months(CM, -3))  # reversed: the chart sorts them
    flows = traces(user, chart)
    assert list(flows[0]["x"]) == [months.add_months(CM, -3), months.add_months(CM, -2)]
    layout_x = plot(user, chart).figure["layout"]["xaxis"]
    assert list(layout_x["ticktext"]) == [
        months.label(months.add_months(CM, -3)),
        months.label(months.add_months(CM, -2)),
    ]
    await set_value(user, control(user, chart, "Categories"), ["EXP:04", "EXP:07"])
    flows = traces(user, chart)
    assert [tr["name"] for tr in flows] == [
        "04 Housing and energy",
        "07 Transport",
    ]
    assert values(flows[0]["y"]) == [1900, 1900]
    await set_value(user, control(user, chart, "Categories"), [])
    assert traces(user, chart) == []


async def test_currency_change_rerenders_everything(user: User, demo, ctx) -> None:
    await open_overview(user)
    await set_value(user, find_all(user, ui.select, label="Currency")[0], "EUR")
    current = wealth_svc.current_wealth(ctx, "EUR")
    assert f'{fmt(current)}<span class="cur">EUR</span>' in kpi_texts(user)
    for c in cards(user):
        assert plot(user, c).figure["layout"]["yaxis"]["ticksuffix"] == " EUR"
    income = traces(user, card(user, "Income and Expenditure"))[1]
    expected = reports.monthly_series(ctx, months.add_months(CM, -6), CM, "EUR", ["INC:TOTAL"])
    assert values(income["y"]) == pytest.approx([row["Total income"] for row in expected])
    assert values(income["y"])[0] != 6200


async def test_chart_error_is_reported(user: User, demo, monkeypatch) -> None:
    await open_overview(user)

    def broken(*_args, **_kwargs):
        raise RuntimeError("series exploded")

    monkeypatch.setattr(reports, "monthly_series", broken)
    chart = card(user, "Income and Expenditure")
    await set_value(user, control(user, chart, "From"), months.add_months(CM, -3))
    await user.should_see("Chart error: series exploded")


# ------------------------------------------------------------------ downloads
async def test_export_data_as_csv(user: User, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Export Data as CSV…", within=card(user, "Income and Expenditure"))
    text = (await downloaded(user)).content.decode()
    lines = text.splitlines()
    assert lines[0] == "month,Total expenditure,Total income"
    assert lines[1] == f"{months.add_months(CM, -6)},2660.0,6200.0"


async def test_export_wealth_data_as_excel(user: User, demo) -> None:
    await open_overview(user)
    await menu_item(user, "Export Data as Excel…", within=card(user, "Wealth Over Time"))
    data = (await downloaded(user)).content
    assert data.startswith(b"PK")
    import pandas as pd  # noqa: PLC0415

    frame = pd.read_excel(io.BytesIO(data), sheet_name="wealth")
    assert list(frame.columns) == ["month", "wealth", "type"]
    assert set(frame["type"]) == {"actual", "projected"}


@pytest.fixture
def chart_image(monkeypatch):
    """The browser renders chart images; the simulation answers with a small PNG."""
    calls = []

    async def to_image(self, name, *args, timeout=1):
        calls.append((name, args))
        return png_data_url()

    monkeypatch.setattr(ui.plotly, "run_plot_method", to_image)
    return calls


async def test_save_image(user: User, demo, chart_image) -> None:
    await open_overview(user)
    await menu_item(user, "Save Image…", within=card(user, "Wealth Over Time"))
    data = (await downloaded(user)).content
    assert data.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(data)).size == (24, 12)
    assert chart_image == [("toImage", ({"format": "png", "width": 1200, "height": 600, "scale": 2},))]


async def test_save_as_pdf(user: User, demo, chart_image) -> None:
    await open_overview(user)
    await menu_item(user, "Save as PDF…", within=card(user, "Income and Expenditure"))
    assert (await downloaded(user)).content.startswith(b"%PDF")


async def test_save_image_failure_is_reported(user: User, demo, monkeypatch) -> None:
    async def no_image(self, name, *args, timeout=1):
        return None  # e.g. the chart was not rendered yet

    monkeypatch.setattr(ui.plotly, "run_plot_method", no_image)
    await open_overview(user)
    await menu_item(user, "Save Image…", within=card(user, "Wealth Over Time"))
    await user.should_see("Could not create the image")
    assert not user.download.http_responses
