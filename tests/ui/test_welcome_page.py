"""Welcome screen (F30): the first start, language, currency, the two ways in, and the sample data in Settings."""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified

from budget_app import i18n
from budget_app.services import demo as demo_svc
from budget_app.services.context import ValidationError
from budget_app.storage.store import Store


@pytest.fixture(autouse=True)
def english():
    yield
    i18n.set_language("en")


@pytest.fixture
def first_run(ctx):
    """The state the app starts in: an empty folder and the welcome screen not passed yet."""
    ctx.store.settings.onboarding_done = False
    return ctx


def button(user: User, text: str) -> ui.button:
    with user:
        found = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == text]
    assert found, f"no button {text!r}"
    return found[0]


def dialog_button(user: User, text: str) -> UserInteraction:
    """The button with exactly `text` in the open sheet (a page button may contain the same word)."""
    with user:
        dialogs = [e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value]
    assert dialogs, "expected an open sheet"
    dlg = max(dialogs, key=lambda d: d.id)
    buttons = {e for e in dlg.descendants() if isinstance(e, ui.button) and e.text == text}
    assert buttons, f"no button {text!r} in the open sheet"
    return UserInteraction(user, buttons, text)


def set_value(user: User, element, value) -> None:
    with user:
        element.value = value


async def until(check, timeout: float = 10.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


# ------------------------------------------------------------------ when it shows
async def test_the_first_start_opens_the_welcome_screen(user: User, first_run, tmp_path) -> None:
    await user.open("/")
    await user.should_see("Welcome")
    await user.should_see("Before You Start")
    await user.should_see(str(tmp_path))  # the data folder
    await user.should_see("Load Sample Data")
    await user.should_see("Add Accounts")
    # no sidebar on the first screen
    with user:
        assert not any(isinstance(e, ui.left_drawer) for e in user.client.elements.values())


async def test_the_overview_stays_once_passed_or_with_records(user: User, ctx, setup) -> None:
    ctx.store.settings.onboarding_done = False
    await user.open("/")  # records exist: an existing folder never sees the welcome screen
    await user.should_see("Overview")
    await user.should_not_see("Before You Start")
    demo_svc.finish_onboarding(ctx)
    await user.open("/")
    await user.should_see("Overview")


async def test_the_screen_stays_reachable(user: User, ctx, setup) -> None:
    await user.open("/welcome")
    await user.should_see("Welcome")
    await user.should_see("Only an empty data folder can take the sample data.")
    assert not button(user, "Load Sample Data").enabled


# ------------------------------------------------------------------ the settings on it
async def test_language_and_currency_are_saved(user: User, first_run, tmp_path) -> None:
    await user.open("/welcome")
    set_value(user, _currency(user), "EUR")
    assert first_run.store.settings.display_currency == "EUR"
    assert Store(tmp_path).settings.display_currency == "EUR"
    set_value(user, _language(user), "de")
    await user.should_see("Willkommen")  # the page reloaded in German
    assert Store(tmp_path).settings.language == "de"
    assert i18n.language() == "de"


def _language(user: User) -> ui.select:
    with user:
        return next(e for e in user.client.elements.values() if isinstance(e, ui.select) and "" in e.options)


def _currency(user: User) -> ui.select:
    with user:
        return next(e for e in user.client.elements.values() if isinstance(e, ui.select) and e.value == "CHF")


# ------------------------------------------------------------------ the two ways in
async def test_add_accounts_passes_the_screen(user: User, first_run, tmp_path) -> None:
    await user.open("/welcome")
    user.find("Add Accounts").click()
    await user.should_see("Add Account…")  # Wealth · Accounts
    assert first_run.store.settings.onboarding_done
    assert Store(tmp_path).settings.onboarding_done
    await user.open("/")
    await user.should_see("Overview")


async def test_loading_the_sample_data_opens_the_budget_page(user: User, first_run) -> None:
    await user.open("/welcome")
    user.find("Load Sample Data").click()
    await user.should_see("Record Balance", retries=200)  # the Budget page, once the set is written
    store = first_run.store
    assert store.settings.sample_data and store.settings.onboarding_done
    assert {a.name for a in store.accounts.all()} >= {"Bank", "Savings", "Depot"}
    await user.should_see("Sample data")  # the sidebar says so
    await user.open("/")
    await user.should_see("Overview")


async def test_a_failed_load_is_reported(user: User, first_run, monkeypatch) -> None:
    def refuse(ctx):
        raise ValidationError("Sample data can only be loaded into an empty data folder.")

    monkeypatch.setattr(demo_svc, "load", refuse)
    await user.open("/welcome")
    load = button(user, "Load Sample Data")
    user.find("Load Sample Data").click()
    await until(lambda: notified(user, "Sample data can only be loaded into an empty data folder."))
    assert "loading" not in load.props  # the button is usable again
    assert not first_run.store.settings.onboarding_done


# ------------------------------------------------------------------ settings
async def test_settings_loads_and_removes_the_sample_data(user: User, first_run) -> None:
    await user.open("/settings")
    await user.should_see("A year of example records to explore the app with. It can be removed again here.")
    user.find("Load Sample Data").click()
    await user.should_see("Record Balance", retries=200)
    assert first_run.store.settings.sample_data
    await user.open("/settings")
    await user.should_see("This folder holds the sample set. Removing it empties the folder, as on the first start.")
    await user.should_see("Sample data")  # sidebar
    user.find("Remove Sample Data…").click()
    await user.should_see("Remove the sample data?")
    await user.should_see("Every record, attachment and the change history in this folder is deleted.")
    dialog_button(user, "Remove").click()
    await user.should_see("Before You Start", retries=200)  # back on the welcome screen
    store = first_run.store  # reloaded by the removal
    assert demo_svc.is_empty(first_run)
    assert not store.settings.sample_data and not store.settings.onboarding_done


async def test_settings_refuses_the_sample_data_on_a_folder_with_records(user: User, ctx, setup) -> None:
    await user.open("/settings")
    await user.should_see("Only an empty data folder can take the sample data.")
    assert not button(user, "Load Sample Data").enabled


async def test_a_failed_removal_is_reported(user: User, first_run, monkeypatch) -> None:
    demo_svc.load(first_run)

    def refuse(ctx):
        raise ValidationError("This data folder holds no sample data.")

    monkeypatch.setattr(demo_svc, "remove", refuse)
    await user.open("/settings")
    user.find("Remove Sample Data…").click()
    await user.should_see("Remove the sample data?")
    dialog_button(user, "Remove").click()
    await until(lambda: notified(user, "This data folder holds no sample data."))
    assert not demo_svc.is_empty(first_run)
