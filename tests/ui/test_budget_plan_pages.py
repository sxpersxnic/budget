"""Budget Plan list (filters, edit, delete) and the New Budget Entry form (page and sheet)."""

import asyncio
import re

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app.domain import months
from budget_app.services import budget as budget_svc
from budget_app.services.budget import BudgetInput
from budget_app.ui.pages.add_budget import FOREVER, until_options


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


# ------------------------------------------------------------------ Budget Plan list
async def test_lists_every_budget_entry(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    await user.should_see("Planned income and spending, month by month")
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
    assert "This can’t be undone." in labels(dlg)
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
    assert user.notify.contains("Select an income category")
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
