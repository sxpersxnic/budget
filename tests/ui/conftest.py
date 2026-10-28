"""UI tests run the real pages in NiceGUI's simulated client: no browser, no server, no network."""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path

import pytest
from nicegui.testing import User
from nicegui.testing.user_simulation import user_simulation

from budget_app.domain import months
from budget_app.services import budget as budget_svc
from budget_app.services import context as context_module
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.transactions import TransactionInput

MAIN = Path(__file__).with_name("ui_main.py")


@pytest.fixture
def demo(ctx, setup):
    """Six months of balances, salary and spending, and a budget that runs forever."""
    cm = months.current_month()
    for k in range(6, 0, -1):
        m = months.add_months(cm, -k)
        y, mo = map(int, m.split("-"))
        wealth_svc.upsert_entry(ctx, setup["chf"].id, m, 4000 + 150 * k)
        wealth_svc.upsert_entry(ctx, setup["eur"].id, m, 800 - 20 * k)
        tx_svc.save(
            ctx,
            TransactionInput(
                date=date(y, mo, 25),
                type="income",
                amount=6200,
                currency="CHF",
                category=setup["salary"].id,
                to_account=setup["chf"].id,
            ),
        )
        for code, amount in (("01.1", 640), ("04.1", 1900), ("07.3", 120)):
            tx_svc.save(
                ctx,
                TransactionInput(
                    date=date(y, mo, 10),
                    type="expenditure",
                    amount=amount,
                    currency="CHF",
                    category=code,
                    from_account=setup["chf"].id,
                ),
            )
    start = months.add_months(cm, -6)
    for code, amount in (("01.1", 700), ("04.1", 1900), ("07.3", 150)):
        budget_svc.create(
            ctx,
            BudgetInput(
                start_month=start, forever=True, type="expenditure", currency="CHF", amount=amount, category=code
            ),
        )
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month=start, forever=True, type="income", currency="CHF", amount=6200, category=setup["salary"].id
        ),
    )
    return setup


@pytest.fixture
async def user(ctx, caplog: pytest.LogCaptureFixture) -> AsyncGenerator[User]:
    """A simulated user on the app's pages; fails the test if a page logged an error."""
    ctx.store.settings.budget_horizon = months.add_months(months.current_month(), 12)
    ctx.store.settings.onboarding_done = True  # the welcome screen (F30) is tested on its own
    context_module._ctx = ctx
    try:
        async with user_simulation(main_file=MAIN) as u:
            yield u
    finally:
        context_module._ctx = None
    errors = [r for r in caplog.get_records("call") if r.levelname == "ERROR"]
    if errors:
        pytest.fail(f"Unexpected ERROR logs: {[r.getMessage() for r in errors]}", pytrace=False)
