import os
from datetime import date, datetime

import pandas as pd
import pytest
import time_machine

from budget_app.domain.fx import FxRates
from budget_app.services import accounts as acc_svc
from budget_app.services.context import Context
from budget_app.storage.store import Store

pytest_plugins = ["nicegui.testing.user_plugin"]  # the `User` simulation for tests/ui

TODAY = date(2026, 10, 18)
# The day the suite runs on: what is due, the sample data's months and the recent categories all follow today, and
# the tests were written around this one. Without it, the tests of loans, the budget plan, filters and the category
# picker failed once the calendar reached November.
NOW = datetime(2026, 10, 30, 12, 0)


_travel = time_machine.travel(NOW, tick=True)
# pytest names a parametrized test by its parameters: a 20 MB payload made a 20 MB name, sent between the xdist
# workers and printed in full, and the Windows build crawled for over ten minutes and drowned in "xxx…"
LONGEST_ID = 2000


def pytest_collection_modifyitems(config, items):
    """Refuse a test whose name is longer than `LONGEST_ID`: give its large parameters a short `id=`."""
    for item in items:
        if len(item.nodeid) > LONGEST_ID:
            raise pytest.UsageError(
                f"{item.nodeid[:120]}… is named by {len(item.nodeid):,} characters of its parameters: "
                "give them a short id (pytest.param(..., id=...))"
            )


def pytest_configure(config):
    """The whole run starts on `NOW` and the clock runs on from there (`tick`), so waits and timeouts still pass;
    set before the test modules are collected, since some take today when they are loaded. The monotonic clock
    asyncio and NiceGUI time with is left alone. Each xdist worker loads this file and sets its own clock."""
    _travel.start()


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


# What one test worker holds at its peak (a NiceGUI app and the clients its UI tests built): measured up to 1.1 GB.
WORKER_MEMORY = 1 << 30


def memory_limit() -> int | None:
    """The memory this process may use, in bytes: a container's limit (cgroup v2 or v1), else the machine's."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            text = open(path, encoding="ascii").read().strip()  # noqa: PTH123, SIM115 - a one-line system file
        except OSError:
            continue
        if text.isdigit() and int(text) < 1 << 60:  # "max", or v1's huge number, means no limit
            return int(text)
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def worker_count(cpus: int, memory: int | None) -> int:
    """`-n auto`: one worker per CPU, but no more than the memory holds (one worker's worth kept for the rest)."""
    if memory is None:
        return max(1, cpus)
    return max(1, min(cpus, memory // WORKER_MEMORY - 1))


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers(config):
    """One worker per CPU killed the CI runner's container (exit 137, out of memory): count the memory too.
    PYTEST_XDIST_AUTO_NUM_WORKERS still sets the number outright (pytest-xdist's own hook reads it)."""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    return worker_count(cpus, memory_limit())


def pytest_unconfigure(config):
    """If the process is still alive 30 s after the session ends, print every thread's stack and exit.

    A leftover non-daemon thread once kept pytest (and with it the pre-push hook) running forever
    after all tests had passed; this turns such a hang into a visible, diagnosable failure.
    """
    import faulthandler  # noqa: PLC0415
    import sys  # noqa: PLC0415

    _travel.stop()
    faulthandler.dump_traceback_later(30, exit=True, file=sys.__stderr__)
