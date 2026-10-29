"""Budget Plan: the Series view (one row per budget: change, delete), the Months grid (filters, edit, delete), and
the New Budget Entry form (page and sheet)."""

import asyncio
import re
from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app.domain import months
from budget_app.domain.models import BudgetEntry
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import undo as undo_svc
from budget_app.services.budget import BudgetInput
from budget_app.ui.pages.add_budget import FOREVER, until_options
from budget_app.ui.pages.budget_entries import category_label


# ------------------------------------------------------------------ helpers
def open_dialogs(user: User) -> list[ui.dialog]:
    with user:
        return sorted(
            (e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value), key=lambda d: d.id
        )


def top_dialog(user: User) -> ui.dialog:
    dialogs = open_dialogs(user)
    assert dialogs, "expected an open sheet"
    return dialogs[-1]


def page_root(user: User) -> ui.element:
    return user.client.layout


def field(root: ui.element, kind: type, label: str | None = None):
    found = [e for e in root.descendants() if isinstance(e, kind) and (label is None or e.props.get("label") == label)]
    assert found, f"no {kind.__name__} {label!r}"
    return found[0]


def button(user: User, root: ui.element, text: str) -> UserInteraction:
    found = {e for e in root.descendants() if isinstance(e, ui.button) and e.text == text}
    assert found, f"no button {text!r}"
    return UserInteraction(user, found, text)


def labels(root: ui.element) -> list[str]:
    return [e.text for e in root.descendants() if isinstance(e, ui.label)]


def status_label(root: ui.element) -> ui.label:
    """The status line of the budget form (the only plain caption label in it)."""
    return next(e for e in root.descendants() if isinstance(e, ui.label) and e.classes == ["caption"])


def set_value(user: User, element, value) -> None:
    with user:
        element.value = value


def grid(user: User) -> ui.aggrid:
    with user:
        return next(e for e in user.client.elements.values() if isinstance(e, ui.aggrid))


def rows(user: User) -> list[dict]:
    return grid(user).options["rowData"]


def count_text(user: User) -> str:
    with user:
        return next(
            e.text
            for e in user.client.elements.values()
            if isinstance(e, ui.label) and e.classes == ["caption", "px-1"] and "budget entr" in e.text
        )


def select_rows(user: User, selected: list[dict]) -> None:
    """Answer the grid's getSelectedRows calls with `selected` and announce a selection change."""
    user.javascript_rules[re.compile(r".*getSelectedRows.*", re.DOTALL)] = lambda _: selected
    UserInteraction(user, {grid(user)}, None).trigger("selectionChanged")


async def until(condition, message: str = "condition not met") -> None:
    for _ in range(60):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


def series(ctx, category: str):
    return sorted((b for b in ctx.store.budget.all() if b.category == category), key=lambda b: b.month)


def label(code: str) -> str:
    """A category's name as the plan shows it (so a change to how categories are named needs no test edits)."""
    return category_label(None, BudgetEntry("", "expenditure", code, 0, "", 0, date.today()))  # type: ignore[arg-type]


def series_rows(user: User) -> list[ui.element]:
    with user:
        return sorted(
            (e for e in user.client.elements.values() if "series-row" in e.classes and e.visible), key=lambda e: e.id
        )


def row_texts(row: ui.element) -> list[str]:
    return [e.text for e in row.descendants() if isinstance(e, ui.label)]


def series_row(user: User, name: str) -> ui.element:
    found = [r for r in series_rows(user) if row_texts(r)[0] == name]
    assert found, f"no series row {name!r}"
    return found[0]


def open_row(user: User, name: str) -> ui.dialog:
    """Click a series row's text: it opens the series sheet."""
    main = next(e for e in series_row(user, name).descendants() if "cursor-pointer" in e.classes)
    UserInteraction(user, {main}, None).click()
    return top_dialog(user)


def row_button(user: User, name: str, label: str) -> UserInteraction:
    found = {e for e in series_row(user, name).descendants() if e.props.get("aria-label") == label}
    assert found, f"no {label!r} button on {name!r}"
    return UserInteraction(user, found, label)


def view_toggle(user: User) -> ui.toggle:
    with user:
        return next(e for e in user.client.elements.values() if isinstance(e, ui.toggle))


def months_view(user: User) -> None:
    set_value(user, view_toggle(user), "months")


async def search(user: User, text: str) -> None:
    """Type into the search field and let the refreshed list render (refreshing runs as a task)."""
    set_value(user, search_field(user), text)
    await asyncio.sleep(0.05)


def search_field(user: User) -> ui.input:
    return next(
        e for e in page_root(user).descendants() if isinstance(e, ui.input) and e.props.get("placeholder") == "Search"
    )


def by_class(user: User, cls: str) -> ui.element:
    with user:
        return next(e for e in user.client.elements.values() if cls in e.classes)


# ------------------------------------------------------------------ Budget Plan · Series
async def test_series_view_shows_one_row_per_budget(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    cm, start = months.current_month(), months.add_months(months.current_month(), -6)
    assert view_toggle(user).value == "series"
    assert [row_texts(r)[0] for r in series_rows(user)] == [
        "Salary",
        label("01.1"),
        label("04.1"),
        label("07.3"),
    ]
    since = f"Every month · from {months.label(start)}, forever"
    assert row_texts(series_row(user, "Salary")) == ["Salary", since, "+6'200.00 CHF"]
    assert row_texts(series_row(user, label("01.1")))[1:] == [since, "700.00 CHF"]
    assert "text-pos" in [e for e in series_row(user, "Salary").descendants() if isinstance(e, ui.label)][-1].classes
    await user.should_see(f"+6'200.00 CHF planned for {months.label(cm)}")
    await user.should_see(f"2'750.00 CHF planned for {months.label(cm)}")
    await user.should_see("Income")
    await user.should_see("Expenditure")
    assert len(rows(user)) == len(ctx.store.budget.all())  # the Months grid is there, hidden
    assert not grid(user).parent_slot.parent.visible
    assert not by_class(user, "selection-bar").visible
    with user:
        filters_btn = next(e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Filters")
    assert not filters_btn.visible  # the filters belong to the Months view


async def test_series_captions(user: User, ctx, setup) -> None:
    cm = months.current_month()
    nxt, past = months.add_months(cm, 1), months.add_months(cm, -3)
    budget_svc.create(ctx, BudgetInput(start_month=nxt, end_month=nxt, currency="EUR", amount=50, category="07.3"))
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month=cm,
            end_month=months.add_months(cm, 6),
            recur_months=3,
            type="income",
            currency="CHF",
            amount=100,
            category=setup["salary"].id,
            comment="quarterly bonus",
        ),
    )
    bonus = acc_svc.add_income_category(ctx, "Allowance")
    budget_svc.create(
        ctx,
        BudgetInput(start_month=cm, end_month=cm, type="income", currency="CHF", amount=1, category=bonus.id),
    )
    rent = budget_svc.create(
        ctx, BudgetInput(start_month=past, end_month=nxt, currency="CHF", amount=1900, category="04.1")
    )
    budget_svc.update_entry(ctx, rent[1].id, 2000)
    budget_svc.create(
        ctx,
        BudgetInput(start_month=past, end_month=months.add_months(cm, -1), currency="CHF", amount=5, category="01.1"),
    )
    await user.open("/budget-entries")
    names = [row_texts(r)[0] for r in series_rows(user)]
    assert names == [  # income by name, then expenditure in COICOP order with the ended budget last
        "Allowance",
        "Salary",
        label("04.1"),
        label("07.3"),
        label("01.1"),
    ]
    assert row_texts(series_row(user, "Salary")) == [
        "Salary",
        f"Every quarter · {months.label(cm)} – {months.label(months.add_months(cm, 6))}",
        "quarterly bonus",
        "+100.00 CHF",
    ]
    assert row_texts(series_row(user, label("07.3")))[1:] == [
        f"Once · {months.label(nxt)}",
        "50.00 EUR",
    ]
    assert row_texts(series_row(user, label("04.1")))[1] == (
        f"Every month · {months.label(past)} – {months.label(nxt)} · 1 month differs"
    )
    ended = [e for e in series_row(user, label("01.1")).descendants() if isinstance(e, ui.label)]
    assert "text-2" in ended[0].classes and "text-2" in ended[-1].classes
    await user.should_see(f"+101.00 CHF planned for {months.label(cm)}")

    ctx.store.income_categories.delete([bonus.id])  # a category removed behind the app's back shows its id
    await user.open("/budget-entries")
    assert bonus.id in [row_texts(r)[0] for r in series_rows(user)]


async def test_search_applies_to_both_views(user: User, ctx, demo) -> None:
    cm = months.current_month()
    budget_svc.create(
        ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=5, category="09.4", comment="Café")
    )
    await user.open("/budget-entries")
    await search(user, " cafe ")
    assert [row_texts(r)[0] for r in series_rows(user)] == [label("09.4")]
    assert [r["amount"] for r in rows(user)] == [5]
    await search(user, "salary")
    assert [row_texts(r)[0] for r in series_rows(user)] == ["Salary"]
    await user.should_see(f"+6'200.00 CHF planned for {months.label(cm)}")
    await user.should_not_see("Expenditure")
    await search(user, "no such thing")
    assert series_rows(user) == []
    await user.should_see("Nothing matches these filters.")


async def test_switch_between_series_and_months(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    series_box = series_rows(user)[0].parent_slot.parent.parent_slot.parent.parent_slot.parent
    months_view(user)
    assert grid(user).parent_slot.parent.visible and not series_box.visible
    user.find("Filters").click()
    with user:
        filters = field(page_root(user), ui.select, "From").parent_slot.parent
    assert filters.visible
    set_value(user, view_toggle(user), "series")
    assert series_box.visible and not grid(user).parent_slot.parent.visible
    assert not filters.visible  # the Months filters close with their view


async def test_series_sheet_changes_a_budget_from_this_month(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    cm = months.current_month()
    food = series(ctx, "01.1")
    dlg = open_row(user, label("01.1"))
    assert label("01.1") in labels(dlg)
    assert f"Every month · from {months.label(food[0].month)}, forever" in labels(dlg)
    start, until = field(dlg, ui.select, "From"), field(dlg, ui.select, "Until")
    assert (start.value, until.value, field(dlg, ui.select, "Repeat").value) == (cm, FOREVER, 1)
    assert field(dlg, ui.number, "Amount").value == 700
    assert f"The months before {months.label(cm)} keep their values." in labels(dlg)
    n = len(months.month_range(cm, ctx.horizon()))
    span = f"{months.label(cm)} – {months.label(ctx.horizon())}"
    assert f"{n} months, {span}, continued as the plan grows" in labels(dlg)
    set_value(user, field(dlg, ui.number, "Amount"), 750)
    set_value(user, field(dlg, ui.textarea, "Note"), " more ")
    button(user, dlg, "Save").click()
    await user.should_see("Budget updated")
    assert not open_dialogs(user)
    now = series(ctx, "01.1")
    assert [(b.month, b.amount) for b in now if b.month < cm] == [(b.month, 700) for b in food if b.month < cm]
    assert all((b.amount, b.comment, b.forever) == (750, "more", True) for b in now if b.month >= cm)
    assert undo_svc.latest(ctx).label == "Replan a budget"
    assert row_texts(series_row(user, label("01.1")))[2:] == ["more", "750.00 CHF"]
    await user.should_see(f"2'800.00 CHF planned for {months.label(cm)}")


async def test_series_sheet_replans_the_schedule_from_the_start(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    first = series(ctx, "07.3")[0].month
    dlg = open_row(user, label("07.3"))
    start, until = field(dlg, ui.select, "From"), field(dlg, ui.select, "Until")
    set_value(user, start, first)
    assert not [s for s in labels(dlg) if "keep their values" in s]
    end = months.add_months(first, 6)
    set_value(user, until, end)
    set_value(user, field(dlg, ui.select, "Repeat"), 3)
    assert f"3 months, {months.label(first)} – {months.label(end)}" in labels(dlg)
    set_value(user, start, months.add_months(first, 7))  # past Until: Until must be chosen again
    assert until.value is None and end not in until.options
    assert "Select the final month" in labels(dlg)
    set_value(user, start, first)
    set_value(user, until, end)
    set_value(user, field(dlg, ui.select, "Category"), "07.2")
    set_value(user, field(dlg, ui.select, "Currency"), "EUR")
    button(user, dlg, "Save").click()
    await user.should_see("Budget updated")
    assert series(ctx, "07.3") == []
    moved = series(ctx, "07.2")
    assert [(b.month, b.currency, b.recur_months) for b in moved] == [
        (m, "EUR", 3) for m in (first, months.add_months(first, 3), end)
    ]
    assert row_texts(series_row(user, label("07.2")))[1] == (
        f"Every quarter · {months.label(first)} – {months.label(end)}"
    )


async def test_series_sheet_of_an_ended_budget_starts_at_its_first_month(user: User, ctx, setup) -> None:
    cm = months.current_month()
    past = months.add_months(cm, -4)
    budget_svc.create(
        ctx,
        BudgetInput(start_month=past, end_month=months.add_months(cm, -2), currency="CHF", amount=5, category="01.1"),
    )
    await user.open("/budget-entries")
    dlg = open_row(user, label("01.1"))
    assert field(dlg, ui.select, "From").value == past
    assert field(dlg, ui.select, "Until").value == months.add_months(cm, -2)


async def test_series_sheet_refuses_an_invalid_amount(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    dlg = open_row(user, label("04.1"))
    set_value(user, field(dlg, ui.number, "Amount"), 0)
    assert "Amount must be a positive number" in labels(dlg)
    button(user, dlg, "Save").click()
    await user.should_see("Amount must be a positive number")
    assert open_dialogs(user) == [dlg]
    assert {b.amount for b in series(ctx, "04.1")} == {1900}


async def test_delete_a_budget_with_undo(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    n = len(series(ctx, "07.3"))
    row_button(user, label("07.3"), "Delete").click()
    await until(lambda: open_dialogs(user), "no confirmation")
    dlg = top_dialog(user)
    assert f"Delete the budget for {label('07.3')}?" in labels(dlg)
    assert any(s.startswith(f"{n} months, ") and s.endswith("continued as the plan grows") for s in labels(dlg))
    button(user, dlg, "Delete").click()
    await user.should_see(f"Deleted the budget for {label('07.3')}")
    assert series(ctx, "07.3") == []
    assert label("07.3") not in [row_texts(r)[0] for r in series_rows(user)]
    act = undo_svc.latest(ctx)
    assert act.label == "Delete a budget"
    undo_svc.undo(ctx, act.id)
    assert len(series(ctx, "07.3")) == n


async def test_delete_from_the_series_sheet_and_edit_from_the_row(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    row_button(user, "Salary", "Edit").click()
    dlg = top_dialog(user)
    assert field(dlg, ui.select, "Income category").value == demo["salary"].id
    button(user, dlg, "Delete…").click()
    await until(lambda: "Delete the budget for Salary?" in labels(top_dialog(user)), "no confirmation")
    button(user, top_dialog(user), "Delete").click()
    await user.should_see("Deleted the budget for Salary")
    assert not [b for b in ctx.store.budget.all() if b.type == "income"]


async def test_a_vanished_budget_does_nothing(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    ctx.store.budget.delete([b.id for b in series(ctx, "01.1")])  # removed behind the page's back
    UserInteraction(user, {next(iter(row_button(user, label("01.1"), "Edit").elements))}, None).click()
    row_button(user, label("01.1"), "Delete").click()
    await asyncio.sleep(0.1)
    assert not open_dialogs(user)


async def test_nothing_planned_explains_a_budget_entry(user: User, ctx, setup) -> None:
    await user.open("/budget-entries")
    await user.should_see("Nothing planned yet")
    await user.should_see("A budget entry is an amount you plan to earn or spend in a category", retries=1)
    assert not view_toggle(user).parent_slot.parent.visible
    empty = by_class(user, "group")
    button(user, empty, "New Budget Entry").click()
    dlg = top_dialog(user)
    cm = months.current_month()
    set_value(user, field(dlg, ui.number, "Amount"), 300)
    set_value(user, field(dlg, ui.select, "Category"), "09.1")
    set_value(user, field(dlg, ui.select, "Until"), months.add_months(cm, 2))
    button(user, dlg, "Add to Budget").click()
    await user.should_see("Added 3 budget entries")
    assert not empty.visible and view_toggle(user).parent_slot.parent.visible
    assert [row_texts(r)[0] for r in series_rows(user)] == [label("09.1")]


async def test_propose_from_history_in_the_empty_state(user: User, ctx, setup) -> None:
    await user.open("/budget-entries")
    button(user, by_class(user, "group"), "Propose from History…").click()
    assert "Propose from History" in labels(top_dialog(user))


# ------------------------------------------------------------------ Budget Plan · Months
async def test_lists_every_budget_entry(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    months_view(user)
    await user.should_not_see("Planned income and spending, month by month")
    total = len(ctx.store.budget.all())
    assert len(rows(user)) == total
    assert count_text(user) == f"{total} budget entries"
    salary = next(r for r in rows(user) if r["_type"] == "income")
    assert (salary["category"], salary["type"], salary["amount"]) == ("Salary", "Income", 6200)
    assert salary["recurrence"] == "Every month, forever"
    food = next(r for r in rows(user) if r["category"].startswith("01.1"))
    assert food["type"] == "Expenditure"
    assert food["amount_chf"] == 700


async def test_recurrence_and_missing_income_category_labels(user: User, ctx, setup) -> None:
    cm = months.current_month()
    budget_svc.create(
        ctx,
        BudgetInput(start_month=cm, end_month=cm, recur_months=5, currency="EUR", amount=50, category="07.3"),
    )
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month=cm,
            end_month=months.add_months(cm, 6),
            recur_months=3,
            type="income",
            currency="CHF",
            amount=100,
            category=setup["salary"].id,
            comment="quarterly bonus",
        ),
    )
    ctx.store.income_categories.delete([setup["salary"].id])  # a category removed behind the app's back
    await user.open("/budget-entries")
    assert count_text(user) == "4 budget entries"
    recurrences = {r["recurrence"] for r in rows(user)}
    assert recurrences == {"Every 5 months", "Every quarter"}
    assert {r["category"] for r in rows(user) if r["_type"] == "income"} == {setup["salary"].id}
    eur = next(r for r in rows(user) if r["currency"] == "EUR")
    assert eur["amount_chf"] == 45.0  # 50 EUR at the latest rate 0.90


async def test_one_entry_uses_the_singular(user: User, ctx, setup) -> None:
    cm = months.current_month()
    budget_svc.create(ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=5, category="01.1"))
    await user.open("/budget-entries")
    assert count_text(user) == "1 budget entry"


async def test_filters(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    months_view(user)
    with user:
        filters = field(page_root(user), ui.select, "From").parent_slot.parent
    assert not filters.visible
    user.find("Filters").click()
    assert filters.visible
    root = page_root(user)
    all_rows = ctx.store.budget.all()
    cm = months.current_month()

    set_value(user, field(root, ui.select, "From"), cm)
    assert len(rows(user)) == sum(b.month >= cm for b in all_rows)
    set_value(user, field(root, ui.select, "To"), cm)
    assert len(rows(user)) == 4 and {r["month"] for r in rows(user)} == {cm}
    set_value(user, field(root, ui.select, "From"), None)
    set_value(user, field(root, ui.select, "To"), None)

    set_value(user, field(root, ui.number, "Min amount"), 1000)
    assert {r["amount"] for r in rows(user)} == {1900, 6200}
    set_value(user, field(root, ui.number, "Max amount"), 2000)
    assert {r["amount"] for r in rows(user)} == {1900}
    set_value(user, field(root, ui.number, "Min amount"), None)
    set_value(user, field(root, ui.number, "Max amount"), None)

    set_value(user, field(root, ui.select, "Type"), "income")
    assert {r["_type"] for r in rows(user)} == {"income"}
    assert count_text(user) == f"{sum(b.type == 'income' for b in all_rows)} budget entries"
    set_value(user, field(root, ui.select, "Type"), None)

    search = next(e for e in root.descendants() if isinstance(e, ui.input) and e.props.get("placeholder") == "Search")
    set_value(user, search, "  SALARY ")
    assert {r["category"] for r in rows(user)} == {"Salary"}
    set_value(user, search, "no such thing")
    assert rows(user) == []
    assert count_text(user) == "0 budget entries"

    user.find("Filters").click()
    assert not filters.visible


async def test_search_finds_notes(user: User, ctx, setup) -> None:
    cm = months.current_month()
    budget_svc.create(
        ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=5, category="01.1", comment="Market")
    )
    budget_svc.create(ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=6, category="01.1"))
    await user.open("/budget-entries")
    search = next(
        e for e in page_root(user).descendants() if isinstance(e, ui.input) and e.props.get("placeholder") == "Search"
    )
    set_value(user, search, "market")
    assert [r["amount"] for r in rows(user)] == [5]


async def test_double_click_edits_one_month(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    food = series(ctx, "01.1")
    entry = food[2]
    UserInteraction(user, {grid(user)}, None).trigger("rowDoubleClicked", {"data": {"id": entry.id}})
    dlg = top_dialog(user)
    assert f"Budget for {months.label(entry.month)}" in labels(dlg)
    switch = field(dlg, ui.switch)
    assert switch.text == f"Also change the {len(food) - 3} later months of this series"
    set_value(user, field(dlg, ui.number, "Amount"), 750)
    set_value(user, field(dlg, ui.textarea, "Note"), "  more  ")
    button(user, dlg, "Save").click()
    await user.should_see("Budget updated")
    changed = ctx.store.budget.get(entry.id)
    assert (changed.amount, changed.comment) == (750, "more")
    assert all(b.amount == 700 for b in series(ctx, "01.1") if b.id != entry.id)
    assert not open_dialogs(user)
    await until(lambda: any(r["amount"] == 750 for r in rows(user)), "grid not refreshed")


async def test_edit_can_change_the_later_months_too(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    food = series(ctx, "01.1")
    entry = food[-3]
    UserInteraction(user, {grid(user)}, None).trigger("rowDoubleClicked", {"data": {"id": entry.id}})
    dlg = top_dialog(user)
    assert field(dlg, ui.switch).text == "Also change the 2 later months of this series"
    UserInteraction(user, {field(dlg, ui.switch)}, None).click()
    set_value(user, field(dlg, ui.select, "Category"), "01.2")
    set_value(user, field(dlg, ui.select, "Currency"), "EUR")
    set_value(user, field(dlg, ui.number, "Amount"), 80)
    button(user, dlg, "Save").click()
    updated = series(ctx, "01.2")
    assert [b.id for b in updated] == [b.id for b in food[-3:]]
    assert all((b.amount, b.currency) == (80, "EUR") for b in updated)
    assert all(b.amount == 700 for b in series(ctx, "01.1"))


async def test_edit_replans_the_series_with_until_and_repeat(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    food = series(ctx, "01.1")
    entry = food[2]
    UserInteraction(user, {grid(user)}, None).trigger("rowDoubleClicked", {"data": {"id": entry.id}})
    dlg = top_dialog(user)
    until, repeat = field(dlg, ui.select, "Until"), field(dlg, ui.select, "Repeat")
    assert (until.value, repeat.value) == ("forever", 1)
    hint = next(e for e in dlg.descendants() if isinstance(e, ui.label) and "replans this budget" in e.text)
    assert not hint.visible
    end = months.add_months(entry.month, 6)
    set_value(user, until, end)
    set_value(user, repeat, 3)
    assert hint.visible and not field(dlg, ui.switch).visible  # a replan always covers the later months
    set_value(user, field(dlg, ui.select, "Category"), "01.2")
    set_value(user, field(dlg, ui.number, "Amount"), 900)
    button(user, dlg, "Save").click()
    await user.should_see("Budget updated")
    replanned = series(ctx, "01.2")
    assert [b.month for b in replanned] == [entry.month, months.add_months(entry.month, 3), end]
    assert all(b.amount == 900 and not b.forever for b in replanned)
    assert [b.month for b in series(ctx, "01.1")] == [b.month for b in food[:2]]  # earlier months stay


async def test_edit_refuses_an_invalid_amount(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    entry = series(ctx, "04.1")[0]
    UserInteraction(user, {grid(user)}, None).trigger("rowDoubleClicked", {"data": {"id": entry.id}})
    dlg = top_dialog(user)
    set_value(user, field(dlg, ui.number, "Amount"), 0)
    button(user, dlg, "Save").click()
    await user.should_see("Amount must be a positive number")
    assert open_dialogs(user) == [dlg]
    assert ctx.store.budget.get(entry.id).amount == 1900


async def test_edit_income_entry_without_later_months(user: User, ctx, setup) -> None:
    cm = months.current_month()
    [entry] = budget_svc.create(
        ctx,
        BudgetInput(
            start_month=cm, end_month=cm, type="income", currency="CHF", amount=10, category=setup["salary"].id
        ),
    )
    await user.open("/budget-entries")
    UserInteraction(user, {grid(user)}, None).trigger("rowDoubleClicked", {"data": {"id": entry.id}})
    dlg = top_dialog(user)
    assert field(dlg, ui.select, "Income category").value == setup["salary"].id
    assert not [e for e in dlg.descendants() if isinstance(e, ui.switch)]
    set_value(user, field(dlg, ui.number, "Amount"), 12.5)
    button(user, dlg, "Save").click()
    assert ctx.store.budget.get(entry.id).amount == 12.5


async def test_double_click_on_a_vanished_entry_does_nothing(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    UserInteraction(user, {grid(user)}, None).trigger("rowDoubleClicked", {"data": {"id": "gone"}})
    assert not open_dialogs(user)


async def test_selection_bar_edit_and_delete(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    months_view(user)
    food = series(ctx, "01.1")
    picked = [{"id": food[0].id}, {"id": food[1].id}]
    select_rows(user, picked)
    await user.should_see("2 selected")

    user.find("Edit").click()
    await user.should_see("Select exactly one budget entry to edit")
    assert not open_dialogs(user)

    user.find("Delete").click()
    await until(lambda: open_dialogs(user), "no confirmation")
    dlg = top_dialog(user)
    assert "Delete 2 budget entries?" in labels(dlg)
    assert "This can’t be undone." not in labels(dlg)  # it can (F28)
    before = len(ctx.store.budget.all())
    button(user, dlg, "Delete").click()
    await user.should_see("Deleted 2 budget entries")
    assert len(ctx.store.budget.all()) == before - 2
    assert ctx.store.budget.get(food[0].id) is None
    assert len(rows(user)) == before - 2

    select_rows(user, [{"id": food[2].id}])
    await user.should_see("1 selected")
    user.find("Edit").click()
    await until(lambda: open_dialogs(user), "no editor")
    assert f"Budget for {months.label(food[2].month)}" in labels(top_dialog(user))


async def test_delete_one_entry_and_delete_without_selection(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    months_view(user)
    entry = series(ctx, "07.3")[0]
    select_rows(user, [{"id": entry.id}])
    await user.should_see("1 selected")
    user.find("Delete").click()
    await until(lambda: open_dialogs(user), "no confirmation")
    dlg = top_dialog(user)
    assert "Delete 1 budget entry?" in labels(dlg)
    button(user, dlg, "Delete").click()
    await user.should_see("Deleted 1 budget entry")

    select_rows(user, [])
    await asyncio.sleep(0.1)
    with user:
        bar = next(e for e in user.client.elements.values() if "selection-bar" in e.classes)
    assert not bar.visible
    UserInteraction(
        user, {b for b in bar.descendants() if isinstance(b, ui.button) and b.text == "Delete"}, None
    ).click()
    await asyncio.sleep(0.1)
    assert not open_dialogs(user)


async def test_new_budget_entry_sheet_adds_and_refreshes(user: User, ctx, setup) -> None:
    await user.open("/budget-entries")
    assert count_text(user) == "0 budget entries"
    user.find("New Budget Entry").click()
    dlg = top_dialog(user)
    assert "New Budget Entry" in labels(dlg)
    cm = months.current_month()
    set_value(user, field(dlg, ui.number, "Amount"), 300)
    set_value(user, field(dlg, ui.select, "Category"), "09.1")
    set_value(user, field(dlg, ui.select, "Until"), months.add_months(cm, 2))
    button(user, dlg, "Add to Budget").click()
    await user.should_see("Added 3 budget entries")
    assert not open_dialogs(user)
    assert count_text(user) == "3 budget entries"


async def test_new_budget_entry_sheet_cancel(user: User, ctx, setup) -> None:
    await user.open("/budget-entries")
    user.find("New Budget Entry").click()
    dlg = top_dialog(user)
    button(user, dlg, "Cancel").click()
    assert not open_dialogs(user)
    assert ctx.store.budget.all() == []


# ------------------------------------------------------------------ New Budget Entry page
async def test_form_starts_quiet_and_explains_problems_once_touched(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    save = button(user, root, "Add to Budget")
    status = status_label(root)
    assert status.text == ""  # nothing entered yet: no error shown
    assert not next(iter(save.elements)).enabled
    assert any(f"Budgets can run until {months.label(ctx.horizon())}." in s for s in labels(root))
    set_value(user, field(root, ui.number, "Amount"), 20)
    assert status.text == "Select an expenditure category"
    set_value(user, field(root, ui.select, "Category"), "01.1")
    assert status.text == "Select the final month"
    set_value(user, field(root, ui.select, "From"), None)
    assert status.text == "Select the first month"


async def test_form_adds_a_forever_series(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    cm = months.current_month()
    horizon = ctx.horizon()
    until_sel = field(root, ui.select, "Until")
    assert next(iter(until_sel.options)) == FOREVER
    set_value(user, field(root, ui.number, "Amount"), 45.5)
    set_value(user, field(root, ui.select, "Category"), "07.3")
    set_value(user, until_sel, FOREVER)
    n = len(months.month_range(cm, horizon))
    status = status_label(root)
    assert status.text == (f"{n} months, {months.label(cm)} – {months.label(horizon)}, continued as the plan grows")
    set_value(user, field(root, ui.textarea, "Note"), " bus pass ")
    button(user, root, "Add to Budget").click()
    await user.should_see(f"Added {n} budget entries")
    rows_ = series(ctx, "07.3")
    assert len(rows_) == n
    assert all(b.forever and b.amount == 45.5 and b.comment == "bus pass" for b in rows_)
    assert rows_[0].generated_until == horizon
    # the form is ready for the next entry
    assert field(root, ui.number, "Amount").value is None
    assert field(root, ui.select, "Category").value is None
    assert field(root, ui.textarea, "Note").value == ""
    assert status.text == ""


async def test_form_repeat_every_quarter_until_a_month(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    cm = months.current_month()
    set_value(user, field(root, ui.number, "Amount"), 90)
    set_value(user, field(root, ui.select, "Category"), "04.5")
    set_value(user, field(root, ui.select, "Until"), months.add_months(cm, 6))
    set_value(user, field(root, ui.select, "Repeat"), 3)
    status = status_label(root)
    assert status.text == f"3 months, {months.label(cm)} – {months.label(months.add_months(cm, 6))}"
    set_value(user, field(root, ui.select, "Until"), cm)
    assert status.text == f"1 month, {months.label(cm)}"
    set_value(user, field(root, ui.select, "Until"), months.add_months(cm, 6))
    button(user, root, "Add to Budget").click()
    await user.should_see("Added 3 budget entries")
    assert [b.month for b in series(ctx, "04.5")] == [cm, months.add_months(cm, 3), months.add_months(cm, 6)]
    assert all(b.recur_months == 3 and not b.forever for b in series(ctx, "04.5"))


async def test_form_one_month_uses_the_singular(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    cm = months.current_month()
    set_value(user, field(root, ui.number, "Amount"), 9)
    set_value(user, field(root, ui.select, "Category"), "TAX")
    set_value(user, field(root, ui.select, "Until"), cm)
    button(user, root, "Add to Budget").click()
    await user.should_see("Added 1 budget entry")
    assert [b.category for b in ctx.store.budget.all()] == ["TAX"]


async def test_moving_the_start_past_the_end_clears_the_end(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    cm = months.current_month()
    until_sel = field(root, ui.select, "Until")
    set_value(user, until_sel, months.add_months(cm, 1))
    set_value(user, field(root, ui.select, "From"), months.add_months(cm, 3))
    assert until_sel.value is None
    assert months.add_months(cm, 1) not in until_sel.options
    set_value(user, until_sel, FOREVER)
    set_value(user, field(root, ui.select, "From"), months.add_months(cm, 4))
    assert until_sel.value == FOREVER  # 'Forever' stays valid for any start


async def test_form_income_entry(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    exp_cat = field(root, ui.select, "Category")
    inc_cat = field(root, ui.select, "Income category")
    assert exp_cat.visible and not inc_cat.visible
    set_value(user, field(root, ui.toggle), "income")
    assert inc_cat.visible and not exp_cat.visible
    set_value(user, field(root, ui.number, "Amount"), 6000)
    status = status_label(root)
    assert status.text == "Select an income category"
    set_value(user, inc_cat, setup["salary"].id)
    set_value(user, field(root, ui.select, "Currency"), "EUR")
    set_value(user, field(root, ui.select, "Until"), months.current_month())
    button(user, root, "Add to Budget").click()
    await user.should_see("Added 1 budget entry")
    [b] = ctx.store.budget.all()
    assert (b.type, b.category, b.currency) == ("income", setup["salary"].id, "EUR")


async def test_form_overwrite_replaces_existing_months(user: User, ctx, setup) -> None:
    cm = months.current_month()
    nxt = months.add_months(cm, 1)
    budget_svc.create(ctx, BudgetInput(start_month=cm, end_month=nxt, currency="CHF", amount=100, category="01.1"))
    await user.open("/add-budget")
    root = page_root(user)
    set_value(user, field(root, ui.number, "Amount"), 150)
    set_value(user, field(root, ui.select, "Category"), "01.1")
    set_value(user, field(root, ui.select, "Until"), cm)
    overwrite = field(root, ui.switch)
    assert overwrite.text == "Replace existing budget for this category in these months"
    UserInteraction(user, {overwrite}, None).click()
    button(user, root, "Add to Budget").click()
    await user.should_see("Added 1 budget entry")
    assert [(b.month, b.amount) for b in series(ctx, "01.1")] == [(cm, 150), (nxt, 100)]
    assert overwrite.value is False  # reset for the next entry


async def test_form_without_overwrite_adds_alongside(user: User, ctx, setup) -> None:
    cm = months.current_month()
    budget_svc.create(ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=100, category="01.1"))
    await user.open("/add-budget")
    root = page_root(user)
    set_value(user, field(root, ui.number, "Amount"), 150)
    set_value(user, field(root, ui.select, "Category"), "01.1")
    set_value(user, field(root, ui.select, "Until"), cm)
    button(user, root, "Add to Budget").click()
    await user.should_see("Added 1 budget entry")
    assert sorted(b.amount for b in series(ctx, "01.1")) == [100, 150]


async def test_form_reports_a_problem_found_when_saving(user: User, ctx, setup) -> None:
    await user.open("/add-budget")
    root = page_root(user)
    set_value(user, field(root, ui.toggle), "income")
    set_value(user, field(root, ui.number, "Amount"), 10)
    set_value(user, field(root, ui.select, "Income category"), setup["salary"].id)
    set_value(user, field(root, ui.select, "Until"), months.current_month())
    ctx.store.income_categories.delete([setup["salary"].id])  # removed elsewhere after the form checked it
    button(user, root, "Add to Budget").click()
    await user.should_see("Select an income category")
    assert notified(user, "Select an income category")
    assert ctx.store.budget.all() == []


async def test_form_warns_when_the_horizon_has_passed(user: User, ctx, setup) -> None:
    past = months.add_months(months.current_month(), -2)
    ctx.store.settings.budget_horizon = past
    await user.open("/add-budget")
    root = page_root(user)
    assert any(
        s == f"Budgets can run until {months.label(past)}. Change this in Settings. "
        "That month has passed, so extend it first."
        for s in labels(root)
    )
    set_value(user, field(root, ui.number, "Amount"), 10)
    set_value(user, field(root, ui.select, "Category"), "01.1")
    set_value(user, field(root, ui.select, "Until"), FOREVER)
    assert status_label(root).text == (  # B13: the reason, not "final month before the first month"
        f"The plan ends in {months.label(past)}, before the first month. Extend it in Settings."
    )


def test_until_options_lists_forever_first_and_keeps_a_stored_month() -> None:
    opts = until_options("2026-10", "2026-12", extra="2027-05")
    assert list(opts) == [FOREVER, "2026-10", "2026-11", "2026-12", "2027-05"]
    assert opts["2027-05"] == "May 2027"
    assert list(until_options("2026-10", "2026-11", extra="2026-11")) == [FOREVER, "2026-10", "2026-11"]
