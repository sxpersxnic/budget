"""Faster manual entry in the interface (F06)."""

from datetime import date

from nicegui import ui
from nicegui.events import GenericEventArguments
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from test_transaction_form import button, click, field, put, save_button, type_toggle

from budget_app.services import accounts as acc_svc
from budget_app.services import items as items_svc
from budget_app.services import templates as templates_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.amount import AmountInput
from budget_app.ui.widgets.transaction_form import open_transaction_sheet


def key(user: User, keyboard: ui.keyboard, name: str, **mods) -> None:
    args = {
        "action": "keydown",
        "repeat": False,
        "altKey": mods.get("alt", False),
        "ctrlKey": mods.get("ctrl", False),
        "metaKey": mods.get("meta", False),
        "shiftKey": False,
        "key": name,
        "code": f"Key{name.upper()}" if len(name) == 1 else name,
        "location": 0,
    }
    UserInteraction(user, {keyboard}, None).trigger("key", args)


def keyboards(user: User) -> list[ui.keyboard]:
    return sorted(user.find(kind=ui.keyboard).elements, key=lambda k: k.id)


def newest(user: User, kind: type, label: str):
    """The most recently created visible element of `kind` labelled `label` (a new sheet's field)."""
    found = [e for e in user.find(kind=kind).elements if e.props.get("label") == label]
    assert found, f"no {kind.__name__} labelled {label!r}"
    return max(found, key=lambda e: e.id)


def menu_items(user: User) -> list[ui.menu_item]:
    from nicegui import ElementFilter  # noqa: PLC0415

    with user:
        return sorted(ElementFilter(kind=ui.menu_item, local_scope=False), key=lambda m: m.id)


def expense(setup, **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        type="expenditure",
        amount=12.5,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
    )
    base.update(kw)
    return TransactionInput(**base)


async def test_the_amount_field_takes_a_sum(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    amount = field(user, AmountInput, "Amount")
    assert amount.props["type"] == "text"
    with user:
        amount.set_value(
            amount._event_args_to_value(GenericEventArguments(sender=amount, client=amount.client, args="12.50+8.30"))
        )
    assert amount.value == 20.8


async def test_cmd_enter_saves_and_n_opens_a_new_transaction(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, field(user, ui.number, "Amount"), 9)
    put(user, field(user, ui.select, "Category"), "01.1")
    put(user, field(user, ui.select, "From Account"), setup["chf"].id)
    form_keyboard = keyboards(user)[-1]
    key(user, form_keyboard, "Enter", ctrl=False)  # plain Enter does nothing
    assert ctx.store.transactions.all() == []
    key(user, form_keyboard, "Enter", meta=True)
    assert [tx.amount for tx in ctx.store.transactions.all()] == [9]

    page_keyboard = keyboards(user)[0]
    key(user, page_keyboard, "n", ctrl=True)  # with a modifier: not ours
    key(user, page_keyboard, "n")
    await user.should_see("New Transaction")
    assert any(d.value for d in user.find(kind=ui.dialog).elements)


async def test_templates_save_apply_and_delete(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    put(user, field(user, ui.number, "Amount"), 4.5)
    put(user, field(user, ui.select, "Category"), "01.1.1")
    put(user, field(user, ui.select, "From Account"), setup["chf"].id)
    UserInteraction(user, {menu_items(user)[-1]}, None).click()  # "Save as Template…"
    name = field(user, ui.input, "Name")
    put(user, name, "Coffee")
    click(user, button(user, "Save"))
    assert [tp.name for tp in templates_svc.all_templates(ctx)] == ["Coffee"]

    click(user, save_button(user))  # add one, the form resets
    assert field(user, ui.number, "Amount").value is None
    coffee = next(m for m in menu_items(user) if "Coffee" in [getattr(e, "text", "") for e in m.descendants()])
    UserInteraction(user, {coffee}, None).click()
    assert field(user, ui.number, "Amount").value == 4.5
    assert field(user, ui.select, "Category").value == "01.1.1"
    assert field(user, ui.select, "From Account").value == setup["chf"].id

    delete = next(b for b in coffee.descendants() if isinstance(b, ui.button))
    UserInteraction(user, {delete}, None).trigger("click.stop")
    assert templates_svc.all_templates(ctx) == []


async def test_a_template_for_income_and_a_transfer(user: User, ctx, setup) -> None:
    templates_svc.save(
        ctx,
        "Salary",
        TransactionInput(
            type="income", amount=6200, currency="CHF", category=setup["salary"].id, to_account=setup["chf"].id
        ),
    )
    templates_svc.save(
        ctx,
        "Savings",
        TransactionInput(
            type="transfer", amount=500, currency="CHF", from_account=setup["chf"].id, to_account=setup["chf"].id
        ),
    )
    await user.open("/add-transaction")
    salary = next(m for m in menu_items(user) if "Salary" in [getattr(e, "text", "") for e in m.descendants()])
    UserInteraction(user, {salary}, None).click()
    assert type_toggle(user).value == "income"
    assert field(user, ui.select, "To Account").value == setup["chf"].id
    savings = next(m for m in menu_items(user) if "Savings" in [getattr(e, "text", "") for e in m.descendants()])
    UserInteraction(user, {savings}, None).click()
    assert type_toggle(user).value == "transfer"


async def test_saving_a_template_needs_a_name(user: User, ctx, setup) -> None:
    await user.open("/add-transaction")
    UserInteraction(user, {menu_items(user)[-1]}, None).click()
    click(user, button(user, "Save"))
    await user.should_see("Name is required")


async def test_duplicate_from_the_edit_sheet_keeps_the_items(user: User, ctx, setup) -> None:
    tx = tx_svc.save(ctx, expense(setup))
    items_svc.replace(ctx, tx.id, [ItemInput("Bread", 1, 3.5), ItemInput("Milk", 2, 4.5)])
    await user.open("/transactions")
    with user:
        open_transaction_sheet(ctx, editing=tx)
    click(user, button(user, "Duplicate"))
    await user.should_see(kind=AmountInput)
    assert newest(user, ui.number, "Amount").value == 12.5
    add = max((b for b in user.find(kind=ui.button).elements if b.text == "Add Transaction"), key=lambda b: b.id)
    click(user, add)
    copies = [t for t in ctx.store.transactions.all() if t.id != tx.id]
    assert len(copies) == 1 and copies[0].date == date.today()
    assert sorted(i.name for i in items_svc.items_for(ctx, copies[0].id)) == ["Bread", "Milk"]


async def test_duplicate_from_the_list_needs_exactly_one(user: User, ctx, setup) -> None:
    from test_transactions_page import select  # noqa: PLC0415

    tx = tx_svc.save(ctx, expense(setup))
    other = tx_svc.save(ctx, expense(setup, amount=3))
    await user.open("/transactions")
    await select(user, tx.id, other.id)
    click(user, button(user, "Duplicate"))
    await user.should_see("Select exactly one transaction to duplicate")
    await select(user, tx.id)
    click(user, button(user, "Duplicate"))
    await user.should_see(kind=AmountInput)
    assert newest(user, ui.number, "Amount").value == 12.5


async def test_recent_accounts_come_first(user: User, ctx, setup) -> None:
    second = acc_svc.add_account(ctx, "Cash", "CHF")
    tx_svc.save(ctx, expense(setup, from_account=second.id))
    await user.open("/add-transaction")
    options = list(field(user, ui.select, "From Account").options)
    assert options[0] == second.id
