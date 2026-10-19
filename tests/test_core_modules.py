"""Non-UI modules: ECB download, instance lock, config paths, entry point, i18n and service edge cases."""

import io
import os
import subprocess
import sys
import types
import zipfile
from contextlib import contextmanager
from datetime import date
from importlib import metadata
from pathlib import Path

import httpx
import pytest

from budget_app import config, i18n
from budget_app import main as main_module
from budget_app.domain import fx as fx_module
from budget_app.domain import months
from budget_app.domain.fx import FxError, FxRates, FxService, download_ecb_csv
from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import context as context_module
from budget_app.services import reports
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.storage import lock
from budget_app.storage.store import Store

ECB_CSV = "Date,USD,CHF,GBP,\n2026-10-16,1.25,0.90,0.85,\n2026-10-15,1.24,0.91,N/A,\n"


def ecb_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


class FakeEcb(list):
    """The URLs requested so far; `handler` answers them (default: a valid ECB archive)."""

    def __init__(self) -> None:
        super().__init__()
        self.handler = lambda request: httpx.Response(200, content=ecb_zip({"eurofxref-hist.csv": ECB_CSV}))

    def set_handler(self, handler) -> None:
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.append(str(request.url))
        return self.handler(request)


@pytest.fixture
def serve(monkeypatch) -> FakeEcb:
    """Route the ECB download through httpx.MockTransport: no network."""
    server = FakeEcb()

    @contextmanager
    def fake_stream(method, url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(server)) as client, client.stream(method, url, **kwargs) as r:
            yield r

    monkeypatch.setattr(fx_module.httpx, "stream", fake_stream)
    return server


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


# ------------------------------------------------------------------ fx: download and service
def test_download_replaces_the_target_with_the_csv(tmp_path, serve):
    target = tmp_path / "ecb" / "eurofxref-hist.csv"
    download_ecb_csv(target, url="https://ecb.test/hist.zip")
    assert serve == ["https://ecb.test/hist.zip"]
    assert target.read_text(encoding="utf-8") == ECB_CSV
    assert not list(target.parent.glob(".ecb-*"))  # no temporary file left behind


@pytest.mark.parametrize(
    ("files", "limits", "message"),
    [
        ({"readme.txt": "no csv"}, {}, "does not contain a CSV"),
        ({"eurofxref-hist.csv": ECB_CSV}, {"MAX_DOWNLOAD_BYTES": 10}, "download is unexpectedly large"),
        ({"eurofxref-hist.csv": ECB_CSV}, {"MAX_CSV_BYTES": 10}, "CSV is unexpectedly large"),
    ],
)
def test_download_rejects_unexpected_archives(tmp_path, serve, monkeypatch, english, files, limits, message):
    serve.set_handler(lambda request: httpx.Response(200, content=ecb_zip(files)))
    for name, value in limits.items():
        monkeypatch.setattr(fx_module, name, value)
    target = tmp_path / "rates.csv"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(FxError, match=message):
        download_ecb_csv(target)
    assert target.read_text(encoding="utf-8") == "old"


def test_download_validates_the_csv_before_replacing(tmp_path, serve):
    serve.set_handler(lambda request: httpx.Response(200, content=ecb_zip({"x.csv": "Nope,USD\n1,2\n"})))
    target = tmp_path / "rates.csv"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(KeyError):
        download_ecb_csv(target)
    assert target.read_text(encoding="utf-8") == "old"


def test_download_http_error(tmp_path, serve):
    serve.set_handler(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        download_ecb_csv(tmp_path / "rates.csv")


def test_download_removes_the_temporary_file_when_writing_fails(tmp_path, serve, monkeypatch):
    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(fx_module.os, "fsync", broken_fsync)
    target = tmp_path / "rates.csv"
    with pytest.raises(OSError, match="disk full"):
        download_ecb_csv(target)
    assert list(tmp_path.iterdir()) == []


def test_service_without_a_file_has_no_rates(tmp_path):
    svc = FxService(tmp_path)
    assert svc.path == tmp_path / "ecb" / config.ECB_CSV_NAME
    assert not svc.rates.available
    assert svc.last_error is None


def test_service_loads_an_existing_file(tmp_path):
    (tmp_path / "ecb").mkdir()
    (tmp_path / "ecb" / config.ECB_CSV_NAME).write_text(ECB_CSV, encoding="utf-8")
    svc = FxService(tmp_path)
    assert svc.rates.latest_date() == date(2026, 10, 16)
    assert svc.rates.currencies() == ["CHF", "EUR", "GBP", "USD"]


def test_service_reports_a_corrupt_file(tmp_path, english):
    (tmp_path / "ecb").mkdir()
    (tmp_path / "ecb" / config.ECB_CSV_NAME).write_text("garbage", encoding="utf-8")
    svc = FxService(tmp_path)
    assert not svc.rates.available
    assert svc.last_error.startswith("Could not read exchange rates: ")


def test_refresh_downloads_once_per_day(tmp_path, serve):
    svc = FxService(tmp_path)
    today = date(2026, 10, 19)
    assert svc.refresh_if_stale(None, today) is True
    assert svc.rates.latest_date() == date(2026, 10, 16)
    assert svc.last_error is None
    assert svc.refresh_if_stale("2026-10-19", today) is False  # already downloaded today
    assert len(serve) == 1
    assert svc.refresh_if_stale("2026-10-18", today) is True  # a new day
    assert len(serve) == 2


def test_refresh_downloads_again_when_the_file_is_missing(tmp_path, serve):
    svc = FxService(tmp_path)
    assert svc.refresh_if_stale(date.today().isoformat()) is True  # marked as done today, but no file


def test_failed_refresh_keeps_the_old_rates(tmp_path, serve, english):
    svc = FxService(tmp_path)
    svc.refresh_if_stale(None, date(2026, 10, 18))
    serve.set_handler(lambda request: httpx.Response(500))
    assert svc.refresh_if_stale("2026-10-18", date(2026, 10, 19)) is False
    assert svc.rates.latest_date() == date(2026, 10, 16)
    assert svc.last_error.startswith("Exchange rate download failed (")
    assert svc.last_error.endswith("Using the last stored rates.")
    i18n.set_language("de")  # the message follows the interface language
    assert "Using the last stored rates." not in svc.last_error
    serve.set_handler(lambda request: httpx.Response(200, content=ecb_zip({"a.csv": ECB_CSV})))
    assert svc.refresh_if_stale("2026-10-18", date(2026, 10, 19)) is True
    assert svc.last_error is None


def test_refresh_skips_while_another_refresh_runs(tmp_path, serve):
    svc = FxService(tmp_path)
    svc._refresh_lock.acquire()
    try:
        assert svc.refresh_if_stale(None) is False
    finally:
        svc._refresh_lock.release()
    assert serve == []


def test_empty_rates():
    fx = FxRates()
    assert not fx.available
    assert fx.latest_date() is None
    assert fx.currencies() == ["CHF"]
    assert fx.rate_date_on_or_before(date(2026, 1, 1)) is None
    assert fx.rate("EUR") == 1.0
    with pytest.raises(FxError):
        fx.rate("USD")


def test_latest_rate_without_a_date(rates):
    assert rates.rate("USD") == 1.25
    assert rates.convert(10, "EUR", "USD") == pytest.approx(12.5)


# ------------------------------------------------------------------ instance lock
@pytest.fixture
def no_lock(monkeypatch):
    monkeypatch.setattr(lock, "_handle", None)
    yield
    if lock._handle is not None:
        lock._handle.close()


def test_lock_writes_the_pid_and_blocks_a_second_instance(tmp_path, no_lock):
    lock.acquire(tmp_path)
    assert (tmp_path / ".lock").read_text(encoding="utf-8") == str(os.getpid())
    first = lock._handle
    with pytest.raises(lock.AlreadyRunningError, match="already running"):
        lock.acquire(tmp_path)
    assert lock._handle is first  # the held lock is kept


def test_lock_can_be_taken_again_after_release(tmp_path, no_lock):
    lock.acquire(tmp_path)
    lock._handle.close()
    lock.acquire(tmp_path)
    assert not lock._handle.closed


def test_lock_on_windows_uses_msvcrt(tmp_path, monkeypatch, no_lock):
    calls = []
    fake = types.SimpleNamespace(LK_NBLCK=2, locking=lambda fd, mode, n: calls.append((mode, n)))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(lock.sys, "platform", "win32")
    lock.acquire(tmp_path)
    assert calls == [(2, 1)]

    def refuse(fd, mode, n):
        raise OSError("locked")

    fake.locking = refuse
    with pytest.raises(lock.AlreadyRunningError):
        lock.acquire(tmp_path)


# ------------------------------------------------------------------ config
def test_app_version(monkeypatch):
    monkeypatch.setattr(config.metadata, "version", lambda name: "1.2.3")
    assert config.app_version() == "1.2.3"

    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(config.metadata, "version", missing)
    assert config.app_version() == "dev"


def test_is_frozen(monkeypatch):
    assert config.is_frozen() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config.is_frozen() is True


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "HOME/Library/Application Support/Budget"),
        ("win32", {"APPDATA": "APPDATA"}, "APPDATA/Budget"),
        ("win32", {}, "HOME/AppData/Roaming/Budget"),
        ("linux", {"XDG_DATA_HOME": "XDG"}, "XDG/budget"),
        ("linux", {}, "HOME/.local/share/budget"),
    ],
)
def test_user_data_dir(monkeypatch, tmp_path, platform, env, expected):
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path / "HOME")
    for var in ("APPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, str(tmp_path / value))
    monkeypatch.setattr(config.sys, "platform", platform)
    assert config.user_data_dir() == tmp_path / expected


def test_data_dir_order(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "project")
    monkeypatch.setattr(config, "user_data_dir", lambda: tmp_path / "user")
    monkeypatch.delenv("BUDGET_DATA_DIR", raising=False)
    assert config.data_dir() == tmp_path / "project" / "data"
    assert (tmp_path / "project" / "data").is_dir()
    monkeypatch.setattr(config, "is_frozen", lambda: True)
    assert config.data_dir() == tmp_path / "user"
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "env"))
    assert config.data_dir() == tmp_path / "env"
    assert (tmp_path / "env").is_dir()


# ------------------------------------------------------------------ entry point
@pytest.fixture
def entry(monkeypatch, tmp_path):
    """run() with the server, the lock and the page setup replaced by recorders."""
    calls: dict = {"run": None, "lock": [], "setup": []}
    monkeypatch.setattr(main_module.ui, "run", lambda **kw: calls.__setitem__("run", kw))
    monkeypatch.setattr(main_module.instance_lock, "acquire", calls["lock"].append)
    monkeypatch.setattr(main_module, "setup", calls["setup"].append)
    monkeypatch.setattr(main_module.native, "find_open_port", lambda: 54321)
    monkeypatch.setattr(main_module.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(main_module.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr(config, "is_frozen", lambda: False)
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "default"))
    monkeypatch.setitem(main_module.app.native.settings, "ALLOW_DOWNLOADS", False)

    def start(*argv: str) -> dict:
        monkeypatch.setattr(sys, "argv", ["budget", *argv])
        main_module.run()
        return calls

    return start


def test_run_defaults_to_the_browser_on_localhost(entry, tmp_path, english):
    calls = entry()
    data = tmp_path / "default"
    assert calls["lock"] == [data] and calls["setup"] == [data]
    kw = calls["run"]
    assert (kw["host"], kw["port"], kw["native"], kw["show"]) == ("127.0.0.1", 8080, False, True)
    assert kw["window_size"] is None
    assert kw["reload"] is False
    assert kw["language"] == "en-GB"
    assert main_module.app.native.settings["ALLOW_DOWNLOADS"] is False


def test_run_with_options(entry, tmp_path, monkeypatch):
    data = tmp_path / "custom"
    calls = entry("--port", "9000", "--host", "0.0.0.0", "--data-dir", str(data), "--no-show")  # noqa: S104
    assert os.environ["BUDGET_DATA_DIR"] == str(data.resolve())
    assert calls["setup"] == [data.resolve()]
    kw = calls["run"]
    assert (kw["host"], kw["port"], kw["show"]) == ("0.0.0.0", 9000, False)  # noqa: S104


def test_run_native_uses_a_free_port_and_allows_downloads(entry):
    kw = entry("--native")["run"]
    assert (kw["native"], kw["port"], kw["window_size"]) == (True, 54321, (1400, 900))
    assert main_module.app.native.settings["ALLOW_DOWNLOADS"] is True


def test_installed_build_opens_natively_unless_told_otherwise(entry, monkeypatch):
    monkeypatch.setattr(config, "is_frozen", lambda: True)
    assert entry()["run"]["native"] is True
    assert entry("--no-native")["run"]["native"] is False


def test_run_exits_when_another_instance_holds_the_lock(entry, monkeypatch, caplog):
    def busy(data_dir):
        raise lock.AlreadyRunningError("The budget app is already running")

    monkeypatch.setattr(main_module.instance_lock, "acquire", busy)
    with pytest.raises(SystemExit) as exc:
        entry()
    assert exc.value.code == 1
    assert "already running" in caplog.text


def test_missing_streams_are_redirected_to_a_log_file(tmp_path, monkeypatch):
    out = sys.stdout
    monkeypatch.setattr(sys, "stderr", None)
    main_module._redirect_missing_streams(tmp_path)
    try:
        assert sys.stdout is out
        assert sys.stderr.name == str(tmp_path / "budget.log")
    finally:
        sys.stderr.close()


def test_streams_are_left_alone_when_present(tmp_path):
    main_module._redirect_missing_streams(tmp_path)
    assert not (tmp_path / "budget.log").exists()


async def test_setup_registers_pages_and_refreshes_rates(tmp_path, monkeypatch, english):
    pages, startup, timers, refreshed = [], [], [], []
    monkeypatch.setattr(context_module, "_ctx", None)
    monkeypatch.setattr(main_module.theme, "install", lambda: None)
    monkeypatch.setattr(main_module.ui, "page", lambda path, title: lambda builder: pages.append((path, builder)))
    monkeypatch.setattr(main_module.app, "on_startup", startup.append)
    monkeypatch.setattr(main_module.app, "timer", lambda interval, fn, immediate: timers.append((interval, immediate)))

    async def fake_refresh(ctx):
        refreshed.append(ctx)

    monkeypatch.setattr(main_module, "refresh_rates", fake_refresh)
    store = Store(tmp_path)
    store.settings.language = "de"
    store.save_settings()

    main_module.setup(tmp_path)
    ctx = context_module.get_ctx()
    assert ctx.store.data_dir == tmp_path
    assert i18n.language() == "de"
    assert dict(pages) == main_module.PAGES
    assert timers == [(1800, False)]
    [refresh] = startup
    await refresh()
    assert refreshed == [ctx]


# ------------------------------------------------------------------ i18n
def test_system_codes_read_the_environment(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LC_MESSAGES", "")
    monkeypatch.setenv("LANG", "de_CH.UTF-8")
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("en_GB", "UTF-8"))
    assert i18n._system_codes() == ["de_CH.UTF-8", "en_GB"]
    assert i18n.system_language() == "de"


def test_system_codes_on_macos_ask_the_os(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (None, None))
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='(\n    "fr-CH",\n    "de-CH"\n)\n')

    monkeypatch.setattr(i18n.subprocess, "run", fake_run)
    assert i18n._system_codes() == ["fr-CH", "de-CH"]
    assert seen[0][0] == "/usr/bin/defaults"
    assert i18n.system_language() == "de"


def test_system_codes_survive_os_errors(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("defaults", 2)

    def bad_locale():
        raise ValueError("unknown locale")

    monkeypatch.setattr(i18n.subprocess, "run", fail)
    monkeypatch.setattr(i18n.locale, "getlocale", bad_locale)
    assert i18n._system_codes() == []
    assert i18n.system_language() == "en"


def test_texts_dates_and_locales(english):
    assert i18n.catalog("en") == {}
    assert i18n.catalog("xx") == {}  # no such catalog file
    assert i18n.t("Hello {name}", name="Ann") == "Hello Ann"
    assert i18n.tc("heading", "Income") == "Income"
    assert i18n.tn("{n} item", "{n} items", 1) == "1 item"
    assert i18n.tn("{n} item", "{n} items", 3) == "3 items"
    assert i18n.N_("Later") == "Later"
    assert i18n.month_label(2026, 3, long=True) == "March 2026"
    assert i18n.date_label(date(2026, 10, 9)) == "9 Oct 2026"
    assert i18n.browser_locale() == "en-GB"
    i18n.set_language("de")
    assert i18n.tc("heading", "Income") == i18n.catalog("de")["heading|Income"]
    assert i18n.tc("heading", "No such text {x}", x=1) == "No such text 1"  # falls back to t()
    assert i18n.month_label(2026, 3, long=True) == "März 2026"
    assert i18n.date_label(date(2026, 10, 9)) == "9. Okt. 2026"
    assert i18n.browser_locale() == "de-CH"


def test_tc_formats_values(english, monkeypatch):
    monkeypatch.setitem(i18n.catalog("de"), "ctx|{n} things", "{n} Dinge")
    i18n.set_language("de")
    assert i18n.tc("ctx", "{n} things", n=2) == "2 Dinge"


def test_set_language_without_a_ui(monkeypatch, english):
    import nicegui  # noqa: PLC0415

    monkeypatch.delattr(nicegui, "app")
    assert i18n.set_language("de") == "de"


# ------------------------------------------------------------------ context
def test_context_conversions(ctx):
    assert ctx.to_base_latest(10, "CHF") == (10.0, date(2026, 10, 16))
    assert ctx.to_base_latest(10, "EUR") == (9.0, date(2026, 10, 16))
    assert ctx.to_base_on(10, "EUR", date(2026, 10, 1)) == (9.4, date(2026, 9, 30))
    assert ctx.to_base_on(10, "CHF", date(2026, 10, 1)) == (10.0, date(2026, 9, 30))
    assert ctx.month_rate_date("2026-09") == date(2026, 9, 30)
    assert ctx.convert_on(9, "CHF", "EUR", date(2026, 10, 16)) == pytest.approx(10)


def test_context_without_rates(tmp_path):
    c = Context(store=Store(tmp_path))
    assert not c.fx.available
    assert c.to_base_latest(5, "CHF") == (5.0, date.today())
    assert c.to_base_on(5, "CHF", date(2026, 1, 2)) == (5.0, date(2026, 1, 2))
    assert c.month_rate_date("2026-01") == date.today()
    assert c.horizon() == f"{date.today().year + 1}-12"


def test_context_uses_the_service_rates(tmp_path):
    svc = FxService(tmp_path)
    svc.rates = FxRates.from_csv(io.StringIO(ECB_CSV))
    c = Context(store=Store(tmp_path), fx_service=svc)
    assert c.fx is svc.rates


def test_get_ctx_before_and_after_init(tmp_path, monkeypatch):
    monkeypatch.setattr(context_module, "_ctx", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        context_module.get_ctx()
    c = context_module.init_context(tmp_path)
    assert context_module.get_ctx() is c
    assert isinstance(c.fx_service, FxService)
    assert (tmp_path / ".app_version").exists()  # the version marker of the backup check


# ------------------------------------------------------------------ accounts
def test_account_labels_and_sorting(ctx, setup):
    acc_svc.add_account(ctx, "bank", "EUR")
    acc_svc.add_account(ctx, "Advance", "CHF", ACCOUNT_ADVANCE)
    assert [acc_svc.account_label(a) for a in acc_svc.accounts(ctx, ACCOUNT_REGULAR)] == [
        "Bank (CHF)",
        "bank (EUR)",
        "Euro account (EUR)",
    ]
    assert [a.name for a in acc_svc.accounts(ctx, currency="CHF")] == ["Advance", "Bank"]
    assert acc_svc.account_label(None) == ""


def test_account_validation(ctx, setup, english):
    with pytest.raises(ValidationError, match="Name is required"):
        acc_svc.add_account(ctx, "   ", "CHF")
    with pytest.raises(ValidationError, match="Currency is required"):
        acc_svc.add_account(ctx, "X", "")
    with pytest.raises(ValidationError, match="already exists"):
        acc_svc.add_account(ctx, " BANK ", "CHF")
    # same name in another currency or as an advance account is fine
    acc_svc.add_account(ctx, "Bank", "USD")
    acc_svc.add_account(ctx, "Bank", "CHF", ACCOUNT_ADVANCE)
    with pytest.raises(ValidationError, match="Account not found"):
        acc_svc.edit_account(ctx, "nope", "X", "CHF")
    with pytest.raises(ValidationError, match="already exists"):
        acc_svc.edit_account(ctx, setup["eur"].id, "bank", "CHF")
    assert acc_svc.edit_account(ctx, setup["chf"].id, "bank", "CHF").name == "bank"  # its own name


def test_account_in_use_by_transactions_and_balances(ctx, setup, english):
    assert not acc_svc.account_in_use(ctx, setup["chf"].id)
    wealth_svc.upsert_entry(ctx, setup["eur"].id, "2026-09", 10)
    assert acc_svc.account_in_use(ctx, setup["eur"].id)
    with pytest.raises(ValidationError, match="currency cannot be changed"):
        acc_svc.edit_account(ctx, setup["eur"].id, "Euro account", "USD")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1), amount=5, currency="CHF", category="01.1", from_account=setup["chf"].id
        ),
    )
    assert acc_svc.account_in_use(ctx, setup["chf"].id)
    with pytest.raises(ValidationError, match="Accounts with transactions cannot be deleted"):
        acc_svc.delete_account(ctx, setup["chf"].id)
    acc_svc.delete_account(ctx, setup["eur"].id)  # balances only: deleted with the account
    assert ctx.store.account_entries.all() == []


def test_default_advance_account_is_created_once_per_currency(ctx, setup):
    acc_svc.add_account(ctx, "Club", "CHF", ACCOUNT_ADVANCE)
    first = acc_svc.default_advance_account(ctx, "CHF")
    assert (first.name, first.kind, first.currency) == (acc_svc.DEFAULT_ADVANCE_NAME, ACCOUNT_ADVANCE, "CHF")
    assert acc_svc.default_advance_account(ctx, "CHF").id == first.id
    assert acc_svc.default_advance_account(ctx, "EUR").id != first.id
    assert len(acc_svc.accounts(ctx, ACCOUNT_ADVANCE)) == 3


def test_income_categories(ctx, setup, english):
    bonus = acc_svc.add_income_category(ctx, " Bonus ")
    assert [c.name for c in acc_svc.income_categories(ctx)] == ["Bonus", "Salary"]
    with pytest.raises(ValidationError, match="already exists"):
        acc_svc.add_income_category(ctx, "salary")
    with pytest.raises(ValidationError, match="Name is required"):
        acc_svc.add_income_category(ctx, "")
    with pytest.raises(ValidationError, match="not found"):
        acc_svc.rename_income_category(ctx, "nope", "X")
    assert acc_svc.rename_income_category(ctx, bonus.id, "bonus").name == "bonus"
    cm = months.current_month()
    budget_svc.create(
        ctx,
        BudgetInput(start_month=cm, end_month=cm, type="income", currency="CHF", amount=1, category=bonus.id),
    )
    assert acc_svc.income_category_in_use(ctx, bonus.id)
    with pytest.raises(ValidationError, match="cannot be deleted"):
        acc_svc.delete_income_category(ctx, bonus.id)
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type="income",
            amount=5,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    assert acc_svc.income_category_in_use(ctx, setup["salary"].id)
    other = acc_svc.add_income_category(ctx, "Other")
    acc_svc.delete_income_category(ctx, other.id)
    assert ctx.store.income_categories.get(other.id) is None


# ------------------------------------------------------------------ reports
def _spend(ctx, setup, d: date, amount: float, code: str, currency: str = "CHF", account: str = "chf"):
    tx_svc.save(
        ctx,
        TransactionInput(date=d, amount=amount, currency=currency, category=code, from_account=setup[account].id),
    )


def test_series_keys(ctx, setup, english):
    keys = reports.series_keys(ctx)
    assert keys["EXP:TOTAL"] == "Total expenditure"
    assert keys["INC:TOTAL"] == "Total income"
    assert keys["EXP:01"].startswith("01")
    assert keys["EXP:TAX"] == "Taxes"
    assert keys[f"INC:{setup['salary'].id}"] == "Income: Salary"
    assert keys["INC:ADV_REPAY"] == "Income: Advance repayments"
    assert list(keys)[:2] == ["EXP:TOTAL", "INC:TOTAL"]


def test_monthly_series_actual_and_budget(ctx, setup, english):
    _spend(ctx, setup, date(2026, 9, 5), 100, "01.1")
    _spend(ctx, setup, date(2026, 9, 6), 50, "07.3")
    _spend(ctx, setup, date(2026, 10, 6), 20, "01.1")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type="income",
            amount=1000,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    budget_svc.create(
        ctx, BudgetInput(start_month="2026-09", end_month="2026-10", currency="CHF", amount=120, category="01.1")
    )
    budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-09",
            end_month="2026-09",
            type="income",
            currency="EUR",
            amount=1000,
            category=setup["salary"].id,
        ),
    )
    keys = ["EXP:TOTAL", "EXP:01", "INC:TOTAL", f"INC:{setup['salary'].id}", "EXP:UNKNOWN"]
    labels = reports.series_keys(ctx)
    plain = reports.monthly_series(ctx, "2026-09", "2026-10", "CHF", keys)
    assert [r["month"] for r in plain] == ["2026-09", "2026-10"]
    sep = plain[0]
    assert sep["Total expenditure"] == 150
    assert sep[labels["EXP:01"]] == 100
    assert sep["Total income"] == 1000
    assert sep["Income: Salary"] == 1000
    assert sep["EXP:UNKNOWN"] == 0  # unknown keys are shown under their own name
    assert "Total expenditure (budget)" not in sep
    assert plain[1]["Total expenditure"] == 20
    assert plain[1]["Total income"] == 0

    with_budget = reports.monthly_series(ctx, "2026-09", "2026-10", "CHF", keys, include_budget=True)
    sep, octo = with_budget
    assert sep["Total expenditure (budget)"] == 120
    assert sep[reports.budget_label(labels["EXP:01"])] == 120
    assert sep["Total income (budget)"] == 900  # 1000 EUR at the latest rate
    assert sep["Income: Salary (budget)"] == 900
    assert octo["Total income (budget)"] == 0


def test_account_changes(ctx, setup):
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-08", 1000)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-10", 1250.555)
    wealth_svc.upsert_entry(ctx, setup["eur"].id, "2026-09", 100)
    changes = sorted(reports.account_changes(ctx, "2026-08", "2026-10", "CHF"), key=lambda r: r["account"])
    assert changes == [
        {"account": "Bank (CHF)", "start": 1000.0, "end": 1250.56, "change": 250.56},
        {"account": "Euro account (EUR)", "start": 0.0, "end": 90.0, "change": 90.0},  # 100 EUR at 0.90
    ]
    assert reports.account_changes(ctx, "2026-07", "2026-07", "CHF") == []  # no balances yet


def test_account_changes_for_an_account_only_known_at_the_start(ctx, setup, monkeypatch):
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-08", 300)
    real_rows = wealth_svc.account_rows
    monkeypatch.setattr(
        reports.wealth_svc, "account_rows", lambda c, m, v: real_rows(c, m, v) if m == "2026-08" else []
    )
    assert reports.account_changes(ctx, "2026-08", "2026-09", "CHF") == [
        {"account": "Bank (CHF)", "start": 300.0, "end": 0.0, "change": -300.0}
    ]


def test_month_report_with_advances_and_repayments(ctx, setup):
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-08", 1000)
    wealth_svc.upsert_entry(ctx, setup["chf"].id, "2026-09", 1500)
    shared = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 5),
            amount=300,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            is_advance=True,
            advance_mode="amount",
            advance_value=100,
            debtor="Ann",
        ),
    )
    tx_svc.save(  # paid entirely for someone else
        ctx,
        TransactionInput(
            date=date(2026, 9, 6),
            amount=40,
            currency="CHF",
            category="01.2",
            from_account=setup["chf"].id,
            is_advance=True,
            advance_value=100,
            debtor="Ben",
        ),
    )
    _spend(ctx, setup, date(2026, 9, 6), 50, "07.3", currency="EUR", account="eur")  # rate of 2026-08-29
    for inp in (
        TransactionInput(
            date=date(2026, 9, 25),
            type="income",
            amount=2000,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
        TransactionInput(
            date=date(2026, 9, 28),
            type="income",
            amount=60,
            currency="CHF",
            to_account=setup["chf"].id,
            is_advance=True,
            advance_account=shared.advance_account,
            settles_id=shared.id,
        ),
    ):
        tx_svc.save(ctx, inp)
    budget_svc.create(
        ctx, BudgetInput(start_month="2026-09", end_month="2026-10", currency="CHF", amount=250, category="01.1")
    )

    rep = reports.month_report(ctx, "2026-09", "CHF", today=date(2026, 10, 18))
    tree = rep.expenditure_tree
    assert rep.complete
    assert (tree["01.1"].actual, tree["01.1"].budget, tree["01.1"].diff) == (200, 250, -50)
    assert "01.2" not in tree  # its whole amount was advanced
    assert tree["ADV"].actual == 140 and not tree["ADV"].editable
    assert tree["07"].actual == pytest.approx(47.5)
    assert [(line.label, line.actual, line.editable) for line in rep.income_lines] == [
        ("Advance repayments", 60, False),
        ("Salary", 2000, True),
    ]
    assert (rep.wealth_start, rep.wealth_end) == (1000, 1500)
    assert rep.unaccounted == pytest.approx(2060 - 387.5 - 500)
    assert rep.top_expenditure[-1] == "UNACCOUNTED"
    assert rep.total_expenditure.actual == pytest.approx(1560)
    assert rep.total_income.actual == 2060
    assert [r.account.name for r in rep.accounts] == ["Bank"]
    assert [line.key for line in rep.children("01")] == ["01.1"]
    every = rep.children("01", include_empty=True)
    assert "01.2" in [line.key for line in every] and all(line.editable for line in every)

    october = reports.month_report(ctx, "2026-10", "CHF", today=date(2026, 10, 18))
    assert not october.complete
    assert october.unaccounted is None and october.accounts == []
    assert october.projected_wealth is not None


# ------------------------------------------------------------------ budget service edge cases
def test_budget_validation_messages(ctx, setup, english):
    cm = months.current_month()
    base = {"start_month": cm, "end_month": cm, "currency": "CHF", "amount": 10, "category": "01.1"}

    def check(message, **changes):
        with pytest.raises(ValidationError, match=message):
            budget_svc.validate(ctx, BudgetInput(**(base | changes)))

    check("Amount must be a positive number", amount=None)
    check("Amount must be a positive number", amount=-1)
    check("Currency is required", currency="")
    check("Select an expenditure category", category="ADV")
    check("Select an expenditure category", category="99.9")
    check("Select an income category", type="income", category="nope")
    check("Select income or expenditure", type="transfer")
    check("Select the first month", start_month="")
    check("Select the final month", end_month="")
    check(r"cannot be after the budget horizon \(Mar 2027\)", end_month="2027-04")
    check("must not be before the first month", start_month="2026-12", end_month="2026-11")
    check("at least every month", recur_months=0)
    assert budget_svc.validate(ctx, BudgetInput(**(base | {"category": "TAX"}))) == [cm]


def test_update_entry(ctx, setup, english):
    cm = months.current_month()
    [entry] = budget_svc.create(
        ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=10, category="01.1", comment="x")
    )
    with pytest.raises(ValidationError, match="Budget entry not found"):
        budget_svc.update_entry(ctx, "nope", 5)
    with pytest.raises(ValidationError, match="positive"):
        budget_svc.update_entry(ctx, entry.id, 0)
    with pytest.raises(ValidationError, match="Select an expenditure category"):
        budget_svc.update_entry(ctx, entry.id, 5, category="ADV")
    assert ctx.store.budget.get(entry.id).amount == 10  # nothing changed by failed updates
    kept = budget_svc.update_entry(ctx, entry.id, 12.345)
    assert (kept.amount, kept.currency, kept.comment, kept.category) == (12.35, "CHF", "x", "01.1")
    moved = budget_svc.update_entry(ctx, entry.id, 20, "EUR", " y ", "02.1")
    assert (moved.amount, moved.currency, moved.comment, moved.category) == (20, "EUR", "y", "02.1")
    assert moved.amount_chf == 18.0
    assert budget_svc.delete(ctx, [entry.id, "nope"]) == 1


def test_schedule_and_series_helpers(ctx, setup):
    rows = budget_svc.create(
        ctx,
        BudgetInput(
            start_month="2026-10", end_month="2027-02", recur_months=2, currency="CHF", amount=5, category="01.1"
        ),
    )
    assert [r.month for r in rows] == ["2026-10", "2026-12", "2027-02"]
    sched = budget_svc.schedule_of(ctx, rows[0])
    assert (sched.end_month, sched.forever, sched.recur_months) == ("2027-02", False, 2)
    assert [b.month for b in budget_svc.later_in_series(ctx, rows[0])] == ["2026-12", "2027-02"]
    assert budget_svc.later_in_series(ctx, rows[-1]) == []
    forever = budget_svc.create(
        ctx, BudgetInput(start_month="2027-01", forever=True, currency="CHF", amount=7, category="02.1")
    )
    assert budget_svc.schedule_of(ctx, forever[0]).forever is True
    lone = budget_svc.create(
        ctx, BudgetInput(start_month="2026-11", end_month="2026-11", currency="CHF", amount=1, category="01.1")
    )[0]
    lone.series_id = ""
    assert budget_svc.schedule_of(ctx, lone).end_month == "2026-11"
    assert budget_svc.later_in_series(ctx, lone) == []
    assert [b.amount for b in budget_svc.entries_for(ctx, "2026-12", "expenditure", "01.1")] == [5]
    assert budget_svc.entries_for(ctx, "2026-12", "income", "01.1") == []


def test_reschedule_replans_from_an_entry(ctx, setup):
    rows = budget_svc.create(
        ctx, BudgetInput(start_month="2026-10", forever=True, currency="CHF", amount=5, category="01.1")
    )
    middle = rows[2]
    new = budget_svc.reschedule(
        ctx, middle.id, BudgetInput(end_month="2027-01", recur_months=1, currency="CHF", amount=8, comment="new")
    )
    assert [b.month for b in new] == ["2026-12", "2027-01"]
    assert all(b.amount == 8 and b.comment == "new" and not b.forever for b in new)
    earlier = series_of(ctx, rows[0].series_id)
    assert [b.month for b in earlier] == ["2026-10", "2026-11"]
    assert not any(b.forever or b.generated_until for b in earlier)
    assert len(ctx.store.budget.all()) == 4


def series_of(ctx, series_id: str):
    return sorted((b for b in ctx.store.budget.all() if b.series_id == series_id), key=lambda b: b.month)


def test_reschedule_unknown_entry(ctx, setup, english):
    with pytest.raises(ValidationError, match="Budget entry not found"):
        budget_svc.reschedule(ctx, "nope", BudgetInput(end_month="2026-12", currency="CHF", amount=1))


def test_extend_forever_is_idempotent(ctx, setup):
    budget_svc.create(ctx, BudgetInput(start_month="2027-01", forever=True, currency="CHF", amount=7, category="02.1"))
    assert budget_svc.extend_forever(ctx, "2027-03") == 0  # already generated up to the horizon
    assert budget_svc.set_horizon(ctx, "2027-05") == 2
    assert Store(Path(ctx.store.data_dir)).settings.budget_horizon == "2027-05"
    assert budget_svc.extend_forever(ctx, "2027-05") == 0


def test_chart_axis_keeps_decimals_for_small_values() -> None:
    """B15: ',.0f' rounded 0.5 to 1, so an empty chart showed '1 CHF' twice."""
    import plotly.graph_objects as go  # noqa: PLC0415

    from budget_app.ui.theme import chart_layout  # noqa: PLC0415

    axis = chart_layout(go.Figure(), "CHF").layout.yaxis
    assert axis.tickformat == ",~f" and axis.ticksuffix == " CHF"
