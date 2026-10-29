"""Phone companion (F35): the pairing page, the quick-add page, and the phone row in Settings → Security."""

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from test_lock_page import PW, button, field, open_unlocked, set_value, settle

from budget_app.services import context as context_module
from budget_app.services import phone, security
from budget_app.storage import vault


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No listener and no real addresses in the tests."""
    monkeypatch.setattr(vault, "SCRYPT_N", 2**8)
    monkeypatch.setattr(security, "on_phone_change", None)
    monkeypatch.setattr(phone, "addresses", lambda: ["192.168.1.5"])


def amount(user: User) -> ui.number:
    with user:
        [found] = [e for e in user.client.elements.values() if isinstance(e, ui.number)]
    return found


def phone_on() -> None:
    security.set_password(PW, PW)
    security.set_phone(True)


async def test_the_pairing_page_says_when_phones_are_off(user: User) -> None:
    await user.open("/pair")
    await user.should_see("Quick add from a phone is off.")


async def test_a_phone_pairs_with_the_code_and_adds_an_expense(user: User, ctx, setup) -> None:
    phone_on()
    await user.open("/pair")
    await user.should_see("Type the code shown in Budget on the computer")
    code = security.pairing_code()
    set_value(user, field(user, "Pairing code"), "000000" if code != "000000" else "111111")
    button(user, "Pair").click()
    await settle()
    assert user.notify.contains("Wrong pairing code")
    set_value(user, field(user, "Pairing code"), f"{code[:3]} {code[3:]}")
    button(user, "Pair").click()
    await settle(30)
    await user.should_see("Save Expense")
    with user:
        assert user.client.page.path == "/quick"
    assert security.phone_sessions() == 1
    button(user, "Save Expense").click()
    await settle()
    assert user.notify.contains("Amount")
    set_value(user, amount(user), 12.5)
    with user:
        [category] = [
            e for e in user.client.elements.values() if isinstance(e, ui.select) and e.props.get("label") == "Category"
        ]
        category.value = "01.1"
    set_value(user, field(user, "Payee"), "Migros")
    button(user, "Save Expense").click()
    await settle()
    assert user.notify.contains("Saved 12.50 CHF")
    [tx] = ctx.store.transactions.all()
    assert (tx.amount, tx.currency, tx.category, tx.from_account) == (12.5, "CHF", "01.1", setup["chf"].id)
    await user.should_see("12.50 CHF")
    assert amount(user).value is None


async def test_quick_add_needs_an_open_folder_and_an_account(user: User, ctx, monkeypatch) -> None:
    await user.open("/quick")
    await user.should_see("Add an account in Budget on the computer first.")
    monkeypatch.setattr(context_module, "_ctx", None)
    await user.open("/quick")
    await user.should_see("Unlock Budget on the computer first.")
    monkeypatch.setattr(context_module, "_ctx", ctx)


async def test_a_bad_date_is_refused(user: User, setup) -> None:
    await user.open("/quick")
    set_value(user, field(user, "Date"), "not a date")
    button(user, "Save Expense").click()
    await settle()
    assert user.notify.contains("Choose a date")


async def test_phones_are_turned_on_paired_and_off_in_settings(user: User, ctx) -> None:
    security.set_password(PW, PW)
    await open_unlocked(user, "/settings")
    await user.should_see("Lets a phone on your home network add expenses")
    with user:
        turn_on = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Turn On…"]
    UserInteraction(user, {turn_on[-1]}, "Turn On…").click()
    await user.should_see("The connection is not encrypted, so use it at home only.")
    button(user, "Turn On").click()
    await settle()
    assert security.phone_enabled()
    await user.should_see("On. A phone on your home network opens http://192.168.1.5:8484/pair")
    button(user, "Pair a Phone…").click()
    await user.should_see("The code works once, for ten minutes.")
    code = security.state().pairing.code
    await user.should_see(f"{code[:3]} {code[3:]}")
    with user:
        assert [e for e in user.client.elements.values() if isinstance(e, ui.image) and "pairing-qr" in e.classes]
    button(user, "Done").click()
    await settle()
    button(user, "Turn Off").click()
    await settle()
    assert user.notify.contains("Phones can no longer reach Budget") and not security.phone_enabled()


async def test_without_a_home_network_address_the_sheet_says_so(user: User, monkeypatch) -> None:
    phone_on()
    monkeypatch.setattr(phone, "addresses", list)
    await open_unlocked(user, "/settings")
    await user.should_see("this computer’s address")
    button(user, "Pair a Phone…").click()
    await user.should_see("This computer has no address on a home network right now.")


async def test_another_device_never_gets_the_password_page(user: User, monkeypatch) -> None:
    phone_on()
    monkeypatch.setattr(phone, "admitted", lambda host, path: True)  # past the gate, to see the page's own check
    monkeypatch.setattr(security, "is_local", lambda host: False)
    await user.open("/lock")
    await user.should_see("Type the code shown in Budget on the computer")


async def test_the_page_lists_the_last_few_expenses_it_saved(user: User, ctx, setup) -> None:
    await user.open("/quick")
    with user:
        [category] = [
            e for e in user.client.elements.values() if isinstance(e, ui.select) and e.props.get("label") == "Category"
        ]
    for n in range(1, 8):
        set_value(user, amount(user), n)
        with user:
            category.value = "01.1"
        button(user, "Save Expense").click()
        await settle(2)
    assert len(ctx.store.transactions.all()) == 7
    with user:
        listed = [e.text for e in user.client.elements.values() if isinstance(e, ui.label) and " CHF · " in e.text]
    assert [x.split(" ")[0] for x in listed] == ["3.00", "4.00", "5.00", "6.00", "7.00"]
