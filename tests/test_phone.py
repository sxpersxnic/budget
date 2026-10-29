"""Phone companion (F35): who a phone is, what it may reach, the pairing code and the home-network listener."""

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from budget_app import main as main_module
from budget_app.services import phone, security
from budget_app.services.context import ValidationError
from budget_app.storage import vault

PW = "correct horse battery"
PHONE = "192.168.1.20"


@pytest.fixture(autouse=True)
def sec(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "SCRYPT_N", 2**8)
    monkeypatch.setattr(security, "on_phone_change", None)
    security.configure(tmp_path)
    yield
    phone.stop()


def request(host: str, token: str = "") -> Request:
    headers = [(b"cookie", f"{security.COOKIE}={token}".encode())] if token else []
    return Request({"type": "http", "client": (host, 5555), "headers": headers, "path": "/", "query_string": b""})


def test_addresses_tell_this_computer_from_the_home_network():
    assert security.is_local("127.0.0.1") and security.is_local("::1") and security.is_local("")
    assert not security.is_local(PHONE)
    for home in (PHONE, "10.0.0.7", "172.20.1.1", "fe80::1", "fd12::3"):
        assert security.from_home_network(home), home
    for away in ("8.8.8.8", "2a00:1450::1", "127.0.0.1", "0.0.0.0", "not an address"):  # noqa: S104
        assert not security.from_home_network(away), away


def test_phones_get_nothing_until_phones_are_allowed_and_paired():
    assert security.allowed(request("127.0.0.1"))  # no lock: this computer may
    assert not security.allowed(request(PHONE), phone_ok=True)  # other devices never without a phone session
    with pytest.raises(ValidationError, match="Set a password first"):
        security.set_phone(True)
    security.set_password(PW, PW)
    with pytest.raises(ValidationError, match="No pairing code is active"):
        security.pair("123456")
    changes = []
    security.on_phone_change = lambda: changes.append(security.phone_enabled())
    security.set_phone(True)
    assert changes == [True] and security.phone_enabled()
    assert security.redirect_for(request(PHONE), "/quick", phone_ok=True) == "/pair?next=/quick"
    assert security.redirect_for(request(PHONE), "/settings") == "/pair"  # nothing else for phones
    assert security.redirect_for(request("127.0.0.1"), "/quick", phone_ok=True) == "/lock?next=/quick"
    code = security.pairing_code()
    assert len(code) == 6 and code.isdigit()
    for _ in range(security.PAIRING_ATTEMPTS - 1):
        with pytest.raises(ValidationError, match="Wrong pairing code"):
            security.pair("not it")
    nonce = security.pair(f" {code[:3]} {code[3:]} ")  # typed with spaces
    token, target = security.consume(nonce)
    assert target == "/quick" and security.phone_sessions() == 1
    assert security.allowed(request(PHONE, token), phone_ok=True)
    assert not security.allowed(request(PHONE, token))  # only the phone pages
    assert not security.allowed(request("8.8.8.8", token), phone_ok=True)  # not from away
    assert security.allowed(request("127.0.0.1", token), phone_ok=True)  # a phone session on this computer: quick add
    assert not security.allowed(request("127.0.0.1", token))
    with pytest.raises(ValidationError, match="No pairing code is active"):
        security.pair(code)  # spent
    security.set_phone(False)
    assert changes == [True, False] and security.phone_sessions() == 0
    assert not security.allowed(request(PHONE, token), phone_ok=True)


def test_a_wrong_code_five_times_ends_the_code():
    security.set_password(PW, PW)
    security.set_phone(True)
    code = security.pairing_code()
    for _ in range(security.PAIRING_ATTEMPTS):
        with pytest.raises(ValidationError, match="Wrong pairing code"):
            security.pair("000000" if code != "000000" else "111111")
    with pytest.raises(ValidationError, match="No pairing code is active"):
        security.pair(code)


def test_turning_the_lock_off_ends_phone_access():
    security.set_password(PW, PW)
    security.set_phone(True)
    token, _ = security.consume(security.pair(security.pairing_code()))
    changes = []
    security.on_phone_change = lambda: changes.append(security.phone_enabled())
    security.remove_password(PW)
    assert changes == [False] and not security.phone_enabled() and security.phone_sessions() == 0
    assert not security.allowed(request(PHONE, token), phone_ok=True)


# ------------------------------------------------------------------ the gate
def gated_app() -> FastAPI:
    async def ok(request):
        return PlainTextResponse("ok")

    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text("hi")
        await websocket.close()

    api = FastAPI(routes=[Route(p, ok) for p in ("/", "/quick", "/pair", "/unlock/x", "/_nicegui/x.js")])
    api.router.routes.append(WebSocketRoute("/_nicegui_ws/socket", ws))
    api.add_middleware(phone.Gate)
    return api


async def get(path: str, host: str) -> int:
    transport = httpx.ASGITransport(app=gated_app(), client=(host, 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://budget") as client:
        return (await client.get(path)).status_code


async def test_the_gate_lets_phones_reach_the_phone_pages_only():
    assert await get("/", "127.0.0.1") == 200
    assert await get("/quick", PHONE) == 403  # phones not allowed yet
    security.set_password(PW, PW)
    security.set_phone(True)
    for path in ("/quick", "/pair", "/unlock/x", "/_nicegui/x.js"):
        assert await get(path, PHONE) == 200, path
    assert await get("/", PHONE) == 403
    assert await get("/quick", "8.8.8.8") == 403  # from away


async def test_the_gate_closes_sockets_of_devices_it_does_not_admit():
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    gate = phone.Gate(gated_app())
    await gate({"type": "websocket", "client": (PHONE, 1), "path": "/_nicegui_ws/socket", "headers": []}, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]
    passed = []

    async def inner(scope, receive, send):
        passed.append(scope["type"])

    await phone.Gate(inner)({"type": "lifespan"}, receive, send)
    assert passed == ["lifespan"]


# ------------------------------------------------------------------ the listener
def test_the_listener_runs_exactly_while_phones_are_allowed(monkeypatch):
    started = []
    monkeypatch.setattr(phone, "_free_port", lambda: 18484)
    monkeypatch.setattr(phone.background_tasks, "create", lambda coro, name: (coro.close(), started.append(name)))
    phone.sync_listener()
    assert not phone.running() and phone.port() == phone.PORT
    security.set_password(PW, PW)
    security.on_phone_change = phone.sync_listener
    security.set_phone(True)
    assert phone.running() and phone.port() == 18484 and started == ["phone listener"]
    server = phone._listener.server
    phone.start()  # already running
    assert started == ["phone listener"]
    with server.capture_signals():  # the app's own server keeps the signals
        pass
    security.set_phone(False)
    assert not phone.running() and server.should_exit
    phone.stop()  # already stopped


def test_a_free_port_is_found_when_the_usual_one_is_taken(monkeypatch):
    bound = []

    class FakeSocket:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def bind(self, address):
            if address[1] == phone.PORT:
                raise OSError("in use")
            bound.append(address)

        def getsockname(self):
            return ("0.0.0.0", 50123)  # noqa: S104

    monkeypatch.setattr(phone.socket, "socket", FakeSocket)
    assert phone._free_port() == 50123 and bound == [(phone.ANY, 0)]


def test_the_address_a_phone_types(monkeypatch):
    class FakeUdp:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect(self, address):
            pass

        def getsockname(self):
            return ("192.168.1.5", 40000)

    monkeypatch.setattr(phone.socket, "socket", FakeUdp)
    monkeypatch.setattr(
        phone.socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("192.168.1.5", 0)), (2, 1, 6, "", ("10.0.0.2", 0))]
    )
    monkeypatch.setattr(phone.socket, "gethostname", lambda: "mac")
    assert phone.addresses() == ["192.168.1.5", "10.0.0.2"]
    assert phone.urls() == ["http://192.168.1.5:8484/pair", "http://10.0.0.2:8484/pair"]

    def offline(*args):
        raise OSError("no network")

    monkeypatch.setattr(phone.socket, "getaddrinfo", offline)
    monkeypatch.setattr(FakeUdp, "connect", lambda self, address: offline())
    assert phone.addresses() == []


def test_the_qr_code_is_an_svg_image():
    url = phone.qr_data_url("http://192.168.1.5:8484/pair")
    assert url.startswith("data:image/svg+xml;base64,")


def test_an_explicit_host_trusts_the_network(monkeypatch):
    monkeypatch.setattr(security, "trust_network", True)
    assert security.is_local(PHONE) and phone.admitted("8.8.8.8", "/settings")


def test_main_admits_the_phone_pages_behind_the_lock():
    assert "/pair" in main_module.OPEN_PAGES and "/quick" in main_module.PHONE_PAGES
    assert "/quick" not in main_module.PAGES
