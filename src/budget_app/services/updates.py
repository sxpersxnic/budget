"""In-app update check (F33): once a day, when the user has turned it on, read the newest release's version.

Off by default. The only request is a read of GitHub's *latest release* API for this repository, the same kind
of read-only download as the ECB rates; it carries nothing about the user or the data, and nothing is ever
downloaded or installed: when the release is newer than the running app, the sidebar links to its page, where
the installers and the update steps are. The answer is read strictly: only a tag of the form `v1.2.3` is kept,
and the link is built from that tag, never taken from the answer.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx

from budget_app.config import RELEASES_API_URL, RELEASES_URL, app_version
from budget_app.i18n import t
from budget_app.services.context import Context

log = logging.getLogger(__name__)

TAG = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")  # 0.2.0, v0.2.0, 1.0; nothing else is a release of this app
MAX_BYTES = 1024 * 1024  # a release's JSON is a few KB; anything larger is not the answer we asked for
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "budget-app"}  # GitHub needs a user agent
TIMEOUT = 10.0


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str  # "0.2.0"
    url: str  # the release page: installers and notes


def parse_version(text: str) -> tuple[int, ...] | None:
    """`0.2.0` or `v0.2.0` → (0, 2, 0); anything else (a dev build, a pre-release) → None."""
    m = TAG.match(text.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str) -> bool:
    """True when both are versions and `candidate` is the later one; a dev build is never behind."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def release_url(tag: str) -> str:
    """The page of a release, built from its (checked) tag; the answer's own links are not used."""
    if not TAG.match(tag):
        raise UpdateError(t("Unexpected answer from the releases page"))
    return f"{RELEASES_URL}/tag/{tag}"


def fetch_latest(url: str = RELEASES_API_URL, transport: httpx.BaseTransport | None = None) -> str:
    """The tag of the newest release (`v0.2.0`), read from GitHub's API. Raises UpdateError."""
    buf = io.BytesIO()
    try:
        with (
            httpx.Client(transport=transport, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client,
            client.stream("GET", url) as resp,
        ):
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                buf.write(chunk)
                if buf.tell() > MAX_BYTES:
                    raise UpdateError(t("Unexpected answer from the releases page"))
        data = json.loads(buf.getvalue().decode("utf-8"))
    except httpx.HTTPError as exc:
        raise UpdateError(t("Could not reach the releases page ({error})", error=exc)) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError(t("Unexpected answer from the releases page")) from exc
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not TAG.match(tag):
        raise UpdateError(t("Unexpected answer from the releases page"))
    return tag


def check(ctx: Context, today: date | None = None, force: bool = False) -> bool:
    """Ask for the newest release if the check is on and not done today (or `force`). Returns True when asked.

    The tag and the day are stored, so the sidebar can show the result on every page. Raises UpdateError
    when the request fails; nothing is stored then, and the next refresh tries again.
    """
    settings = ctx.store.settings
    if not settings.update_check:
        return False
    today = today or date.today()
    if not force and settings.update_checked == today.isoformat():
        return False
    tag = fetch_latest()
    settings.update_latest = tag
    settings.update_checked = today.isoformat()
    ctx.store.save_settings()
    return True


def check_if_due(ctx: Context, today: date | None = None) -> bool:
    """The periodic check (startup, then with every rate refresh): a failure is logged, never shown."""
    try:
        return check(ctx, today)
    except UpdateError as exc:
        log.warning("Update check failed: %s", exc)
        return False


def forget(ctx: Context) -> None:
    """Turning the check off also drops what it found; the sidebar shows nothing the user didn't ask for."""
    settings = ctx.store.settings
    settings.update_check = False
    settings.update_latest = ""
    settings.update_checked = ""
    ctx.store.save_settings()


def available(ctx: Context) -> Release | None:
    """The release found by the last check when it is newer than the running app, else None."""
    tag = ctx.store.settings.update_latest
    if not tag or not is_newer(tag, app_version()):
        return None
    return Release(version=tag.lstrip("v"), url=release_url(tag))


def latest_version(ctx: Context) -> str:
    """The version number of the last check's answer ("0.2.0"), or "" before the first check."""
    return ctx.store.settings.update_latest.lstrip("v")
