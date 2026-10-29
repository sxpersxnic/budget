"""Settings page: language, plan horizon, display currency, accounts and income categories."""

import asyncio
from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app import i18n
from budget_app.domain import months
from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR, PROJECTION_BUDGET, PROJECTION_HIGHER
from budget_app.services import accounts as acc_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store


# ------------------------------------------------------------------ helpers
def top_dialog(user: User) -> ui.dialog:
    """The most recently opened sheet that is still open."""
    with user:
        dialogs = [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]
    assert dialogs, "expected an open sheet"
    return max(dialogs, key=lambda d: d.id)


def dialog_button(user: User, text: str) -> UserInteraction:
    dlg = top_dialog(user)
    buttons = {e for e in dlg.descendants() if isinstance(e, ui.button) and e.text == text}
    assert buttons, f"no button {text!r} in the open sheet"
    return UserInteraction(user, buttons, text)


def dialog_field(user: User, kind: type, label: str | None = None):
    dlg = top_dialog(user)
    found = [e for e in dlg.descendants() if type(e) is kind and (label is None or e.props.get("label") == label)]
    assert found, f"no {kind.__name__} {label!r} in the open sheet"
    return found[0]


def dialog_texts(user: User) -> list[str]:
    return [e.text for e in top_dialog(user).descendants() if isinstance(e, ui.label)]


def row_button(user: User, name: str, icon: str) -> UserInteraction:
    """The edit/delete button in the settings row that shows `name`."""
    with user:
        labels = [
            e
            for e in user.client.elements.values()
            if isinstance(e, ui.label) and e.text == name and "group-row" not in e.classes
        ]
    assert labels, f"no row {name!r}"
    row = labels[0]
    while "group-row" not in row.classes:
        row = row.parent_slot.parent
    buttons = {b for b in row.descendants() if isinstance(b, ui.button) and b.props.get("icon") == icon}
    assert buttons, f"no {icon} button in row {name!r}"
    return UserInteraction(user, buttons, None)


async def no_label(user: User, text: str) -> None:
    """Wait until no label shows exactly `text` (refreshes run in the background)."""
    for _ in range(40):
        with user:
            if not any(isinstance(e, ui.label) and e.text == text for e in user.client.elements.values()):
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"still showing {text!r}")


def set_value(user: User, element, value) -> None:
    with user:
        element.value = value


def account_named(ctx, name: str):
    return next((a for a in ctx.store.accounts.all() if a.name == name), None)


@pytest.fixture(autouse=True)
def english():
    yield
    i18n.set_language("en")


# ------------------------------------------------------------------ overview
async def test_rows_show_balances_and_outstanding_advances(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 1234.5)
    adv = acc_svc.add_account(ctx, "Flatmates", "CHF", ACCOUNT_ADVANCE)
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 10),
            type="expenditure",
            amount=100,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            is_advance=True,
            advance_mode="percent",
            advance_value=40,
            advance_account=adv.id,
            debtor="Anna",
        ),
    )
    await user.open("/settings")
    await user.should_see("Settings")
    await user.should_see("1'234.50 CHF at the end of Sep 2026")
    await user.should_see("No balance recorded")  # the euro account has none
    await user.should_see("Flatmates")
    await user.should_see("40.00 CHF outstanding")
    await user.should_see("Salary")
    await user.should_see("Base Currency")
    await user.should_see("Every amount is also stored in this currency. It can’t be changed.")


# ------------------------------------------------------------------ accounts
async def test_add_account(user: User, ctx) -> None:
    await user.open("/settings")
    user.find("Add Account…").click()
    assert "Every transaction on this account will be in this currency." in dialog_texts(user)
    name = dialog_field(user, ui.input, "Name")
    set_value(user, name, "  Savings  ")
    set_value(user, dialog_field(user, ui.select, "Currency"), "USD")
    dialog_button(user, "Add").click()
    acc = account_named(ctx, "Savings")
    assert acc is not None
    assert (acc.currency, acc.kind) == ("USD", ACCOUNT_REGULAR)
    await user.should_see("Savings")
    await user.should_see("No balance recorded")
    # the sheet closed after saving
    with user:
        assert not any(isinstance(e, ui.dialog) and e.value for e in user.client.elements.values())


async def test_add_account_with_enter_key(user: User, ctx) -> None:
    await user.open("/settings")
    user.find("Add Account…").click()
    name = dialog_field(user, ui.input, "Name")
    set_value(user, name, "Wallet")
    UserInteraction(user, {name}, None).trigger("keydown.enter")
    assert account_named(ctx, "Wallet").currency == "CHF"  # the display currency is preselected


async def test_add_account_refuses_empty_and_duplicate_names(user: User, ctx, setup) -> None:
    await user.open("/settings")
    user.find("Add Account…").click()
    dialog_button(user, "Add").click()
    await user.should_see("Name is required")
    set_value(user, dialog_field(user, ui.input, "Name"), "bank")
    dialog_button(user, "Add").click()
    await user.should_see("An account 'bank' in CHF already exists")
    assert len(ctx.store.accounts.all()) == 2
    dialog_button(user, "Cancel").click()
    with user:
        assert not any(isinstance(e, ui.dialog) and e.value for e in user.client.elements.values())


async def test_add_advance_payment_account(user: User, ctx) -> None:
    await user.open("/settings")
    await user.should_see("Advance Payment Accounts")
    user.find("Add Advance Payment Account…").click()
    assert "New Advance Payment Account" in dialog_texts(user)
    set_value(user, dialog_field(user, ui.input, "Name"), "Club")
    set_value(user, dialog_field(user, ui.select, "Currency"), "EUR")
    dialog_button(user, "Add").click()
    acc = account_named(ctx, "Club")
    assert (acc.kind, acc.currency) == (ACCOUNT_ADVANCE, "EUR")
    await user.should_see("0.00 EUR outstanding")


async def test_edit_account_name_and_currency(user: User, ctx, setup) -> None:
    await user.open("/settings")
    row_button(user, "Euro account", "o_edit").click()
    assert "Edit Account" in dialog_texts(user)
    cur = dialog_field(user, ui.select, "Currency")
    assert cur.enabled and cur.value == "EUR"
    set_value(user, dialog_field(user, ui.input, "Name"), "Dollar account")
    set_value(user, cur, "USD")
    dialog_button(user, "Save").click()
    acc = ctx.store.accounts.get(setup["eur"].id)
    assert (acc.name, acc.currency) == ("Dollar account", "USD")
    await user.should_see("Dollar account")
    await no_label(user, "Euro account")


async def test_edit_account_in_use_keeps_its_currency(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 500)
    await user.open("/settings")
    row_button(user, "Bank", "o_edit").click()
    # a recorded balance is enough to lock the currency, and the caption says so (B11)
    assert "The currency can’t change while transactions or recorded balances use this account." in dialog_texts(user)
    assert not dialog_field(user, ui.select, "Currency").enabled
    set_value(user, dialog_field(user, ui.input, "Name"), "Main bank")
    dialog_button(user, "Save").click()
    acc = ctx.store.accounts.get(setup["chf"].id)
    assert (acc.name, acc.currency) == ("Main bank", "CHF")


async def test_edit_advance_account_title(user: User, ctx) -> None:
    acc_svc.add_account(ctx, "Club", "CHF", ACCOUNT_ADVANCE)
    await user.open("/settings")
    row_button(user, "Club", "o_edit").click()
    assert "Edit Advance Payment Account" in dialog_texts(user)


async def test_delete_unused_account_removes_its_balances(user: User, ctx, setup) -> None:
    wealth_svc.upsert_entry(ctx, setup["eur"].id, "2026-09", 900)
    await user.open("/settings")
    row_button(user, "Euro account", "o_delete").click()
    assert "Delete “Euro account”?" in dialog_texts(user)
    assert any("Accounts with transactions can’t be deleted." in s for s in dialog_texts(user))
    dialog_button(user, "Delete").click()
    await user.should_see("Deleted Euro account")
    assert ctx.store.accounts.get(setup["eur"].id) is None
    assert not ctx.store.account_entries.where(account_id=setup["eur"].id)
    await no_label(user, "Euro account")


async def test_delete_cancelled_keeps_the_account(user: User, ctx, setup) -> None:
    await user.open("/settings")
    row_button(user, "Euro account", "o_delete").click()
    dialog_button(user, "Cancel").click()
    assert ctx.store.accounts.get(setup["eur"].id) is not None


async def test_account_with_transactions_cannot_be_deleted(user: User, ctx, demo) -> None:
    await user.open("/settings")
    row_button(user, "Bank", "o_delete").click()
    dialog_button(user, "Delete").click()
    await user.should_see("Accounts with transactions cannot be deleted")
    assert ctx.store.accounts.get(demo["chf"].id) is not None
    await user.should_see("Bank")


# ------------------------------------------------------------------ income categories
async def test_add_income_category(user: User, ctx) -> None:
    await user.open("/settings")
    user.find("Add Category…").click()
    assert "New Income Category" in dialog_texts(user)
    set_value(user, dialog_field(user, ui.input, "Name"), "Bonus")
    dialog_button(user, "Add").click()
    assert [c.name for c in ctx.store.income_categories.all()] == ["Bonus"]
    await user.should_see("Bonus")


async def test_add_income_category_refuses_duplicates(user: User, ctx, setup) -> None:
    await user.open("/settings")
    user.find("Add Category…").click()
    name = dialog_field(user, ui.input, "Name")
    set_value(user, name, "salary")
    UserInteraction(user, {name}, None).trigger("keydown.enter")
    await user.should_see("Income category 'salary' already exists")
    assert len(ctx.store.income_categories.all()) == 1


async def test_rename_income_category(user: User, ctx, setup) -> None:
    await user.open("/settings")
    row_button(user, "Salary", "o_edit").click()
    assert "Rename Income Category" in dialog_texts(user)
    name = dialog_field(user, ui.input, "Name")
    assert name.value == "Salary"
    set_value(user, name, "")
    dialog_button(user, "Save").click()
    await user.should_see("Name is required")
    set_value(user, name, "Wage")
    dialog_button(user, "Save").click()
    assert ctx.store.income_categories.get(setup["salary"].id).name == "Wage"
    await user.should_see("Wage")


async def test_delete_unused_income_category(user: User, ctx, setup) -> None:
    await user.open("/settings")
    row_button(user, "Salary", "o_delete").click()
    assert "Categories that are in use can’t be deleted." in dialog_texts(user)
    dialog_button(user, "Delete").click()
    assert ctx.store.income_categories.all() == []
    assert notified(user, "Deleted Salary")  # like deleting an account (B12)
    await no_label(user, "Salary")


async def test_used_income_category_cannot_be_deleted(user: User, ctx, demo) -> None:
    await user.open("/settings")
    row_button(user, "Salary", "o_delete").click()
    dialog_button(user, "Delete").click()
    await user.should_see("Income categories with entries cannot be deleted")
    assert ctx.store.income_categories.get(demo["salary"].id) is not None


# ------------------------------------------------------------------ budget settings
def _page_select(user: User, value):
    """The select on the page (not in a sheet) whose current value is `value`."""
    with user:
        found = [e for e in user.client.elements.values() if isinstance(e, ui.select) and e.value == value]
    assert found, f"no select with value {value!r}"
    return found[0]


async def test_display_currency_is_saved(user: User, ctx, tmp_path) -> None:
    await user.open("/settings")
    await user.should_see("Display Currency")
    set_value(user, _page_select(user, "CHF"), "EUR")
    await user.should_see("Showing amounts in EUR")
    assert ctx.store.settings.display_currency == "EUR"
    assert Store(tmp_path).settings.display_currency == "EUR"  # persisted


async def test_extending_the_horizon_continues_forever_series(user: User, ctx, demo) -> None:
    await user.open("/settings")
    horizon = ctx.horizon()
    before = len(ctx.store.budget.all())
    sel = _page_select(user, horizon)
    with user:
        apply_btn = next(e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Apply")
    assert not apply_btn.visible
    target = months.add_months(horizon, 3)
    set_value(user, sel, target)
    assert apply_btn.visible
    set_value(user, sel, horizon)  # back to the current horizon: nothing to apply
    assert not apply_btn.visible
    set_value(user, sel, target)
    user.find("Apply").click()
    # four series (three expenditure, one income) run forever: three more months each
    await user.should_see(f"Planning until {months.label(target)}, 12 budget entries added")
    assert len(ctx.store.budget.all()) == before + 12
    assert ctx.store.settings.budget_horizon == target
    assert not apply_btn.visible


async def test_shrinking_the_horizon_adds_nothing(user: User, ctx, demo) -> None:
    await user.open("/settings")
    horizon = ctx.horizon()
    before = len(ctx.store.budget.all())
    target = months.add_months(horizon, -2)
    set_value(user, _page_select(user, horizon), target)
    user.find("Apply").click()
    await user.should_see(f"Planning until {months.label(target)}")
    assert not notified(user, "added")
    assert len(ctx.store.budget.all()) == before  # entries past the new horizon stay
    assert ctx.horizon() == target


async def test_extending_by_one_month_uses_the_singular(user: User, ctx, setup) -> None:
    from budget_app.services import budget as budget_svc  # noqa: PLC0415
    from budget_app.services.budget import BudgetInput  # noqa: PLC0415

    await user.open("/settings")
    horizon = ctx.horizon()
    budget_svc.create(ctx, BudgetInput(start_month=horizon, forever=True, currency="CHF", amount=10, category="01.1"))
    target = months.add_months(horizon, 1)
    set_value(user, _page_select(user, horizon), target)
    user.find("Apply").click()
    await user.should_see(f"Planning until {months.label(target)}, 1 budget entry added")


async def test_horizon_past_the_selectable_range_is_offered(user: User, ctx) -> None:
    far = months.add_months(months.current_month(), 12 * 20)
    ctx.store.settings.budget_horizon = far
    await user.open("/settings")
    sel = _page_select(user, far)
    assert sel.options[far] == months.label(far)


async def test_switching_the_language_is_saved_and_reloads(user: User, ctx, tmp_path) -> None:
    await user.open("/settings")
    sel = _page_select(user, "")
    assert sel.options[""].startswith("System (")
    set_value(user, sel, "de")
    assert ctx.store.settings.language == "de"
    assert Store(tmp_path).settings.language == "de"
    assert i18n.language() == "de"
    await user.should_see("Einstellungen")  # the reloaded page is German


async def test_projection_rule_is_chosen_in_settings(user: User, ctx) -> None:
    """The owner's choice for months without balances: F47's rule by default, or the budgets alone."""
    await user.open("/settings")
    await user.should_see("Projected Wealth")
    rule = user.find(marker="projection-rule").elements.pop()
    assert rule.value == PROJECTION_HIGHER and set(rule.options) == {PROJECTION_BUDGET, PROJECTION_HIGHER}
    rule.set_value(PROJECTION_BUDGET)
    assert ctx.store.settings.projection == PROJECTION_BUDGET
    assert Store(ctx.store.data_dir).settings.projection == PROJECTION_BUDGET  # saved with the settings
    assert notified(user, "Projected wealth: Budget only")
