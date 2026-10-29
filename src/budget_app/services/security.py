"""The app lock, encryption at rest and the sessions that pass the lock (F27, decision 20; F35, decision 21).

The state is process-wide, like the data folder: one `Vault` (the `security.json` of the open folder),
the data key once an encrypted folder is unlocked, and the sessions. A *session* is a random token in
the `budget_session` cookie, granted after the password (kind `desktop`) or a pairing code (kind
`phone`, F35), and kept only in memory: every start of the app locks it again. Pages are guarded in
`main.register_pages`: without a session that fits, the request is redirected to the lock page before
anything is built.

The cookie is set by the `/unlock/<nonce>` route, because a page cannot set one after it was sent: the lock
page verifies the password, takes a one-time nonce for the new session and navigates there.

Unlocking an encrypted folder also opens its store: `opener` (set by `main`) runs the same steps as the
startup does for a plain folder. Wrong passwords are answered more slowly each time (`delay`), from the
fourth attempt on, so the lock holds up against guessing from the home network (F35).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fastapi import Request

from budget_app.i18n import t
from budget_app.services import context as context_module
from budget_app.services.context import ValidationError
from budget_app.storage import encryption
from budget_app.storage.backup import BACKUP_DIR, BACKUP_NAME
from budget_app.storage.encryption import Codec
from budget_app.storage.vault import MIN_PASSWORD, Vault, WrongPassword, WrongRecoveryKey

log = logging.getLogger(__name__)

COOKIE = "budget_session"
DESKTOP, PHONE = "desktop", "phone"
NONCE_SECONDS = 60
PAIRING_SECONDS = 10 * 60
PAIRING_ATTEMPTS = 5
FREE_ATTEMPTS = 3  # wrong passwords answered at once; then the wait doubles, up to MAX_DELAY
MAX_DELAY = 30.0


@dataclass
class Session:
    kind: str
    created: float = field(default_factory=time.monotonic)


@dataclass
class Nonce:
    kind: str
    next: str
    expires: float


@dataclass
class Pairing:
    code: str
    expires: float
    attempts: int = 0


@dataclass
class State:
    data_dir: Path
    vault: Vault
    codec: Codec = field(default_factory=Codec)
    sessions: dict[str, Session] = field(default_factory=dict)
    nonces: dict[str, Nonce] = field(default_factory=dict)
    failures: int = 0
    pairing: Pairing | None = None


_state: State | None = None
# set by main: opens the store of `data_dir` with the codec once an encrypted folder is unlocked
opener: Callable[[Path], None] | None = None


def configure(data_dir: Path) -> State:
    """Read the folder's `security.json`; forget every session and key of the folder open before."""
    global _state  # noqa: PLW0603 - one open data folder per process
    _state = State(data_dir, Vault.load(data_dir))
    return _state


def relocate(data_dir: Path) -> State:
    """The open folder's records were moved to `data_dir` (F26): the same security file, key and sessions there."""
    st = state()
    st.data_dir = data_dir
    st.vault = Vault.load(data_dir)
    return st


def state() -> State:
    if _state is None:
        raise RuntimeError("security not configured")
    return _state


def codec() -> Codec:
    return state().codec


# ------------------------------------------------------------------ what the folder requires
def required() -> bool:
    """Whether a password is set (the lock is on)."""
    return _state is not None and _state.vault.has_password


def encrypted() -> bool:
    return _state is not None and _state.vault.encrypted


def phone_enabled() -> bool:
    return required() and state().vault.phone_enabled


def can_open_without_password() -> bool:
    """A plain folder opens at startup; an encrypted one waits for the password."""
    return not encrypted()


# ------------------------------------------------------------------ sessions
def session(request: Request | None) -> Session | None:
    if request is None or _state is None:
        return None
    token = request.cookies.get(COOKIE, "")
    return _state.sessions.get(token)


def allowed(request: Request | None, phone_ok: bool = False) -> bool:
    """Whether the request may see a page: no lock, a desktop session, or a phone session where phones may go."""
    if not required():
        return True
    s = session(request)
    if s is None:
        return False
    return s.kind == DESKTOP or (phone_ok and s.kind == PHONE)


def redirect_for(request: Request | None, path: str, phone_ok: bool = False) -> str | None:
    """Where a request that may not see `path` is sent (None when it may)."""
    if allowed(request, phone_ok):
        return None
    target = "/pair" if phone_ok and _phone_request(request) else "/lock"
    return f"{target}?next={quote(path, safe='/')}" if path not in ("/", target) else target


def _phone_request(request: Request | None) -> bool:
    """A request from another device on the network (the computer itself gets the password page)."""
    host = request.client.host if request is not None and request.client else ""
    return host not in ("", "127.0.0.1", "::1", "localhost", "testclient")


def _grant(kind: str, next_path: str) -> str:
    """A session of `kind`, handed over as a one-time nonce for the cookie route."""
    st = state()
    now = time.monotonic()
    for n in [n for n, v in st.nonces.items() if v.expires < now]:
        del st.nonces[n]
    nonce = secrets.token_urlsafe(24)
    safe = next_path if next_path.startswith("/") and "//" not in next_path else "/"
    st.nonces[nonce] = Nonce(kind, safe, now + NONCE_SECONDS)
    return nonce


def consume(nonce: str) -> tuple[str, str] | None:
    """Turn a nonce into a session: (token, path to go to), or None for an unknown or stale nonce."""
    st = state()
    granted = st.nonces.pop(nonce, None)
    if granted is None or granted.expires < time.monotonic():
        return None
    token = secrets.token_urlsafe(32)
    st.sessions[token] = Session(granted.kind)
    return token, granted.next


def lock() -> None:
    """Lock now: every session, on this computer and on phones, has to pass the lock again."""
    st = state()
    st.sessions.clear()
    st.nonces.clear()
    st.pairing = None


def delay() -> float:
    """Seconds to wait before answering the next password attempt."""
    extra = state().failures - FREE_ATTEMPTS
    return 0.0 if extra < 0 else min(float(2**extra), MAX_DELAY)


def _store_open() -> None:
    """Open the store once the key is known (an encrypted folder), the way the startup does for a plain one."""
    if not context_module.is_open() and opener is not None:
        opener(state().data_dir)


def _set_key(key: bytes | None) -> None:
    """The data key for every file written from now on: the folder's codec, and the open store's (the same object
    when `main` opened the store; a store opened on its own, as in the tests, follows along)."""
    st = state()
    st.codec.key = key
    if context_module.is_open():
        context_module.get_ctx().store.codec.key = key


# ------------------------------------------------------------------ the lock page's actions
async def unlock(password: str, next_path: str = "/") -> str:
    """Check the password (slowly after repeated failures), open the store if need be and grant a desktop session."""
    st = state()
    wait = delay()
    if wait:
        await asyncio.sleep(wait)
    try:
        key = st.vault.unlock(password)
    except WrongPassword as exc:
        st.failures += 1
        raise ValidationError(t("Wrong password")) from exc
    st.failures = 0
    if key is not None:
        _set_key(key)
    _store_open()
    return _grant(DESKTOP, next_path)


async def recover(recovery_key: str, new_password: str, next_path: str = "/") -> str:
    """Forgotten password: set a new one with the recovery key, then unlock with it."""
    st = state()
    wait = delay()
    if wait:
        await asyncio.sleep(wait)
    _check_new_password(new_password)
    try:
        key = st.vault.reset_password(recovery_key, new_password)
    except WrongRecoveryKey as exc:
        st.failures += 1
        raise ValidationError(t("This is not the recovery key of these records")) from exc
    st.failures = 0
    _set_key(key)
    _store_open()
    return _grant(DESKTOP, next_path)


def _check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValidationError(t("The password needs at least {n} characters", n=MIN_PASSWORD))


# ------------------------------------------------------------------ settings
def set_password(password: str, repeat: str, next_path: str = "/settings") -> str:
    """Turn the lock on; returns the nonce that gives this browser its session."""
    st = state()
    _check_new_password(password)
    if password != repeat:
        raise ValidationError(t("The passwords differ"))
    if st.vault.has_password:
        raise ValidationError(t("A password is already set"))
    st.vault.set_password(password)
    return _grant(DESKTOP, next_path)


def change_password(current: str, new: str, repeat: str) -> None:
    st = state()
    _check_new_password(new)
    if new != repeat:
        raise ValidationError(t("The passwords differ"))
    try:
        st.vault.change_password(current, new)
    except WrongPassword as exc:
        raise ValidationError(t("Wrong password")) from exc


def remove_password(current: str) -> None:
    """Turn the lock off (encryption has to be off). Sessions stay; no page needs one any more."""
    st = state()
    if st.vault.encrypted:
        raise ValidationError(t("Turn encryption off before you turn the lock off"))
    try:
        st.vault.remove_password(current)
    except WrongPassword as exc:
        raise ValidationError(t("Wrong password")) from exc
    st.pairing = None


def _folders() -> list[Path]:
    """The open data folder and the app's own backup copies in it: all encrypted or decrypted together."""
    root = state().data_dir
    backups = root / BACKUP_DIR
    copies = (
        sorted(p for p in backups.iterdir() if p.is_dir() and BACKUP_NAME.match(p.name)) if backups.is_dir() else []
    )
    return [root, *copies]


def enable_encryption(password: str) -> str:
    """Encrypt the record files; returns the recovery key, shown once."""
    st = state()
    if st.vault.encrypted:
        raise ValidationError(t("The records are encrypted already"))
    try:
        key, recovery = st.vault.enable_encryption(password)
    except WrongPassword as exc:
        raise ValidationError(t("Wrong password")) from exc
    _set_key(key)  # the key is on file first, so a half-rewritten folder is still readable
    for folder in _folders():
        encryption.reencode(folder, st.codec)
    log.info("Encryption at rest turned on")
    return recovery


def disable_encryption(password: str) -> None:
    """Decrypt the record files, then drop the key material."""
    st = state()
    try:
        st.vault.unlock(password)
    except WrongPassword as exc:
        raise ValidationError(t("Wrong password")) from exc
    if not st.vault.encrypted:
        raise ValidationError(t("The records are not encrypted"))
    for folder in _folders():
        encryption.reencode(folder, st.codec, Codec())  # read with the key, written plain
    st.vault.disable_encryption(password)
    _set_key(None)
    log.info("Encryption at rest turned off")


def new_recovery_key(password: str) -> str:
    try:
        return state().vault.new_recovery_key(password)
    except WrongPassword as exc:
        raise ValidationError(t("Wrong password")) from exc
    except ValueError as exc:
        raise ValidationError(t("The records are not encrypted")) from exc


# ------------------------------------------------------------------ phone companion (F35)
def set_phone(enabled: bool) -> None:
    st = state()
    if enabled and not st.vault.has_password:
        raise ValidationError(t("Set a password first: phones reach the app through the lock"))
    st.vault.set_phone(enabled)
    if not enabled:
        st.pairing = None
        for token in [k for k, s in st.sessions.items() if s.kind == PHONE]:
            del st.sessions[token]


def pairing_code() -> str:
    """A fresh six-digit code, good for ten minutes and five tries; shown in Settings, typed on the phone."""
    st = state()
    st.pairing = Pairing(f"{secrets.randbelow(10**6):06d}", time.monotonic() + PAIRING_SECONDS)
    return st.pairing.code


def pair(code: str, next_path: str = "/quick") -> str:
    """Check a pairing code and grant a phone session; the code is spent, right or wrong after five tries."""
    st = state()
    p = st.pairing
    if not phone_enabled() or p is None or p.expires < time.monotonic():
        st.pairing = None
        raise ValidationError(t("No pairing code is active. Show one in Settings on the computer."))
    p.attempts += 1
    if not secrets.compare_digest(p.code, "".join(code.split())):
        if p.attempts >= PAIRING_ATTEMPTS:
            st.pairing = None
        raise ValidationError(t("Wrong pairing code"))
    st.pairing = None
    return _grant(PHONE, next_path)


def phone_sessions() -> int:
    return sum(1 for s in state().sessions.values() if s.kind == PHONE) if _state else 0
