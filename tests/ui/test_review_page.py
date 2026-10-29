"""The Year in Review page (F19): the metric strip, the chart, the groups with their changes, the biggest payments,
the year and currency pickers, opening a transaction, and the PDF and Excel exports."""

import asyncio
import html
import io
from datetime import date

import pandas as pd
from nicegui import ui
from nicegui.testing import User
from test_data_io import downloaded, menu_item
from test_overview_page import png_data_url
from test_payees_page import click, icon_buttons, newest

from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.pages import review as review_page

LAST = date.today().year - 1  # the year the page opens with


def spend(ctx, setup, amount, category, day, **kw):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=day,
            type="expenditure",
            amount=amount,
            currency="CHF",
            category=category,
            from_account=setup["chf"].id,
            **kw,
        ),
    )


def earn(ctx, setup, amount, day):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=day,
            type="income",
            amount=amount,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )


def filled(ctx, setup):
    """Last year against the year before: salaries, rent, a washing machine and balances."""
    for month in (2, 8):
        earn(ctx, setup, 6000, date(LAST, month, 25))
        earn(ctx, setup, 5000, date(LAST - 1, month, 25))
        spend(ctx, setup, 1900, "04.1", date(LAST, month, 1), payee="Immo Muster AG")
        spend(ctx, setup, 1800, "04.1", date(LAST - 1, month, 1), payee="Immo Muster AG")
    fust = spend(ctx, setup, 2400, "05.3", date(LAST, 8, 20), payee="Fust", comment="Washing machine")
    spend(ctx, setup, 400, "01.1", date(LAST - 1, 5, 2), payee="Migros")
    wealth_svc.upsert_entry(ctx, setup["chf"].id, f"{LAST - 1}-12", 10000)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, f"{LAST}-12", 14000)
    return fust


def labels(user: User) -> list[str]:
    return [lb.text for lb in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


def kpis(user: User) -> list[str]:
    found = sorted(user.find(kind=ui.html).elements, key=lambda e: e.id)
    return [html.unescape(h.content) for h in found if "cur" in h.content]


def plot(user: User) -> ui.plotly:
    return newest(user, ui.plotly)


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0.01)


# ------------------------------------------------------------------ the page
async def test_the_sidebar_has_the_page_before_the_tax_summary(user: User) -> None:
    assert layout.NAV[-1][0] == "/review"  # Reports, the last place
    assert [p for p, _ in layout.SECTIONS["/review"]] == ["/review", "/tax"]
    await user.open("/review")
    await user.should_see("Year in Review")
    await user.should_see(f"Nothing recorded in {date.today().year}")  # no data: this year, so far


async def test_the_page_reviews_last_year(user: User, ctx, setup) -> None:
    filled(ctx, setup)
    await user.open("/review")
    await user.should_see(str(LAST), kind=ui.label)
    shown = labels(user)
    assert f"compared with {LAST - 1}" in shown
    assert "Income" in shown and "Expenditure" in shown and "Saved" in shown and "Wealth" in shown
    assert kpis(user)[:4] == [
        '12\'000.00<span class="cur">CHF</span>',
        '6\'200.00<span class="cur">CHF</span>',
        '5\'800.00<span class="cur">CHF</span>',
        '14\'000.00<span class="cur">CHF</span>',
    ]
    assert f"{LAST - 1}: 10'000.00 CHF" in shown  # income the year before
    assert f"48 % savings rate · {LAST - 1}: 60 %" in shown
    assert f"+4'000.00 since the end of Dec {LAST - 1}" in shown
    # the groups
    assert "Spending by Category" in shown and "Biggest Expenses" in shown
    assert "04 Housing, water, electricity, gas and other fuels" in shown and "3'800.00 CHF" in shown
    assert "▲ 6 %" in shown and f"{LAST - 1}: 3'600.00 CHF · 61 % of spending" in shown  # the rent rose
    assert "new" in shown  # the washing machine's division had nothing the year before
    assert f"Nothing in {LAST - 1} · 39 % of spending" in shown  # the caption of a new line
    assert "▼ 100 %" in shown and f"{LAST - 1}: 400.00 CHF" in shown  # groceries stopped
    assert "Fust" in shown and f"20 Aug {LAST} · 05.3 Household appliances" in shown
    assert "Salary" in shown and "▲ 20 %" in shown
    # the chart: one bar series each for income and expenditure, over the twelve months
    data = plot(user).figure["data"]
    assert [trace["name"] for trace in data] == ["Income", "Expenditure"]
    assert len(data[0]["x"]) == 12


async def test_change_colours_and_labels() -> None:
    from budget_app.domain.trends import Change  # noqa: PLC0415

    assert review_page.change_label(Change(0, 0), True) == ("—", "text-3")
    assert review_page.change_label(Change(10, 0), True) == ("new", "text-neg")
    assert review_page.change_label(Change(100, 100), True) == ("0 %", "text-2")
    assert review_page.change_label(Change(120, 100), True) == ("▲ 20 %", "text-neg")
    assert review_page.change_label(Change(80, 100), True) == ("▼ 20 %", "text-2")
    assert review_page.change_label(Change(80, 100), False) == ("▼ 20 %", "text-neg")  # income fell
    assert review_page.change_label(Change(120, 100), False) == ("▲ 20 %", "text-2")


async def test_switching_the_year_and_the_currency(user: User, ctx, setup) -> None:
    filled(ctx, setup)
    spend(ctx, setup, 90, "01.1", date(date.today().year, 10, 16))
    await user.open("/review")
    await user.should_see(f"compared with {LAST - 1}")
    year = newest(user, ui.select, label="Year")
    assert year.options == {date.today().year: str(date.today().year), LAST: str(LAST), LAST - 1: str(LAST - 1)}
    with user:
        year.value = date.today().year
    await settle()
    await user.should_see(f"compared with the same months of {LAST}")
    assert "so far (January to" in " ".join(labels(user))
    currency = newest(user, ui.select, label="Currency")
    with user:
        currency.value = "EUR"
    await settle()
    assert any(x.endswith('<span class="cur">EUR</span>') for x in kpis(user))
    with user:
        currency.value = None  # cleared: the page keeps its currency
    await settle()
    assert any(x.endswith('<span class="cur">EUR</span>') for x in kpis(user))


async def test_opening_a_big_payment_and_refreshing_after_a_change(user: User, ctx, setup) -> None:
    fust = filled(ctx, setup)
    await user.open("/review")
    await user.should_see("Fust")
    click(user, icon_buttons(user, "o_open_in_new")[0])
    await user.should_see("Edit Transaction")
    assert newest(user, ui.input, label="Payee").value == "Fust"
    tx_svc.delete(ctx, [fust.id])
    with user:
        layout.data_changed()
    await settle()
    assert "Fust" not in labels(user)
    assert "Washing machine" not in labels(user)


async def test_a_year_without_balances_or_payments(user: User, ctx, setup) -> None:
    spend(ctx, setup, 100, "01.1", date(LAST, 3, 3))
    await user.open("/review")
    await user.should_see("No balances recorded")
    await user.should_see("No income recorded")
    tx_svc.delete(ctx, [tx.id for tx in ctx.store.transactions.all()])
    earn(ctx, setup, 500, date(LAST, 3, 3))
    wealth_svc.upsert_entry(ctx, setup["chf"].id, f"{LAST}-12", 500)  # a balance at the end only
    with user:
        layout.data_changed()
    await settle()
    await user.should_see(f"No balances at the end of Dec {LAST - 1}")
    assert labels(user).count("Nothing recorded") == 2  # no spending: no categories, no biggest payments


async def test_the_pdf_of_an_empty_year_has_no_chart_to_ask_for(user: User, ctx, setup, monkeypatch) -> None:
    async def to_image(self, name, *args, timeout=1):
        raise AssertionError("there is no chart on an empty page")

    monkeypatch.setattr(ui.plotly, "run_plot_method", to_image)
    await user.open("/review")
    await user.should_see("Nothing recorded in")
    await menu_item(user, "Save as PDF…")
    assert (await downloaded(user)).content.startswith(b"%PDF")


async def test_export_as_excel(user: User, ctx, setup) -> None:
    filled(ctx, setup)
    await user.open("/review")
    await user.should_see("Fust")
    await menu_item(user, "Export as Excel…")
    response = await downloaded(user)
    assert response.content.startswith(b"PK")
    sheets = pd.read_excel(io.BytesIO(response.content), sheet_name=None)
    assert list(sheets) == ["Summary", "Months", "Expenditure", "Income", "Biggest Expenses"]
    assert sheets["Biggest Expenses"]["Payee"].tolist() == ["Fust", "Immo Muster AG", "Immo Muster AG"]


async def test_save_as_pdf_with_the_chart(user: User, ctx, setup, monkeypatch) -> None:
    calls = []

    async def to_image(self, name, *args, timeout=1):
        calls.append((name, args))
        return png_data_url()

    monkeypatch.setattr(ui.plotly, "run_plot_method", to_image)
    filled(ctx, setup)
    await user.open("/review")
    await user.should_see("Fust")
    await menu_item(user, "Save as PDF…")
    response = await downloaded(user)
    assert response.content.startswith(b"%PDF")
    assert calls == [("toImage", (review_page.IMAGE,))]


async def test_the_pdf_does_without_a_chart_the_browser_cannot_render(user: User, ctx, setup, monkeypatch) -> None:
    async def no_image(self, name, *args, timeout=1):
        return None

    monkeypatch.setattr(ui.plotly, "run_plot_method", no_image)
    filled(ctx, setup)
    await user.open("/review")
    await user.should_see("Fust")
    await menu_item(user, "Save as PDF…")
    assert (await downloaded(user)).content.startswith(b"%PDF")


async def test_an_export_error_is_reported(user: User, ctx, setup, monkeypatch) -> None:
    filled(ctx, setup)
    monkeypatch.setattr(review_page.review_export, "xlsx", lambda review: 1 / 0)
    await user.open("/review")
    await user.should_see("Fust")
    await menu_item(user, "Export as Excel…")
    await user.should_see("Could not create the file")
    assert not user.download.http_responses
