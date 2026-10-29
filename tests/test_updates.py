"""In-app update check (F33): versions, the mocked request, once a day, off by default, nothing downloaded."""

import json
from datetime import date

import httpx
import pytest

from budget_app import config
from budget_app.services import updates
from budget_app.services.updates import UpdateError, is_newer, parse_version
from budget_app.storage.store import Store


class FakeGitHub(list):
    """Answers the releases API through httpx.MockTransport (no network); records every URL asked."""

    def __init__(self, tag="v0.2.0", status=200, body=None):
        super().__init__()
        self.tag, self.status, self.body = tag, status, body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.append(str(request.url))
        assert request.headers["User-Agent"] == "budget-app"
        assert request.headers["Accept"] == "application/vnd.github+json"
        body = self.body if self.body is not None else json.dumps({"tag_name": self.tag, "html_url": "x"}).encode()
        return httpx.Response(self.status, content=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


@pytest.fixture
def github(monkeypatch):
    """`fetch_latest` goes through the fake; tests swap the answer on the fake."""
    server = FakeGitHub()
    original = updates.fetch_latest
    monkeypatch.setattr(updates, "fetch_latest", lambda url=config.RELEASES_API_URL: original(url, server.transport))
    return server


# ------------------------------------------------------------------ versions
@pytest.mark.parametrize(
    ("text", "parsed"),
    [("0.2.0", (0, 2, 0)), ("v0.2.0", (0, 2, 0)), (" 1.0 ", (1, 0)), ("dev", None), ("0.2.0-rc1", None), ("", None)],
)
def test_parse_version(text, parsed):
    assert parse_version(text) == parsed


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("v0.2.0", "0.1.0", True),
        ("0.1.1", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.1", "0.1.0", False),
        ("1.0", "0.9.9", True),
        ("0.2.0", "dev", False),  # a dev build is never behind
        ("nonsense", "0.1.0", False),
    ],
)
def test_is_newer(candidate, current, newer):
    assert is_newer(candidate, current) is newer


def test_release_url_is_built_from_the_tag_only():
    assert updates.release_url("v0.2.0") == f"https://github.com/{config.GITHUB_REPO}/releases/tag/v0.2.0"
    with pytest.raises(UpdateError):
        updates.release_url("../evil")


# ------------------------------------------------------------------ the request
def test_fetch_reads_the_tag(github):
    assert updates.fetch_latest() == "v0.2.0"
    assert github == [config.RELEASES_API_URL]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, None),
        (200, b"not json"),
        (200, json.dumps({"tag_name": "nightly"}).encode()),
        (200, json.dumps({"tag_name": 42}).encode()),
        (200, json.dumps(["v0.2.0"]).encode()),
        (200, b"\xff\xfe"),
        (200, b'{"tag_name": "v0.2.0", "body": "' + b"x" * (updates.MAX_BYTES + 10) + b'"}'),
    ],
)
def test_fetch_refuses_unexpected_answers(github, status, body):
    github.status, github.body = status, body
    with pytest.raises(UpdateError):
        updates.fetch_latest()


def test_fetch_reports_a_network_failure():
    def down(request):
        raise httpx.ConnectError("no network")

    with pytest.raises(UpdateError, match="Could not reach"):
        updates.fetch_latest(transport=httpx.MockTransport(down))


# ------------------------------------------------------------------ the check
def test_off_by_default_makes_no_request(ctx, github):
    assert ctx.store.settings.update_check is False
    assert updates.check(ctx) is False
    assert updates.check_if_due(ctx) is False
    assert github == []
    assert updates.available(ctx) is None


def test_checks_once_a_day_when_on(ctx, github, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "app_version", lambda: "0.1.0")
    monkeypatch.setattr(updates, "app_version", lambda: "0.1.0")
    ctx.store.settings.update_check = True
    day = date(2026, 10, 28)
    assert updates.check(ctx, day) is True
    assert updates.check(ctx, day) is False  # done today
    assert updates.check_if_due(ctx, day) is False
    assert len(github) == 1
    assert updates.check_if_due(ctx, date(2026, 10, 29)) is True  # the next day asks again
    assert len(github) == 2
    assert updates.check(ctx, date(2026, 10, 29), force=True) is True  # Check Now
    assert len(github) == 3
    saved = Store(tmp_path).settings
    assert (saved.update_latest, saved.update_checked) == ("v0.2.0", "2026-10-29")
    release = updates.available(ctx)
    assert release is not None
    assert (release.version, release.url) == ("0.2.0", f"https://github.com/{config.GITHUB_REPO}/releases/tag/v0.2.0")
    assert updates.latest_version(ctx) == "0.2.0"


def test_nothing_to_show_when_the_app_is_current(ctx, github, monkeypatch):
    monkeypatch.setattr(updates, "app_version", lambda: "0.2.0")
    ctx.store.settings.update_check = True
    updates.check(ctx)
    assert updates.available(ctx) is None
    assert updates.latest_version(ctx) == "0.2.0"


def test_a_failed_check_keeps_the_last_result_and_is_logged(ctx, github, caplog):
    ctx.store.settings.update_check = True
    updates.check(ctx, date(2026, 10, 28))
    github.status = 500
    with pytest.raises(UpdateError):
        updates.check(ctx, date(2026, 10, 29))
    assert updates.check_if_due(ctx, date(2026, 10, 29)) is False
    assert "Update check failed" in caplog.text
    assert (ctx.store.settings.update_latest, ctx.store.settings.update_checked) == ("v0.2.0", "2026-10-28")
    assert len(github) == 3  # a failure is tried again at the next refresh


def test_turning_off_forgets_the_result(ctx, github, tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "app_version", lambda: "0.1.0")
    ctx.store.settings.update_check = True
    updates.check(ctx)
    assert updates.available(ctx) is not None
    updates.forget(ctx)
    saved = Store(tmp_path).settings
    assert (saved.update_check, saved.update_latest, saved.update_checked) == (False, "", "")
    assert updates.available(ctx) is None
    assert updates.latest_version(ctx) == ""


def test_the_real_urls_point_at_the_repository():
    assert config.GITHUB_REPO == "sxpersxnic/budget"
    assert config.RELEASES_API_URL == "https://api.github.com/repos/sxpersxnic/budget/releases/latest"
    assert config.RELEASES_URL == "https://github.com/sxpersxnic/budget/releases"
