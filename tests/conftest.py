from datetime import date

import pandas as pd
import pytest

from budget_app.domain.fx import FxRates
from budget_app.services import accounts as acc_svc
from budget_app.services.context import Context
from budget_app.storage.store import Store

TODAY = date(2026, 10, 18)


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
