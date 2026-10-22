"""F18 in the pages: the Budget page compared with another month, *Notable this month* on the Overview, and the
links from there to the Budget page and the filtered Transactions."""

from __future__ import annotations

import asyncio
from datetime import date

from nicegui import ui
from nicegui.element import Element
from nicegui.element_filter import ElementFilter
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app.domain import months
from budget_app.domain import trends as rules
from budget_app.domain.coicop import TAX
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import trends
from budget_app.services.budget import BudgetInput
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.pages.budget_overview import _compare_summary
from budget_app.ui.widgets import notable

CM = months.current_month()
PREV = months.previous_month()
LAST_YEAR = months.add_months(CM, -12)
FIRST = months.first_day(CM)  # never in the future, whatever the day


# ------------------------------------------------------------------ helpers
def find_all(user: User, kind: type[Element], **props) -> list:
    with user:
        flt = ElementFilter(kind=kind, only_visible=True, local_scope=False)
        return sorted((e for e in flt if all(e.props.get(k) == v for k, v in props.items())), key=lambda e: e.id)


def labels(user: User) -> list[ui.label]:
    return find_all(user, ui.label)


def texts(user: User) -> list[str]:
    return [lbl.text for lbl in labels(user)]


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0.01)


async def click(user: User, element: Element) -> None:
    UserInteraction(user, {element}, None).click()
    await settle()


def compare_toggle(user: User) -> ui.toggle:
    return next(tg for tg in find_all(user, ui.toggle) if "budget" in (tg.options or {}))


async def compare(user: User, mode: str) -> None:
    with user:
        compare_toggle(user).value = mode
    await settle()


def ledger_row(user: User, code: str = "", name: str = "") -> Element:
    """The ledger row of a category by its code, or of an income line by its name."""
    with user:
        cell = next(
            e
            for e in ElementFilter(kind=ui.html, only_visible=True, local_scope=False)
            if (code and f'class="code">{code}<' in e.content) or (name and e.content == name)
        )
    return cell.parent_slot.parent.parent_slot.parent  # html -> label cell -> row


def row_texts(row: Element) -> list[str]:
    return [e.text for e in row.descendants() if isinstance(e, ui.label)]


def change_cell(row: Element) -> ui.label:
    return next(e for e in row.descendants() if isinstance(e, ui.label) and "c-diff" in e.classes)


def spend(ctx, setup, amount: float, code: str, day: date = FIRST, **kw):
    inp = TransactionInput(
        date=day, type="expenditure", amount=amount, currency="CHF", category=code, from_account=setup["chf"].id
    )
    for key, value in kw.items():
        setattr(inp, key, value)
    return tx_svc.save(ctx, inp)


def notable_links(user: User) -> list[ui.link]:
    return [lk for lk in find_all(user, ui.link) if "notable-row" in lk.classes]


# ------------------------------------------------------------------ Budget page
async def test_the_budget_page_compares_with_the_month_before_and_the_year_before(user: User, ctx, demo):
    spend(ctx, demo, 800, "01.1")
    await user.open("/budget")
    await user.should_see("Compare with")
    assert compare_toggle(user).options == {
        "budget": "Budget",
        "month": months.label(PREV),
        "year": months.label(LAST_YEAR),
    }
    assert "Difference" in texts(user)
    edits = len(find_all(user, ui.button, icon="o_edit"))
    assert edits > 0

    await compare(user, "month")
    assert "Difference" not in texts(user) and "Change" in texts(user)
    assert months.label(PREV) in texts(user)  # the column header
    food = change_cell(ledger_row(user, "01"))  # 800 against 640
    assert food.text == "▲ 25 %" and "text-neg" in food.classes and food.props["title"] == "+160.00 CHF"
    housing = change_cell(ledger_row(user, "04"))  # nothing yet against 1'900
    assert housing.text == "▼ 100 %" and "text-neg" not in housing.classes
    assert "1'900.00" in row_texts(ledger_row(user, "04"))
    assert change_cell(ledger_row(user, "02")).text == "—"
    assert f"{months.label(CM)} so far, against all of {months.label(PREV)}" in texts(user)
    assert any(t.startswith("70 % less than in") for t in texts(user))  # 800 against 2'660
    income = f"100 % less than in {months.label(PREV)} (6'200.00 CHF)"
    assert income in texts(user)
    assert not find_all(user, ui.button, icon="o_edit")  # budgets are edited against the budget
    assert "Budget Another Category" not in [b.text for b in find_all(user, ui.button)]

    await compare(user, "year")
    assert change_cell(ledger_row(user, "01")).text == "new"
    assert f"Nothing recorded in {months.label(LAST_YEAR)}" in texts(user)

    await compare(user, "budget")
    assert "Difference" in texts(user) and len(find_all(user, ui.button, icon="o_edit")) == edits


async def test_a_complete_month_compared_with_one_that_had_the_same(user: User, ctx, demo):
    bonus = acc_svc.add_income_category(ctx, "Bonus")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=months.first_day(months.add_months(PREV, -1)),
            type="income",
            amount=500,
            currency="CHF",
            category=bonus.id,
            to_account=demo["chf"].id,
        ),
    )
    await user.open(f"/budget?month={PREV}")
    await user.should_see(months.label(PREV))
    await compare(user, "month")
    assert "so far" not in " ".join(texts(user))  # a finished month
    assert change_cell(ledger_row(user, "04")).text == "0 %"
    assert any(t.startswith("The same as in") for t in texts(user))  # the expenditure total
    bonus_row = ledger_row(user, name="Bonus")  # income only in the month compared with gets a row
    assert change_cell(bonus_row).text == "▼ 100 %" and "text-neg" in change_cell(bonus_row).classes


async def test_a_link_opens_the_budget_page_at_a_month_and_category(user: User, ctx, demo):
    await user.open(f"/budget?month={PREV}&category=01.1")
    await user.should_see(months.label(PREV))
    row = ledger_row(user, "01.1")  # its division is open
    assert "target" in row.classes
    await user.open("/budget?month=nonsense&category=99.9")
    await user.should_see(months.label(CM))
    assert not any("target" in e.classes for e in find_all(user, ui.element))


# ------------------------------------------------------------------ Overview
async def test_the_card_hides_when_nothing_stands_out(user: User, ctx, demo):
    await user.open("/")
    await user.should_see("Wealth")
    await user.should_not_see("Notable This Month")


async def test_the_card_lists_what_stands_out_and_links_to_it(user: User, ctx, demo):
    spend(ctx, demo, 300, "07.3")  # over the 150 budget
    spend(ctx, demo, 1290, "05.3.1.2", payee="Fust")  # nearly half of a usual month, in a category never used
    await user.open("/")
    await user.should_see("Notable This Month")
    await user.should_see(f"{months.label(CM)} so far")
    await user.should_see("07.3 Passenger transport services")
    await user.should_see("150.00 over the 150.00 CHF budget")
    await user.should_see("Fust")
    await user.should_see("1'290.00 CHF")
    sub = next(t for t in texts(user) if "05.3 Household appliances" in t)
    assert sub.endswith("· 48 % of a usual month")  # 1'290 of 2'660
    assert [lk.props["href"] for lk in notable_links(user)] == [
        f"/budget?month={CM}&category=07.3",
        f"/transactions?month={CM}&category=05.3",
    ]
    await user.should_not_see("Comparisons with your usual spending")

    await click(user, notable_links(user)[1])
    await user.should_see("Transactions")
    [grid] = user.find(kind=ui.aggrid).elements
    assert [r["payee"] for r in grid.options["rowData"]] == ["Fust"]


async def test_the_card_follows_the_currency(user: User, ctx, demo):
    spend(ctx, demo, 300, "07.3")
    await user.open("/")
    await user.should_see("Notable This Month")
    currency = next(s for s in find_all(user, ui.select) if s.props.get("label") == "Currency")
    with user:
        currency.value = "EUR"
    await settle()
    assert any(t.endswith("EUR budget") for t in texts(user))


async def test_a_short_history_says_so(user: User, ctx, setup):
    spend(ctx, setup, 50, "01.1")
    await user.open("/")
    await user.should_see("Notable This Month")
    await user.should_see("Comparisons with your usual spending start once 3 months are recorded.")
    assert not notable_links(user)


async def test_no_records_no_card(user: User, ctx, setup):
    await user.open("/")
    await user.should_see("Wealth")
    await user.should_not_see("Notable This Month")


# ------------------------------------------------------------------ Transactions filtered
async def test_transactions_filtered_by_month_and_category(user: User, ctx, setup):
    spend(ctx, setup, 40, "01.1.1", payee="Coop")
    spend(ctx, setup, 25, "11.1", payee="Hirschen")
    spend(ctx, setup, 30, "01.1", day=months.first_day(PREV), payee="Last month")
    receipt = spend(ctx, setup, 60, "05.6", payee="Migros")  # its bread counts in 01.1 (F11)
    items_svc.replace(ctx, receipt.id, [ItemInput("Soap", 1, 45), ItemInput("Bread", 1, 15, "01.1.3")])
    tx_svc.save(
        ctx,
        TransactionInput(
            date=FIRST,
            type="income",
            amount=10,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    budget_svc.create(  # unrelated, so the page has a budget too
        ctx, BudgetInput(start_month=CM, end_month=CM, type="expenditure", currency="CHF", amount=10, category="01")
    )
    await user.open(f"/transactions?month={CM}&category=01")
    await user.should_see("Transactions")
    [grid] = user.find(kind=ui.aggrid).elements
    assert sorted(r["payee"] for r in grid.options["rowData"]) == ["Coop", "Migros"]
    dates = [e.value for e in find_all(user, ui.input) if e.props.get("label") in ("From", "To")]
    assert dates == [months.first_day(CM).isoformat(), months.last_day(CM).isoformat()]

    await user.open("/transactions?month=2026-13&category=ADV")
    await user.should_see("Transactions")
    [grid] = user.find(kind=ui.aggrid).elements
    assert len(grid.options["rowData"]) == 5  # nothing filtered
    await user.should_not_see("Min amount")  # the filters stay closed


# ------------------------------------------------------------------ wording
def test_the_wording_of_each_kind_and_change():
    result = trends.Notable(CM, "CHF", history_months=6)
    pace = trends.Item(trends.PACE, "01.1", 400, 600, projected=688.89)
    assert notable.texts(pace, result) == ("01.1 Food", "On pace for 688.89 of the 600.00 CHF budget")
    usual = trends.Item(trends.ABOVE_USUAL, "11.1", 360, 120)
    assert notable.texts(usual, result) == ("11.1 Food and beverage serving services", "Usually 120.00 CHF a month")
    new = trends.Item(trends.ABOVE_USUAL, "09.4", 150, 0)
    assert notable.texts(new, result)[1] == "Nothing in the 6 months before"
    bare = trends.Item(trends.LARGE, TAX, 900, 0)  # no usual month to compare with
    assert notable.texts(bare, result) == ("Taxes", "Taxes")
    more = rules.Change(120, 100)
    assert _compare_summary(more, PREV, "CHF") == f"20 % more than in {months.label(PREV)} (100.00 CHF)"
