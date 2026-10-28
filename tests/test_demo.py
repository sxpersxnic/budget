"""Sample data (F30): loading the set into an empty folder, removing it, and the first-run flag."""

import json

import pytest

from budget_app.domain.fx import FxRates
from budget_app.domain.models import TX_EXCHANGE
from budget_app.services import accounts as acc_svc
from budget_app.services import demo
from budget_app.services.context import Context, ValidationError
from budget_app.storage import backup
from budget_app.storage.store import Store


def test_empty_folder_is_the_first_run(ctx):
    assert demo.is_empty(ctx)
    assert demo.first_run(ctx)
    demo.finish_onboarding(ctx)
    assert not demo.first_run(ctx)
    assert Store(ctx.store.data_dir).settings.onboarding_done  # persisted


def test_a_folder_with_records_never_shows_the_welcome_screen(ctx, setup):
    assert not demo.is_empty(ctx)
    assert not ctx.store.settings.onboarding_done
    assert not demo.first_run(ctx)


def test_load_writes_the_set_and_marks_the_folder(ctx, tmp_path):
    demo.load(ctx)
    store = ctx.store
    names = {a.name for a in store.accounts.all()}
    assert {"Bank", "Savings", "Euro account", "Depot", "Crypto wallet", "Pillar 3a"} <= names
    assert len(store.transactions.all()) > 200
    assert store.transactions.where(type=TX_EXCHANGE)  # EUR bookings need the rates the fixture has
    for table in ("budget", "account_entries", "templates", "tags", "goals", "holdings", "trades", "prices"):
        assert getattr(store, table).all(), table
    assert store.attachments.all() and list((tmp_path / backup.ATTACHMENTS).iterdir())
    assert store.tax_sections.all()
    assert store.settings.sample_data and store.settings.onboarding_done
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["sample_data"] is True
    # the sample records are no changes to undo: the history starts empty
    assert store.history.actions() == []
    assert not (tmp_path / "history.jsonl").exists()
    assert not demo.first_run(ctx)


def test_load_without_rates_stays_in_chf(tmp_path):
    ctx = Context(store=Store(tmp_path), _fx=FxRates())
    demo.load(ctx)
    accounts = ctx.store.accounts.all()
    assert {a.currency for a in accounts} == {"CHF"}
    assert not ctx.store.transactions.where(type=TX_EXCHANGE)
    assert all(b.currency == "CHF" for b in ctx.store.budget.all())
    assert len(ctx.store.transactions.all()) > 150


def test_load_refuses_a_folder_with_records(ctx, setup):
    with pytest.raises(ValidationError, match="empty data folder"):
        demo.load(ctx)
    assert not ctx.store.settings.sample_data


def test_remove_empties_the_folder_and_keeps_the_settings(ctx, tmp_path):
    ctx.store.settings.language = "de"
    ctx.store.settings.display_currency = "EUR"
    demo.load(ctx)
    assert (tmp_path / "transactions.csv").exists()
    demo.remove(ctx)
    assert demo.is_empty(ctx)
    assert demo.first_run(ctx)  # the welcome screen comes back
    assert not (tmp_path / backup.ATTACHMENTS).exists()
    assert not (tmp_path / "history.jsonl").exists()
    for f in backup.data_files(tmp_path):  # the reloaded store writes empty tables: a header line at most
        assert f.name == "settings.json" or len(f.read_text(encoding="utf-8").splitlines()) <= 1, f.name
    settings = Store(tmp_path).settings
    assert (settings.language, settings.display_currency) == ("de", "EUR")
    assert not settings.sample_data and not settings.onboarding_done
    # the folder takes records again
    acc_svc.add_account(ctx, "Bank", "CHF")
    assert len(ctx.store.accounts.all()) == 1


def test_remove_refuses_a_folder_without_sample_data(ctx, setup):
    with pytest.raises(ValidationError, match="no sample data"):
        demo.remove(ctx)
    assert len(ctx.store.accounts.all()) == 2
