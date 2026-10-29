import os
from datetime import date

import pandas as pd
import pytest

from budget_app.domain.fx import FxRates
from budget_app.services import accounts as acc_svc
from budget_app.services.context import Context
from budget_app.storage.store import Store

pytest_plugins = ["nicegui.testing.user_plugin"]  # the `User` simulation for tests/ui

TODAY = date(2026, 10, 18)


@pytest.fixture(autouse=True)
def home_folder(tmp_path):
    """The app's home folder and its profile registry (F34) are the test's own folder, never the developer's data.

    Not through `monkeypatch`: an autouse fixture would set it up first, so its undo would run after the UI tests'
    `user` fixture has shut the app down, too late for what those tests patch on the app."""
    before = os.environ.get("BUDGET_DATA_DIR")
    os.environ["BUDGET_DATA_DIR"] = str(tmp_path)
    yield
    if before is None:
        os.environ.pop("BUDGET_DATA_DIR", None)
    else:
        os.environ["BUDGET_DATA_DIR"] = before


@pytest.fixture
def rates() -> FxRates:
    df = pd.DataFrame(
        {"CHF": [0.95, 0.94, 0.90], "USD": [1.10, 1.20, 1.25], "EUR": [1.0, 1.0, 1.0]},
        index=[date(2026, 8, 29), date(2026, 9, 30), date(2026, 10, 16)],
    )
    return FxRates(df)


@pytest.fixture
def ctx(tmp_path, rates) -> Context:
    c = Context(store=Store(tmp_path), _fx=rates)
    c.store.settings.budget_horizon = "2027-03"
    return c


@pytest.fixture
def setup(ctx):
    """Two regular accounts (CHF, EUR) and one income category."""
    chf = acc_svc.add_account(ctx, "Bank", "CHF")
    eur = acc_svc.add_account(ctx, "Euro account", "EUR")
    salary = acc_svc.add_income_category(ctx, "Salary")
    return {"chf": chf, "eur": eur, "salary": salary}


def pytest_unconfigure(config):
    """If the process is still alive 30 s after the session ends, print every thread's stack and exit.

    A leftover non-daemon thread once kept pytest (and with it the pre-push hook) running forever
    after all tests had passed; this turns such a hang into a visible, diagnosable failure.
    """
    import faulthandler  # noqa: PLC0415
    import sys  # noqa: PLC0415

    faulthandler.dump_traceback_later(30, exit=True, file=sys.__stderr__)
