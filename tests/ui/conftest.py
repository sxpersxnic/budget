"""UI tests run the real pages in NiceGUI's simulated client: no browser, no server, no network."""

import re
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from nicegui import Client, background_tasks
from nicegui.nicegui import _on_handshake
from nicegui.testing import User
from nicegui.testing.user_simulation import user_simulation

from budget_app.domain import months
from budget_app.services import budget as budget_svc
from budget_app.services import context as context_module
from budget_app.services import security
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


_patch_outbox = User._patch_outbox_emit_function


def _follow_full_loads(self: User) -> None:
    """Load the page a client is sent to (`client.open`, layout.full_load) as a browser does: NiceGUI's simulated
    user only follows `ui.navigate`, which changes pages in the shell (47)."""
    _patch_outbox(self)
    emit = self._client.outbox._emit

    async def follow(message) -> None:
        await emit(message)
        _, kind, data = message
        if kind == "open" and not data["new_tab"]:
            background_tasks.create(self.open(data["path"]), name=f"load {data['path']}")

    self._client.outbox._emit = follow  # type: ignore[method-assign]


User._patch_outbox_emit_function = _follow_full_loads  # type: ignore[method-assign]


async def _open(self: User, path: str, *, clear_forward_history: bool = True) -> Client:
    """NiceGUI's `User.open`, with the simulated browser's JavaScript answers in place before the page connects.

    NiceGUI connects first and only then installs them, so the sidebar's question on connecting (`ui.left_drawer`,
    "is the drawer open?") could go out unanswered on a busy machine, and its 1 s wait failed the test (seen on the
    CI runner with parallel workers).
    """
    response = await self.http_client.get(path, follow_redirects=True)
    assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
    if response.headers.get("X-Nicegui-Content") != "page":
        raise ValueError(f"Expected a page response, got {response.text}")
    match = re.search(r"'client_id': '([0-9a-f-]+)'", response.text)
    assert match is not None
    self.client = Client.instances[match.group(1)]
    self._patch_outbox_emit_function()  # before the handshake, which runs the page's on_connect handlers
    self.sio.on("connect")
    await _on_handshake(
        f"test-{uuid4()}", {"client_id": self.client.id, "tab_id": self.tab_id, "document_id": str(uuid4())}
    )
    self.back_history.append(path)
    if clear_forward_history:
        self.forward_history.clear()
    return self.client


User.open = _open  # type: ignore[method-assign]

# A page change that reloads the page (the lock, the welcome screen) takes longer on a busy machine than the 0.3 s
# NiceGUI's `should_see` waits by default; up to 3 s here, which costs nothing when the text is already there.
WAIT_RETRIES = 30
_should_see = User.should_see


async def _patient_should_see(self: User, *args, retries: int = WAIT_RETRIES, **kwargs) -> None:
    await _should_see(self, *args, retries=retries, **kwargs)


User.should_see = _patient_should_see  # type: ignore[method-assign, assignment]


@pytest.fixture
async def user(ctx, caplog: pytest.LogCaptureFixture) -> AsyncGenerator[User]:
    """A simulated user on the app's pages; fails the test if a page logged an error."""
    ctx.store.settings.budget_horizon = months.add_months(months.current_month(), 12)
    ctx.store.settings.onboarding_done = True  # the welcome screen (F30) is tested on its own
    context_module._ctx = ctx
    security.configure(ctx.store.data_dir)  # the app lock (F27): off unless the test sets a password
    try:
        async with user_simulation(main_file=MAIN) as u:
            yield u
    finally:
        context_module._ctx = None
    errors = [r for r in caplog.get_records("call") if r.levelname == "ERROR"]
    if errors:
        pytest.fail(f"Unexpected ERROR logs: {[r.getMessage() for r in errors]}", pytrace=False)
