"""The phone companion's network side (F35, decision 21): a second listener on the home network, and its gate.

The app's own server stays on 127.0.0.1 (decision 9). Only while the open folder's lock is on and the user has
turned on *Quick Add from a Phone* does `sync_listener` run a second server for the same app, on every interface at
`PORT` (or a free port when that one is taken). `Gate` is an ASGI middleware every request passes, on both
servers: a request from another device reaches only the pairing page, the quick-add page, the cookie route and
NiceGUI's own files and socket, only from a private (home network) address, and only while phones are allowed;
anything else is refused before a route runs. The quick-add page then needs a phone session (`security.allowed`),
which only the pairing code shown in Settings on the computer grants.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import socket
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import uvicorn
import zxingcpp
from nicegui import background_tasks, core
from starlette.responses import PlainTextResponse

from budget_app.services import security

log = logging.getLogger(__name__)

PORT = 8484
ANY = "0.0.0.0"  # noqa: S104 - every interface; the Gate keeps out what is not from the home network
PAGES = ("/quick", "/pair")
PREFIXES = ("/unlock/", "/_nicegui/", "/_nicegui_ws/")


# ------------------------------------------------------------------ who may come in
def admitted(host: str, path: str) -> bool:
    """Whether a request may reach a route: everything from this computer; from other devices only the phone pages
    and what they load, from the home network, while phones are allowed."""
    if security.is_local(host):
        return True
    if not (security.phone_enabled() and security.from_home_network(host)):
        return False
    return path in PAGES or path.startswith(PREFIXES)


class Gate:
    """ASGI middleware: `admitted` for every HTTP request and socket connection."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not admitted(str(client[0]) if client else "", str(scope.get("path", ""))):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await PlainTextResponse("Not available on the network", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ------------------------------------------------------------------ where a phone finds the app
def addresses() -> list[str]:
    """This computer's home-network addresses (IPv4), as a phone would type them; the default route's first."""
    found: list[str] = []
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))  # only picks the outgoing interface: a UDP connect sends nothing
        found.append(str(s.getsockname()[0]))
    with contextlib.suppress(OSError):
        found += [str(info[4][0]) for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    return [h for i, h in enumerate(found) if security.from_home_network(h) and h not in found[:i]]


def port() -> int:
    return _listener.port if _listener is not None else PORT


def urls(path: str = "/pair") -> list[str]:
    return [f"http://{host}:{port()}{path}" for host in addresses()]


def qr_data_url(text: str) -> str:
    """A QR code of `text` as an SVG data URL (zxing-cpp, which already reads the QR bills)."""
    svg = zxingcpp.write_barcode_to_svg(zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode))
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


# ------------------------------------------------------------------ the listener
class _Server(uvicorn.Server):
    """A uvicorn server inside the app's running loop; the app's own server keeps the signal handlers."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


@dataclass
class Listener:
    server: uvicorn.Server
    port: int


_listener: Listener | None = None


def _free_port() -> int:
    for wanted in (PORT, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((ANY, wanted))
            except OSError:
                continue
            return int(s.getsockname()[1])
    raise OSError("no free port")  # pragma: no cover - port 0 always binds


def running() -> bool:
    return _listener is not None


def start() -> None:
    """Serve the app on the home network too (from the app's running loop)."""
    global _listener  # noqa: PLW0603 - one listener per process
    if _listener is not None:
        return
    try:
        free = _free_port()
    except OSError as exc:  # pragma: no cover - see _free_port
        log.warning("Phones can't reach the app: %s", exc)
        return
    config = uvicorn.Config(core.app, host=ANY, port=free, lifespan="off", log_level="warning", ws="wsproto")
    _listener = Listener(_Server(config), free)
    background_tasks.create(_listener.server.serve(), name="phone listener")
    log.info("Phones on the home network can reach the quick-add page on port %d", free)


def stop() -> None:
    global _listener  # noqa: PLW0603 - see start
    if _listener is None:
        return
    _listener.server.should_exit = True
    _listener = None
    log.info("Phones can no longer reach the app")


def sync_listener() -> None:
    """Listen on the home network exactly while phones are allowed for the open folder."""
    if security.phone_enabled():
        start()
    else:
        stop()
