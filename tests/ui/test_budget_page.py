"""The budget page: month navigation, the ledger tree, balances, budget editing and statement export."""

from __future__ import annotations

import asyncio
import html
import sys
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from nicegui import app, ui
from nicegui.element import Element
from nicegui.element_filter import ElementFilter
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app.domain import months
from budget_app.domain.models import BUDGET_EXPENDITURE, BUDGET_INCOME
from budget_app.services import accounts as acc_svc
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.layout import fmt
from budget_app.ui.pages.add_budget import FOREVER

CM = months.current_month()
PREV = months.previous_month()


# ------------------------------------------------------------------ helpers
def find_all(user: User, kind: type[Element], within: Element | None = None, *, hidden: bool = False, **props) -> list:
    """Visible (or with `hidden`, all) elements of `kind` whose props match, in creation order."""
    with user:
        flt = ElementFilter(kind=kind, only_visible=not hidden, local_scope=False)
        if within is not None:
            flt = flt.within(instance=within)
        return sorted((e for e in flt if all(e.props.get(k) == v for k, v in props.items())), key=lambda e: e.id)


def find_one(user: User, kind: type[Element], within: Element | None = None, **props):
    found = find_all(user, kind, within, **props)
    assert found, f"no {kind.__name__} with {props}"
    return found[0]


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


async def downloaded(user: User, timeout: float = 5.0) -> httpx.Response:
    """The first file the page handed to the user (it may have arrived while the click settled)."""
    for _ in range(int(timeout / 0.05)):
        if user.download.http_responses:
            return user.download.http_responses[0]
        await asyncio.sleep(0.05)
    raise AssertionError("no download happened")


async def menu_item(user: User, text: str) -> None:
    """Click a menu item; menus are closed in the simulation, so their items count as hidden."""
    with user:
        items = sorted(
            (
                item
                for item in ElementFilter(kind=ui.menu_item, local_scope=False)
                if any(getattr(e, "text", None) == text for e in item.descendants())
            ),
            key=lambda i: i.id,
        )
    assert items, f"no menu item {text!r}"
    await click(user, items[0])


def open_dialog(user: User) -> ui.dialog:
    dialogs = [d for d in find_all(user, ui.dialog) if d.value]
    assert dialogs, "no open dialog"
    return dialogs[-1]


def button(user: User, text: str, within: Element | None = None) -> ui.button:
    found = [b for b in find_all(user, ui.button, within) if b.text == text]
    assert found, f"no button {text!r}"
    return found[0]


def month_select(user: User) -> ui.select:
    return next(s for s in find_all(user, ui.select) if CM in (s.options or {}) and s.props.get("label") is None)


def ledger_row(user: User, code: str) -> Element:
    """The ledger row of a category, found by its code cell."""
    with user:
        cell = next(
            e
            for e in ElementFilter(kind=ui.html, only_visible=True, local_scope=False)
            if f'class="code">{code}<' in e.content
        )
    return cell.parent_slot.parent.parent_slot.parent  # html -> label cell -> row


def label_cell(user: User, code: str) -> Element:
    row = ledger_row(user, code)
    return next(c for c in row.default_slot.children if "cell-label" in c.classes)


async def open_budget(user: User, month: str | None = None) -> None:
    await user.open("/budget")
    await user.should_see("Record Balance")
    if month:
        await set_value(user, month_select(user), month)
        await user.should_see(months.label(month))


async def edit_budget(user: User, code: str) -> ui.dialog:
    await click(user, find_one(user, ui.button, within=ledger_row(user, code), icon="o_edit"))
    return open_dialog(user)


def budgets(ctx, category: str) -> dict[str, float]:
    return {b.month: b.amount for b in ctx.store.budget.all() if b.category == category}


# ------------------------------------------------------------------ navigation
async def test_current_month_shows_projection_and_ledger(user: User, demo) -> None:
    await open_budget(user)
    await user.should_see(months.label(CM))
    await user.should_see("Projected Wealth")
    await user.should_see("Net Income")
    # nothing booked yet this month: the whole salary is still to come
    await user.should_see("6'200.00 still expected of 6'200.00 CHF")
    await user.should_see("2'750.00 left of 2'750.00 CHF")
    await user.should_not_see("Balances · End of")


async def test_month_stepper_moves_between_months(user: User, demo) -> None:
    await open_budget(user)
    await click(user, find_one(user, ui.button, icon="chevron_left"))
    await user.should_see(f"Balances · End of {months.label(PREV)}")
    await user.should_see("On budget, 6'200.00 CHF")
    await click(user, find_one(user, ui.button, icon="chevron_right"))
    await click(user, find_one(user, ui.button, icon="chevron_right"))
    assert month_select(user).value == months.add_months(CM, 1)
    await user.should_see("Projected Wealth")
    await user.should_not_see("Balances · End of")


async def test_month_stepper_stops_at_the_last_month(user: User, demo) -> None:
    await open_budget(user)
    sel = month_select(user)
    last = list(sel.options)[-1]
    await set_value(user, sel, last)
    await click(user, find_one(user, ui.button, icon="chevron_right"))
    assert sel.value == last


async def test_month_without_data(user: User, demo) -> None:
    await open_budget(user, months.add_months(CM, -7))
    await user.should_see("No income this month.")
    await user.should_see("No balances recorded yet.")
    await user.should_see("No budget set")


async def test_first_recorded_month_has_no_reconciliation_yet(user: User, demo) -> None:
    await open_budget(user, months.add_months(CM, -6))
    await user.should_see("Appears once balances are recorded for this and the previous month.")
    await user.should_see("90.00 left of 2'750.00 CHF")  # 2'660 spent
    await user.should_not_see("Unexplained")


async def test_reconciliation_line_shows_the_gap(user: User, demo, ctx) -> None:
    rep = reports.month_report(ctx, PREV, "CHF")
    assert rep.unexplained is not None and rep.unexplained < 0  # the demo balances fall faster than booked
    await open_budget(user, PREV)
    await user.should_see("Reconciliation")
    await user.should_see("Recorded Net")
    await user.should_see("Change in Balances")
    await user.should_see("Money that left without a record")
    amounts = [e.content for e in find_all(user, ui.html)]
    assert any(html.escape(fmt(rep.unexplained)) in c for c in amounts)  # amounts are HTML (number + currency)
    # no row for it in the expenditure table any more (F42, B04)
    await user.should_not_see("Unaccounted Expenditure")


async def test_currency_change_converts_the_ledger(user: User, demo, ctx) -> None:
    await open_budget(user, PREV)
    await user.should_see("6'200.00 CHF")
    currency = find_one(user, ui.select, label="Currency")
    await set_value(user, currency, "EUR")
    rep = reports.month_report(ctx, PREV, "EUR")
    await user.should_see(f"{rep.total_income.actual:,.2f} EUR".replace(",", "'"))
    await user.should_not_see("6'200.00 CHF")
    # the CHF account is foreign now and shows its own amount beside the converted one
    await user.should_see("4'150.00 CHF")


async def test_over_and_above_budget_summaries(user: User, demo, ctx) -> None:
    y, m = map(int, PREV.split("-"))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(y, m, 26),
            type="income",
            amount=300,
            currency="CHF",
            category=demo["salary"].id,
            to_account=demo["chf"].id,
        ),
    )
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(y, m, 12),
            type="expenditure",
            amount=400,
            currency="CHF",
            category="09.1",
            from_account=demo["chf"].id,
        ),
    )
    rep = reports.month_report(ctx, PREV, "CHF")
    over = rep.total_expenditure.actual - rep.total_expenditure.budget
    assert over > 0  # 2'660 + 400 recorded against a budget of 2'750
    await open_budget(user, PREV)
    await user.should_see(f"{fmt(over)} over the 2'750.00 CHF budget")
    await user.should_see("300.00 above the 6'200.00 CHF budget")
    assert any("over" in e.classes for e in find_all(user, ui.element) if "meter" in e.classes)


# ------------------------------------------------------------------ the expenditure tree
async def test_expanding_a_division_shows_its_subcategories(user: User, demo) -> None:
    await open_budget(user, PREV)
    assert not [e for e in find_all(user, ui.html) if 'class="code">01.1<' in e.content]
    await click(user, label_cell(user, "01"))
    sub = next(e for e in find_all(user, ui.html) if 'class="code">01.1<' in e.content)
    assert sub.props["title"] == "01.1 Food"
    assert sub.parent_slot.parent._style["padding-left"] == "18px"  # indented one level
    assert any("open" in i.classes for i in find_all(user, ui.icon, label_cell(user, "01")))
    assert not [e for e in find_all(user, ui.html) if 'class="code">01.2<' in e.content]  # empty: hidden
    # collapse again
    await click(user, label_cell(user, "01"))
    assert not [e for e in find_all(user, ui.html) if 'class="code">01.1<' in e.content]


async def test_show_all_subcategories_lists_empty_ones(user: User, demo) -> None:
    await open_budget(user, PREV)
    await click(user, label_cell(user, "01"))
    assert not [e for e in find_all(user, ui.html) if 'class="code">01.2<' in e.content]
    switch = next(s for s in find_all(user, ui.switch) if s.text == "Show all subcategories")
    await click(user, switch)
    row = ledger_row(user, "01.2")  # empty, but listed now
    assert "dim" in row.classes


async def test_past_month_cannot_be_edited(user: User, demo) -> None:
    await open_budget(user, PREV)
    assert not find_all(user, ui.button, within=ledger_row(user, "04"), icon="o_edit")
    await open_budget(user)
    assert find_all(user, ui.button, within=ledger_row(user, "04"), icon="o_edit")


# ------------------------------------------------------------------ balances
async def test_accounts_card_lists_balances_of_a_past_month(user: User, demo) -> None:
    await open_budget(user, PREV)
    await user.should_see("Bank")
    await user.should_see("Euro account")
    await user.should_see("4'150.00 CHF")
    await user.should_see("780.00 EUR")  # the foreign account shows its own currency too
    await user.should_see("Total")
    assert len([b for b in find_all(user, ui.button) if b.text == "Edit"]) == 2


async def test_record_balance_for_a_new_month(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, button(user, "Record Balance"))
    dlg = open_dialog(user)
    await user.should_see("What did the account hold at the end of the month?")
    save = button(user, "Save", dlg)
    assert not save.enabled
    await set_value(user, find_one(user, ui.select, dlg, label="Month"), CM)
    await set_value(user, find_one(user, ui.select, dlg, label="Account"), demo["eur"].id)
    amount = find_one(user, ui.number, dlg, label="Balance")
    assert amount.value is None
    assert amount.props["suffix"] == "EUR"
    assert not save.enabled
    await set_value(user, amount, 912.5)
    assert save.enabled
    await click(user, save)
    await user.should_see("Balance recorded")
    entry = wealth_svc.entry_for(ctx, demo["eur"].id, CM)
    assert entry is not None and entry.amount == 912.5 and entry.currency == "EUR"
    assert not dlg.value


async def test_record_balance_edits_an_existing_entry(user: User, demo, ctx) -> None:
    await open_budget(user, PREV)
    edit = next(b for b in find_all(user, ui.button) if b.text == "Edit")  # Bank comes first
    await click(user, edit)
    dlg = open_dialog(user)
    await user.should_see("Already recorded. Saving replaces it.")
    amount = find_one(user, ui.number, dlg, label="Balance")
    assert amount.value == 4150
    await set_value(user, amount, 4321)
    await click(user, button(user, "Save", dlg))
    await user.should_see("Balance recorded")
    assert wealth_svc.entry_for(ctx, demo["chf"].id, PREV).amount == 4321
    await user.should_see("4'321.00 CHF")  # the page refreshed


async def test_record_balance_reports_errors(user: User, demo, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(wealth_svc, "upsert_entry", broken)
    await open_budget(user)
    await click(user, button(user, "Record Balance"))
    dlg = open_dialog(user)
    await set_value(user, find_one(user, ui.select, dlg, label="Account"), demo["chf"].id)
    await click(user, button(user, "Save", dlg))
    await user.should_see("disk full")
    assert dlg.value  # stays open to correct the input


async def test_missing_balance_callout_opens_the_entry_sheet(user: User, demo, ctx) -> None:
    entry = wealth_svc.entry_for(ctx, demo["eur"].id, PREV)
    ctx.store.account_entries.delete([entry.id])
    await open_budget(user, PREV)
    await user.should_see(f"No balance recorded for Euro account at the end of {months.label(PREV)}.")
    await user.should_see("Not recorded, last balance carried over")
    await click(user, button(user, "Record"))  # the callout's button
    dlg = open_dialog(user)
    assert find_one(user, ui.select, dlg, label="Account").value == demo["eur"].id
    assert find_one(user, ui.select, dlg, label="Month").value == PREV
    await set_value(user, find_one(user, ui.number, dlg, label="Balance"), 777)
    await click(user, button(user, "Save", dlg))
    await user.should_see("Balance recorded")
    assert wealth_svc.entry_for(ctx, demo["eur"].id, PREV).amount == 777
    await user.should_not_see("No balance recorded for Euro account")


# ------------------------------------------------------------------ budget editing
async def test_edit_amount_of_this_month_only(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "01"))
    dlg = await edit_budget(user, "01.1")
    await user.should_see(f"Budget for {months.label(CM)}")
    later = len([m for m in budgets(ctx, "01.1") if m > CM])
    await user.should_see(f"Also change the {later} later months of this series")
    amounts = find_all(user, ui.number, dlg, label="Amount")
    assert amounts[0].value == 700
    await set_value(user, amounts[0], 820)
    await click(user, button(user, "Save", dlg))
    rows = budgets(ctx, "01.1")
    assert rows[CM] == 820
    assert rows[months.add_months(CM, 1)] == 700
    assert rows[PREV] == 700
    await user.should_see("2'870.00 left of 2'870.00 CHF")


async def test_edit_amount_and_note_of_the_later_months_too(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "01"))
    dlg = await edit_budget(user, "01.1")
    await set_value(user, find_all(user, ui.number, dlg, label="Amount")[0], 650)
    await set_value(user, find_all(user, ui.textarea, dlg, label="Note")[0], "  groceries  ")
    following = next(s for s in find_all(user, ui.switch, dlg) if "later months" in s.text)
    await click(user, following)
    await click(user, button(user, "Save", dlg))
    entries = [b for b in ctx.store.budget.all() if b.category == "01.1"]
    assert {b.amount for b in entries if b.month >= CM} == {650}
    assert {b.comment for b in entries if b.month >= CM} == {"groceries"}
    assert {b.amount for b in entries if b.month < CM} == {700}


async def test_remove_this_month(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "07"))
    dlg = await edit_budget(user, "07.3")
    remove = next(c for c in find_all(user, ui.checkbox, dlg) if c.text == "Remove")
    await click(user, remove)
    await click(user, button(user, "Save", dlg))
    rows = budgets(ctx, "07.3")
    assert CM not in rows
    assert rows[PREV] == 150 and rows[months.add_months(CM, 1)] == 150


async def test_changing_until_replans_the_series(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "04"))
    dlg = await edit_budget(user, "04.1")
    hint = next(
        lb for lb in find_all(user, ui.label, dlg, hidden=True) if lb.text.startswith("Changing Until or Repeat")
    )
    assert not hint.visible
    until = find_all(user, ui.select, dlg, label="Until")[0]
    assert until.value == FOREVER
    end = months.add_months(CM, 4)
    await set_value(user, until, end)
    assert hint.visible
    await set_value(user, find_all(user, ui.select, dlg, label="Repeat")[0], 2)
    await set_value(user, find_all(user, ui.number, dlg, label="Amount")[0], 2000)
    await click(user, button(user, "Save", dlg))
    entries = [b for b in ctx.store.budget.all() if b.category == "04.1"]
    planned = sorted((b.month, b.amount) for b in entries if b.month >= CM)
    assert planned == [(CM, 2000), (months.add_months(CM, 2), 2000), (months.add_months(CM, 4), 2000)]
    earlier = [b for b in entries if b.month < CM]
    assert len(earlier) == 6 and not any(b.forever for b in earlier)
    new = [b for b in entries if b.month >= CM]
    assert {b.recur_months for b in new} == {2} and len({b.series_id for b in new}) == 1


async def test_replan_hides_the_later_months_switch(user: User, demo) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "04"))
    dlg = await edit_budget(user, "04.1")
    following = next(s for s in find_all(user, ui.switch, dlg) if "later months" in s.text)
    assert following.visible
    await set_value(user, find_all(user, ui.select, dlg, label="Repeat")[0], 3)
    assert not following.visible
    await set_value(user, find_all(user, ui.select, dlg, label="Repeat")[0], 1)
    assert following.visible


async def test_add_a_budget_to_an_empty_category(user: User, demo, ctx) -> None:
    await open_budget(user)
    dlg = await edit_budget(user, "02")
    await user.should_see("Alcoholic beverages, tobacco and narcotics")
    assert not find_all(user, ui.checkbox, dlg)  # nothing to remove
    await set_value(user, find_one(user, ui.number, dlg, label="Amount"), 45)
    await set_value(user, find_one(user, ui.select, dlg, label="Until"), months.add_months(CM, 6))
    await set_value(user, find_one(user, ui.select, dlg, label="Repeat"), 3)
    await set_value(user, find_one(user, ui.textarea, dlg, label="Note"), "wine club")
    await click(user, button(user, "Save", dlg))
    entries = sorted((b for b in ctx.store.budget.all() if b.category == "02"), key=lambda b: b.month)
    assert [b.month for b in entries] == [CM, months.add_months(CM, 3), months.add_months(CM, 6)]
    assert {(b.amount, b.currency, b.comment, b.type) for b in entries} == {(45, "CHF", "wine club", "expenditure")}
    assert not entries[0].forever


async def test_add_a_forever_entry_beside_an_existing_one(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "07"))
    dlg = await edit_budget(user, "07.3")
    await user.should_see("Add to this budget")
    amounts = find_all(user, ui.number, dlg, label="Amount")
    assert len(amounts) == 2
    await set_value(user, amounts[1], 30)
    await set_value(user, find_all(user, ui.select, dlg, label="Until")[1], FOREVER)
    await click(user, button(user, "Save", dlg))
    this_month = sorted(b.amount for b in ctx.store.budget.all() if b.category == "07.3" and b.month == CM)
    assert this_month == [30, 150]
    added = [b for b in ctx.store.budget.all() if b.category == "07.3" and b.amount == 30]
    assert all(b.forever for b in added) and max(b.month for b in added) == ctx.horizon()


async def test_invalid_kept_amount_changes_nothing(user: User, demo, ctx) -> None:
    before = sorted((b.month, b.category, b.amount) for b in ctx.store.budget.all())
    await open_budget(user)
    await click(user, label_cell(user, "01"))
    dlg = await edit_budget(user, "01.1")
    amounts = find_all(user, ui.number, dlg, label="Amount")
    await set_value(user, amounts[0], 0)
    await set_value(user, amounts[1], 20)  # a valid new entry must not be added either
    await click(user, button(user, "Save", dlg))
    await user.should_see("Every kept amount must be a positive number with a currency")
    assert dlg.value
    assert sorted((b.month, b.category, b.amount) for b in ctx.store.budget.all()) == before


async def test_invalid_new_entry_is_rejected(user: User, demo, ctx) -> None:
    count = len(ctx.store.budget.all())
    await open_budget(user)
    dlg = await edit_budget(user, "03")
    await set_value(user, find_one(user, ui.number, dlg, label="Amount"), 80)
    await set_value(user, find_one(user, ui.select, dlg, label="Currency"), None)
    await click(user, button(user, "Save", dlg))
    await user.should_see("Currency is required")
    assert len(ctx.store.budget.all()) == count


async def test_invalid_replan_is_rejected(user: User, demo, ctx) -> None:
    await open_budget(user)
    await click(user, label_cell(user, "01"))
    dlg = await edit_budget(user, "01.1")
    await set_value(user, find_all(user, ui.select, dlg, label="Until")[0], None)
    await click(user, button(user, "Save", dlg))
    await user.should_see("Select the final month")
    assert budgets(ctx, "01.1")[months.add_months(CM, 1)] == 700


async def test_edit_an_income_budget(user: User, demo, ctx) -> None:
    await open_budget(user)
    row = next(
        lb.parent_slot.parent.parent_slot.parent
        for lb in find_all(user, ui.html)
        if lb.content == "Salary" and lb.props.get("title") == "Salary"
    )
    await click(user, find_one(user, ui.button, within=row, icon="o_edit"))
    dlg = open_dialog(user)
    await set_value(user, find_all(user, ui.number, dlg, label="Amount")[0], 6500)
    await click(user, button(user, "Save", dlg))
    salary = [b for b in ctx.store.budget.all() if b.type == BUDGET_INCOME and b.month == CM]
    assert [b.amount for b in salary] == [6500]
    await user.should_see("6'500.00 still expected of 6'500.00 CHF")


async def test_budget_another_income_category(user: User, demo, ctx) -> None:
    bonus = acc_svc.add_income_category(ctx, "Bonus")
    await open_budget(user)
    await user.should_see("Budget Another Category")
    await menu_item(user, "Bonus")
    dlg = open_dialog(user)
    await set_value(user, find_one(user, ui.number, dlg, label="Amount"), 1000)
    await click(user, button(user, "Save", dlg))
    added = [b for b in ctx.store.budget.all() if b.category == bonus.id]
    assert [(b.month, b.type, b.amount) for b in added] == [(CM, BUDGET_INCOME, 1000)]
    await user.should_see("7'200.00 still expected of 7'200.00 CHF")


async def test_cancel_leaves_the_budget_alone(user: User, demo, ctx) -> None:
    before = len(ctx.store.budget.all())
    await open_budget(user)
    dlg = await edit_budget(user, "05")
    await set_value(user, find_one(user, ui.number, dlg, label="Amount"), 99)
    await click(user, button(user, "Cancel", dlg))
    assert not dlg.value
    assert len(ctx.store.budget.all()) == before
    assert not [b for b in ctx.store.budget.all() if b.type == BUDGET_EXPENDITURE and b.category == "05"]


# ------------------------------------------------------------------ export
@pytest.mark.parametrize(("item", "magic"), [("Export as Excel…", b"PK"), ("Export as PDF…", b"%PDF")])
async def test_statement_export(user: User, demo, item: str, magic: bytes) -> None:
    await open_budget(user, PREV)
    await menu_item(user, item)
    response = await downloaded(user)
    assert response.content.startswith(magic)


async def test_statement_export_failure_is_reported(user: User, demo, monkeypatch) -> None:
    from budget_app.services import export as export_svc  # noqa: PLC0415

    def broken(_rep):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(export_svc, "statement_pdf", broken)
    await open_budget(user)
    await menu_item(user, "Export as PDF…")
    await user.should_see("Export failed: no fonts")
    assert not user.download.http_responses


async def test_overview_error_is_reported(user: User, demo, monkeypatch) -> None:
    await open_budget(user)

    def broken(*_args, **_kwargs):
        raise RuntimeError("corrupt store")

    monkeypatch.setattr(reports, "month_report", broken)
    await click(user, find_one(user, ui.button, icon="chevron_left"))
    await user.should_see("Cannot compute the overview: corrupt store")


# ------------------------------------------------------------------ the page frame
async def test_transaction_from_the_sidebar_refreshes_the_page(user: User, demo, ctx) -> None:
    await open_budget(user)
    await user.should_see("2'750.00 left of 2'750.00 CHF")
    await click(user, button(user, "New Transaction"))
    dlg = open_dialog(user)
    await set_value(user, find_one(user, ui.number, dlg, label="Amount"), 250)
    await set_value(user, find_one(user, ui.select, dlg, label="Category"), "01.1")
    await set_value(user, find_one(user, ui.select, dlg, label="From Account"), demo["chf"].id)
    await click(user, button(user, "Add Transaction", dlg))
    await user.should_see("Transaction added")
    assert not dlg.value
    await user.should_see("2'500.00 left of 2'750.00 CHF")


async def test_exchange_rate_problem_is_shown(user: User, demo, ctx) -> None:
    ctx.fx_service = SimpleNamespace(last_error="The ECB rates could not be downloaded")
    await open_budget(user)
    await user.should_see("The ECB rates could not be downloaded")


class FakeWindow:
    """The pywebview window of the desktop app: a save dialog that answers `chosen`."""

    def __init__(self, chosen) -> None:
        self.chosen = chosen
        self.asked: list[dict] = []

    async def create_file_dialog(self, dialog_type, **kwargs):
        self.asked.append(kwargs)
        return self.chosen


@pytest.fixture
def native(monkeypatch):
    """Pretend the page runs in the app window, which saves files from Python instead of downloading them.

    Install the window once the page is open: building a page in a real window also sets its title.
    """
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(FileDialog=SimpleNamespace(SAVE=30)))

    def use(window: FakeWindow) -> FakeWindow:
        monkeypatch.setattr(app.native, "main_window", window)
        return window

    return use


async def test_native_export_writes_the_chosen_file(user: User, demo, native, tmp_path) -> None:
    target = tmp_path / "out" / "my statement.xlsx"
    target.parent.mkdir()
    await open_budget(user, PREV)
    window = native(FakeWindow([str(target)]))
    await menu_item(user, "Export as Excel…")
    await user.should_see("Saved “my statement.xlsx”")
    assert target.read_bytes().startswith(b"PK")
    assert window.asked[0]["save_filename"] == f"statement_{PREV}_CHF.xlsx"
    assert not user.download.http_responses


async def test_native_export_cancelled(user: User, demo, native, tmp_path) -> None:
    await open_budget(user)
    window = native(FakeWindow(None))
    await menu_item(user, "Export as PDF…")
    for _ in range(20):
        if window.asked:
            break
        await asyncio.sleep(0.05)
    assert window.asked
    await settle()
    assert not any("Saved" in m for m in user.notify.messages)


async def test_native_export_reports_write_errors(user: User, demo, native, tmp_path) -> None:
    await open_budget(user)
    native(FakeWindow(str(tmp_path / "missing-folder" / "statement.pdf")))
    await menu_item(user, "Export as PDF…")
    await user.should_see("Could not save statement.pdf")


async def test_refresh_rates_records_the_download(user: User, ctx) -> None:
    calls = []

    def refresh_if_stale(last):
        calls.append(last)
        return True

    ctx.fx_service = SimpleNamespace(refresh_if_stale=refresh_if_stale, last_error="")
    ctx.store.settings.last_fx_download = "2026-01-01"
    await layout.refresh_rates(ctx)
    assert calls == ["2026-01-01"]
    assert ctx.store.settings.last_fx_download == date.today().isoformat()


async def test_refresh_rates_without_a_service_does_nothing(ctx) -> None:
    ctx.store.settings.last_fx_download = "2026-01-01"
    await layout.refresh_rates(ctx)
    assert ctx.store.settings.last_fx_download == "2026-01-01"


def test_formatting_helpers() -> None:
    assert layout.fmt(None) == "–"
    assert layout.fmt(-1234.5, "CHF") == "−1'234.50 CHF"
    assert layout.fmt_signed(12) == "+12.00"
    assert layout.fmt_signed(0.001) == ""
    assert layout.parse_date("2026-10-19") == date(2026, 10, 19)
    assert layout.parse_date("19.10.2026") is None
    assert layout.parse_date("") is None
