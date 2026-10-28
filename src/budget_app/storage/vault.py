"""The app lock's password and the key material of encryption at rest, in `<data>/security.json` (F27).

The file is plain JSON (it has to be readable before anything is unlocked) and holds no secret in the
clear:

- `password`: a salt, the scrypt parameters and a *verifier*. The password is stretched with scrypt
  (`cryptography`'s implementation) to 64 bytes: the first half is the verifier that is stored and
  compared, the second half is the *wrapping key* that is never stored.
- `encryption` (only while encryption is on): the one random *data key* that encrypts the record files
  (`storage.encryption`), wrapped (Fernet-encrypted) twice: with the password's wrapping key, and with
  a key stretched the same way from the *recovery key*, 32 letters and digits shown once when encryption
  is turned on. Either one unwraps the data key; a wrong one fails the token check. Changing the password
  rewraps the data key; the files are not rewritten.
- `phone`: whether phones on the home network may reach the quick-add page (F35, decision 21).

Everything here is file handling around vetted primitives; no primitive is implemented in this app.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FILE_NAME = "security.json"
VERSION = 1
SCRYPT_N = 2**15  # 32 MiB, well under 100 ms on a laptop; stored per entry so it can be raised later
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD = 8
RECOVERY_BYTES = 20  # 160 bits: 32 characters of base32, shown in groups of four
_B32_FIXES = str.maketrans({"0": "O", "1": "I", "8": "B"})  # what a reader may mistake


class WrongPassword(ValueError):
    pass


class WrongRecoveryKey(ValueError):
    pass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _stretch(secret: str, salt: bytes, n: int, r: int, p: int) -> tuple[bytes, bytes]:
    """(verifier, wrapping key) from a password or recovery key."""
    derived = Scrypt(salt=salt, length=64, n=n, r=r, p=p).derive(secret.encode("utf-8"))
    return derived[:32], base64.urlsafe_b64encode(derived[32:])


def _new_entry(secret: str) -> dict[str, Any]:
    """A fresh salt and verifier for `secret`, with the key stretched alongside (returned under `_key`)."""
    salt = os.urandom(16)
    verifier, key = _stretch(secret, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return {"salt": _b64(salt), "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "verifier": _b64(verifier), "_key": key}


def _check(entry: dict[str, Any], secret: str) -> bytes:
    """The wrapping key of `secret` if it matches the entry's verifier; raises InvalidKey otherwise."""
    kdf = Scrypt(salt=_unb64(entry["salt"]), length=64, n=int(entry["n"]), r=int(entry["r"]), p=int(entry["p"]))
    derived = kdf.derive(secret.encode("utf-8"))
    if not secrets.compare_digest(derived[:32], _unb64(entry["verifier"])):
        raise InvalidKey
    return base64.urlsafe_b64encode(derived[32:])


def new_recovery_key() -> str:
    """32 base32 characters in eight groups of four: `ABCD-EFGH-…`."""
    raw = base64.b32encode(os.urandom(RECOVERY_BYTES)).decode("ascii")
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def normalize_recovery_key(text: str) -> str:
    """What the user typed, as the key was shown: upper case, no spaces or dashes, look-alikes fixed."""
    return "".join(text.split()).replace("-", "").upper().translate(_B32_FIXES)


@dataclass
class Vault:
    """The security file of one data folder, loaded in memory and saved on every change."""

    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path) -> Vault:
        path = data_dir / FILE_NAME
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return cls(path, data if isinstance(data, dict) else {})

    # ------------------------------------------------------------------ state
    @property
    def has_password(self) -> bool:
        return "password" in self.data

    @property
    def encrypted(self) -> bool:
        return "encryption" in self.data

    @property
    def phone_enabled(self) -> bool:
        return bool(self.data.get("phone", {}).get("enabled"))

    def save(self) -> None:
        if not {k for k in self.data if k != "version"}:  # nothing left to keep: no file
            self.data.clear()
            self.path.unlink(missing_ok=True)
            return
        self.data["version"] = VERSION
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)

    # ------------------------------------------------------------------ the lock
    def set_password(self, password: str) -> None:
        """Turn the lock on (no password yet). Raises ValueError for a short password."""
        if self.has_password:
            raise ValueError("a password is already set")
        self._store_password(password)

    def verify(self, password: str) -> bytes:
        """The password's wrapping key; raises WrongPassword."""
        try:
            return _check(self.data["password"], password)
        except (InvalidKey, KeyError) as exc:
            raise WrongPassword from exc

    def change_password(self, current: str, new: str) -> None:
        wrap = self.verify(current)
        key = self._unwrap(self.data["encryption"]["password"], wrap) if self.encrypted else None
        self._store_password(new, key)

    def remove_password(self, password: str) -> None:
        """Turn the lock off; encryption has to be off first."""
        self.verify(password)
        if self.encrypted:
            raise ValueError("turn encryption off first")
        self.data.pop("password", None)
        self.save()

    def _store_password(self, password: str, data_key: bytes | None = None) -> None:
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"the password needs at least {MIN_PASSWORD} characters")
        entry = _new_entry(password)
        wrap = entry.pop("_key")
        self.data["password"] = entry
        if data_key is not None:
            self.data["encryption"]["password"] = _wrap_entry(wrap, data_key)
        self.save()

    # ------------------------------------------------------------------ encryption
    def enable_encryption(self, password: str) -> tuple[bytes, str]:
        """Create the data key, wrapped with the password and with a new recovery key; returns both."""
        wrap = self.verify(password)
        if self.encrypted:
            raise ValueError("encryption is already on")
        data_key = Fernet.generate_key()
        recovery = new_recovery_key()
        self.data["encryption"] = {
            "password": _wrap_entry(wrap, data_key),
            "recovery": _recovery_entry(recovery, data_key),
        }
        self.save()
        return data_key, recovery

    def unlock(self, password: str) -> bytes | None:
        """The data key for `password` (None when encryption is off); raises WrongPassword."""
        wrap = self.verify(password)
        if not self.encrypted:
            return None
        return self._unwrap(self.data["encryption"]["password"], wrap)

    def unlock_with_recovery(self, recovery_key: str) -> bytes:
        """The data key for the recovery key; raises WrongRecoveryKey."""
        if not self.encrypted:
            raise WrongRecoveryKey
        entry = self.data["encryption"]["recovery"]
        try:
            wrap = _check(entry, normalize_recovery_key(recovery_key))
            return self._unwrap(entry, wrap)
        except (InvalidKey, WrongPassword) as exc:
            raise WrongRecoveryKey from exc

    def reset_password(self, recovery_key: str, new_password: str) -> bytes:
        """Forgotten password: a new one, proven by the recovery key. Returns the data key."""
        data_key = self.unlock_with_recovery(recovery_key)
        self._store_password(new_password, data_key)
        return data_key

    def new_recovery_key(self, password: str) -> str:
        """Replace the recovery key (the old one stops working)."""
        data_key = self.unlock(password)
        if data_key is None:
            raise ValueError("encryption is off")
        recovery = new_recovery_key()
        self.data["encryption"]["recovery"] = _recovery_entry(recovery, data_key)
        self.save()
        return recovery

    def disable_encryption(self, password: str) -> bytes:
        """Drop the key material; the caller decrypts the files with the returned data key first."""
        data_key = self.unlock(password)
        if data_key is None:
            raise ValueError("encryption is off")
        del self.data["encryption"]
        self.save()
        return data_key

    @staticmethod
    def _unwrap(entry: dict[str, Any], wrap: bytes) -> bytes:
        try:
            return Fernet(wrap).decrypt(entry["wrapped"].encode("ascii"))
        except InvalidToken as exc:
            raise WrongPassword from exc

    # ------------------------------------------------------------------ phone companion (F35)
    def set_phone(self, enabled: bool) -> None:
        self.data["phone"] = {"enabled": bool(enabled)}
        self.save()


def _wrap_entry(wrap: bytes, data_key: bytes) -> dict[str, Any]:
    """The data key wrapped with the password's wrapping key (the password entry holds the salt)."""
    return {"wrapped": Fernet(wrap).encrypt(data_key).decode("ascii")}


def _recovery_entry(recovery: str, data_key: bytes) -> dict[str, Any]:
    entry = _new_entry(normalize_recovery_key(recovery))
    wrap = entry.pop("_key")
    entry["wrapped"] = Fernet(wrap).encrypt(data_key).decode("ascii")
    return entry
