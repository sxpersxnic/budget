"""App lock and encryption at rest (F27): the codec, the vault, the store, backups and the sessions."""

import json
import re
import types
import zipfile
from datetime import datetime
from io import BytesIO

import httpx
import pytest
from fastapi import FastAPI

from budget_app.domain.models import Account
from budget_app.services import archive, security
from budget_app.services import attachments as att_svc
from budget_app.services import context as context_module
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.storage import backup, encryption, vault
from budget_app.storage.encryption import MAGIC, Codec, EncryptedError, WrongKeyError
from budget_app.storage.store import Store
from budget_app.storage.vault import Vault, WrongPassword, WrongRecoveryKey
from budget_app.ui.widgets import attachments as att_ui

PW = "correct horse battery"
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
RECOVERY = re.compile(r"^[A-Z2-7]{4}(-[A-Z2-7]{4}){7}$")


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """scrypt at a fraction of its cost: the tests derive dozens of keys."""
    monkeypatch.setattr(vault, "SCRYPT_N", 2**8)


def request(token: str | None = None, host: str = "127.0.0.1"):
    cookies = {security.COOKIE: token} if token else {}
    return types.SimpleNamespace(cookies=cookies, client=types.SimpleNamespace(host=host))


@pytest.fixture
def sec(ctx):
    """The security state of the test folder, with its store open as in the app."""
    state = security.configure(ctx.store.data_dir)
    context_module._ctx = ctx
    yield state
    context_module._ctx = None
    security.opener = None


def expense(ctx, setup, amount: float = 12.5):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=datetime(2026, 9, 10).date(),
            type="expenditure",
            amount=amount,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
        ),
    )


# ------------------------------------------------------------------ the codec
def test_plain_codec_writes_what_it_reads(tmp_path):
    path = tmp_path / "a.csv"
    encryption.write_text(path, "name,id\nBank,a1\n", Codec())
    assert path.read_bytes() == b"name,id\nBank,a1\n"
    assert encryption.read_text(path) == "name,id\nBank,a1\n"
    assert not encryption.is_encrypted(path)
    assert not (tmp_path / "a.csv.tmp").exists()


def test_encrypted_codec_hides_the_text_and_reads_it_back(tmp_path):
    from cryptography.fernet import Fernet  # noqa: PLC0415

    key = Fernet.generate_key()
    codec = Codec(key)
    path = tmp_path / "a.csv"
    encryption.write_text(path, "name,id\nBank Secret,a1\n", codec)
    raw = path.read_bytes()
    assert raw.startswith(MAGIC) and b"Secret" not in raw
    assert encryption.is_encrypted(path)
    assert encryption.read_text(path, codec) == "name,id\nBank Secret,a1\n"
    assert Codec(key).decode(b"plain text") == "plain text"  # a plain file reads with a key too
    with pytest.raises(EncryptedError):
        encryption.read_text(path)
    with pytest.raises(WrongKeyError):
        encryption.read_text(path, Codec(Fernet.generate_key()))
    with pytest.raises(WrongKeyError):  # a tampered file fails the check
        Codec(key).decode(raw[:-3] + b"abc")


def test_reencode_rewrites_the_record_files_only(tmp_path):
    from cryptography.fernet import Fernet  # noqa: PLC0415

    store = Store(tmp_path)
    store.accounts.add(Account(name="Bank", currency="CHF"))
    store.write_json("import_profiles.json", [{"name": "UBS"}])
    (tmp_path / "security.json").write_text("{}")
    (tmp_path / "profiles.json").write_text("{}")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "x.pdf").write_bytes(PDF)
    (tmp_path / ".lock").write_text("1")
    codec = Codec(Fernet.generate_key())
    n = encryption.reencode(tmp_path, codec)
    records = [p.name for p in encryption.record_files(tmp_path)]
    assert n == len(records) and "history.jsonl" in records and "import_profiles.json" in records
    assert all(encryption.is_encrypted(p) for p in encryption.record_files(tmp_path))
    for name in ("security.json", "profiles.json", ".lock"):
        assert not encryption.is_encrypted(tmp_path / name)
    assert (tmp_path / "attachments" / "x.pdf").read_bytes() == PDF
    # back to plain: files already plain are left alone
    assert encryption.reencode(tmp_path, codec, Codec()) == n
    assert encryption.reencode(tmp_path, Codec()) == 0
    assert Store(tmp_path).accounts.all()[0].name == "Bank"


# ------------------------------------------------------------------ the vault
def test_password_is_set_verified_changed_and_removed(tmp_path):
    v = Vault.load(tmp_path)
    assert not v.has_password and not v.encrypted
    with pytest.raises(ValueError, match="at least"):
        v.set_password("short")
    v.set_password(PW)
    with pytest.raises(ValueError, match="already"):
        v.set_password(PW)
    saved = json.loads((tmp_path / vault.FILE_NAME).read_text())
    assert saved["version"] == 1 and PW not in json.dumps(saved)
    assert saved["password"]["n"] == 2**8  # the parameters travel with the file
    again = Vault.load(tmp_path)
    assert again.has_password
    again.verify(PW)
    with pytest.raises(WrongPassword):
        again.verify("wrong")
    assert again.unlock(PW) is None  # no encryption: nothing to unwrap
    again.change_password(PW, "another password")
    with pytest.raises(WrongPassword):
        again.change_password(PW, "whatever else")
    Vault.load(tmp_path).verify("another password")
    again.remove_password("another password")
    assert not (tmp_path / vault.FILE_NAME).exists()


def test_recovery_keys_are_readable_and_normalized():
    key = vault.new_recovery_key()
    assert RECOVERY.match(key)
    assert vault.normalize_recovery_key(key.lower().replace("-", " ")) == key.replace("-", "")
    assert vault.normalize_recovery_key("0ab1-8") == "OABIB"


def test_encryption_wraps_one_key_with_the_password_and_the_recovery_key(tmp_path):
    v = Vault.load(tmp_path)
    v.set_password(PW)
    with pytest.raises(WrongPassword):
        v.enable_encryption("wrong")
    key, recovery = v.enable_encryption(PW)
    assert RECOVERY.match(recovery)
    with pytest.raises(ValueError, match="already on"):
        v.enable_encryption(PW)
    saved = (tmp_path / vault.FILE_NAME).read_text()
    assert key.decode() not in saved and recovery not in saved
    again = Vault.load(tmp_path)
    assert again.encrypted
    assert again.unlock(PW) == key
    assert again.unlock_with_recovery(recovery.lower()) == key
    with pytest.raises(WrongPassword):
        again.unlock("wrong")
    with pytest.raises(WrongRecoveryKey):
        again.unlock_with_recovery("AAAA-" * 8)
    # the password changes, the data key does not
    again.change_password(PW, "a new password")
    assert Vault.load(tmp_path).unlock("a new password") == key
    with pytest.raises(ValueError, match="encryption off"):
        again.remove_password("a new password")
    # forgotten: the recovery key proves a new password
    with pytest.raises(WrongRecoveryKey):
        again.reset_password("not it", "whatever else")
    assert again.reset_password(recovery, "third password") == key
    assert Vault.load(tmp_path).unlock("third password") == key
    # a new recovery key retires the old one
    fresh = again.new_recovery_key("third password")
    assert fresh != recovery and again.unlock_with_recovery(fresh) == key
    with pytest.raises(WrongRecoveryKey):
        again.unlock_with_recovery(recovery)
    assert again.disable_encryption("third password") == key
    assert not Vault.load(tmp_path).encrypted
    with pytest.raises(WrongRecoveryKey):
        again.unlock_with_recovery(fresh)
    with pytest.raises(ValueError, match="off"):
        again.new_recovery_key("third password")
    with pytest.raises(ValueError, match="off"):
        again.disable_encryption("third password")


def test_phone_flag_lives_in_the_vault(tmp_path):
    v = Vault.load(tmp_path)
    assert not v.phone_enabled
    v.set_phone(True)
    assert Vault.load(tmp_path).phone_enabled
    (tmp_path / vault.FILE_NAME).write_text("[1, 2]")  # not an object: ignored
    assert not Vault.load(tmp_path).has_password


# ------------------------------------------------------------------ the store and backups
def test_an_encrypted_store_reads_only_with_its_key(tmp_path):
    from cryptography.fernet import Fernet  # noqa: PLC0415

    key = Fernet.generate_key()
    store = Store(tmp_path, Codec(key))
    store.accounts.add(Account(name="Bank Secret", currency="CHF"))
    store.settings.display_currency = "EUR"
    store.save_settings()
    store.write_json("import_accounts.json", {"CH93": "a1"})
    for name in ("accounts.csv", "settings.json", "history.jsonl", "import_accounts.json"):
        data = (tmp_path / name).read_bytes()
        assert data.startswith(MAGIC) and b"Secret" not in data and b"EUR" not in data and b"CH93" not in data
    again = Store(tmp_path, Codec(key))
    assert again.accounts.all()[0].name == "Bank Secret" and again.settings.display_currency == "EUR"
    assert again.read_json("import_accounts.json") == {"CH93": "a1"} and again.read_json("none.json") is None
    assert again.history.actions()[0].changes[0].table == "accounts"
    with pytest.raises(EncryptedError):
        Store(tmp_path)


def test_key_material_and_the_profile_registry_are_not_records(tmp_path):
    Store(tmp_path)
    (tmp_path / "security.json").write_text("{}")
    (tmp_path / "profiles.json").write_text("{}")
    (tmp_path / ".hidden.json").write_text("{}")
    names = [p.name for p in backup.data_files(tmp_path)]
    assert "settings.json" in names and "accounts.csv" in names
    assert not {"security.json", "profiles.json", ".hidden.json"} & set(names)


def test_a_backup_is_plain_and_restores_into_an_encrypted_folder(ctx, setup, sec):
    security.set_password(PW, PW)
    tx = expense(ctx, setup)
    att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    security.enable_encryption(PW)
    assert ctx.store.codec.encrypting
    assert encryption.is_encrypted(ctx.store.data_dir / "transactions.csv")
    data = archive.create(ctx)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert "security.json" not in names
        assert not zf.read("transactions.csv").startswith(MAGIC) and b"01.1" in zf.read("transactions.csv")
    expense(ctx, setup, 99)  # a change after the backup
    files = archive.read(data)
    archive.check(files, ctx.store.codec)
    archive.restore(ctx, files)
    assert [t.amount for t in ctx.store.transactions.all()] == [12.5]
    assert ctx.store.codec.encrypting and encryption.is_encrypted(ctx.store.data_dir / "transactions.csv")
    assert security.required() and security.encrypted()  # the vault was not touched by the restore
    # the app's own copies are encrypted with the same key and restore through the codec
    [saved] = archive.saved_backups(ctx)
    copies = archive.files_in(saved.path)
    assert copies["transactions.csv"].startswith(MAGIC)
    archive.check(copies, ctx.store.codec)
    with pytest.raises(ValidationError, match="can’t be read"):
        archive.check(copies)  # without the key


def test_a_backup_may_not_smuggle_in_key_material(ctx):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(archive.MANIFEST, json.dumps({"format": archive.FORMAT, "version": 1}))
        zf.writestr("accounts.csv", "name,currency,kind,created,asset_class,invest_from,id\n")
        zf.writestr("security.json", "{}")
    with pytest.raises(ValidationError, match=r"unexpected file: security\.json"):
        archive.read(buf.getvalue())


# ------------------------------------------------------------------ the sessions
def test_without_a_password_everything_is_allowed(sec):
    assert not security.required() and not security.encrypted() and security.can_open_without_password()
    assert security.allowed(request()) and security.redirect_for(request(), "/budget") is None
    assert security.session(None) is None


async def test_the_lock_grants_sessions_through_one_time_nonces(sec):
    with pytest.raises(ValidationError, match="differ"):
        security.set_password(PW, PW + "x")
    with pytest.raises(ValidationError, match="at least"):
        security.set_password("short", "short")
    nonce = security.set_password(PW, PW, "/settings")
    with pytest.raises(ValidationError, match="already"):
        security.set_password(PW, PW)
    assert security.required()
    assert security.redirect_for(request(), "/budget") == "/lock?next=/budget"
    assert security.redirect_for(request(), "/") == "/lock"
    assert security.redirect_for(request(), "/transactions?tag=a&month=2026-10") == (
        "/lock?next=/transactions%3Ftag%3Da%26month%3D2026-10"
    )
    assert security.redirect_for(request(), "/lock") == "/lock"
    token, target = security.consume(nonce)
    assert target == "/settings" and security.consume(nonce) is None and security.consume("made up") is None
    assert security.allowed(request(token)) and security.session(request(token)).kind == security.DESKTOP
    assert not security.allowed(request("other"))
    # the lock page: wrong, then right
    with pytest.raises(ValidationError, match="Wrong password"):
        await security.unlock("wrong", "/tax")
    assert sec.failures == 1
    nonce = await security.unlock(PW, "/tax")
    assert sec.failures == 0
    token2, target = security.consume(nonce)
    assert target == "/tax" and security.allowed(request(token2))
    # lock now: every session is gone
    security.lock()
    assert not security.allowed(request(token)) and not security.allowed(request(token2))
    # a next path may only be a path of this app
    _token3, target = security.consume(await security.unlock(PW, "https://elsewhere.example/"))
    assert target == "/"
    _token4, target = security.consume(await security.unlock(PW, "//elsewhere.example/"))
    assert target == "/"


async def test_wrong_passwords_are_answered_more_slowly(sec, monkeypatch):
    security.set_password(PW, PW)
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(security.asyncio, "sleep", fake_sleep)
    for _ in range(6):
        with pytest.raises(ValidationError):
            await security.unlock("wrong")
    assert security.delay() == 8.0
    assert waited == [1.0, 2.0, 4.0]  # the first three were free
    sec.failures = 20
    assert security.delay() == security.MAX_DELAY
    await security.unlock(PW)
    assert security.delay() == 0.0


def test_stale_nonces_expire(sec, monkeypatch):
    nonce = security.set_password(PW, PW)
    monkeypatch.setattr(security.time, "monotonic", lambda: 10**9)
    assert security.consume(nonce) is None


def test_changing_and_removing_the_password(sec):
    security.set_password(PW, PW)
    with pytest.raises(ValidationError, match="Wrong password"):
        security.change_password("wrong", "new password!", "new password!")
    with pytest.raises(ValidationError, match="differ"):
        security.change_password(PW, "new password!", "other")
    security.change_password(PW, "new password!", "new password!")
    with pytest.raises(ValidationError, match="Wrong password"):
        security.remove_password(PW)
    security.remove_password("new password!")
    assert not security.required()


def test_encryption_rewrites_the_folder_and_its_backup_copies(ctx, setup, sec):
    data = ctx.store.data_dir
    expense(ctx, setup)
    copy = data / backup.BACKUP_DIR / "2026-10-01_090000_0.1.0_to_0.2.0"
    copy.mkdir(parents=True)
    (copy / "accounts.csv").write_text((data / "accounts.csv").read_text())
    (data / backup.BACKUP_DIR / "notes").mkdir()
    (data / backup.BACKUP_DIR / "notes" / "a.csv").write_text("x\n")  # not one of the app's copies
    security.set_password(PW, PW)
    with pytest.raises(ValidationError, match="Wrong password"):
        security.enable_encryption("wrong")
    with pytest.raises(ValidationError, match="not encrypted"):
        security.disable_encryption(PW)
    with pytest.raises(ValidationError, match="not encrypted"):
        security.new_recovery_key(PW)
    recovery = security.enable_encryption(PW)
    assert RECOVERY.match(recovery) and security.encrypted() and ctx.store.codec.encrypting
    with pytest.raises(ValidationError, match="already"):
        security.enable_encryption(PW)
    assert encryption.is_encrypted(data / "transactions.csv") and encryption.is_encrypted(copy / "accounts.csv")
    assert not encryption.is_encrypted(data / backup.BACKUP_DIR / "notes" / "a.csv")
    expense(ctx, setup, 5)  # written through the codec from now on
    assert encryption.is_encrypted(data / "transactions.csv")
    assert Store(data, Codec(ctx.store.codec.key)).transactions.all()[1].amount == 5
    with pytest.raises(ValidationError, match="Turn encryption off"):
        security.remove_password(PW)
    assert RECOVERY.match(security.new_recovery_key(PW))
    with pytest.raises(ValidationError, match="Wrong password"):
        security.new_recovery_key("wrong")
    with pytest.raises(ValidationError, match="Wrong password"):
        security.disable_encryption("wrong")
    security.disable_encryption(PW)
    assert not security.encrypted() and not ctx.store.codec.encrypting
    assert not encryption.is_encrypted(data / "transactions.csv") and not encryption.is_encrypted(copy / "accounts.csv")
    assert Store(data).transactions.all()[1].amount == 5


async def test_an_encrypted_folder_opens_after_the_unlock(ctx, setup, sec, monkeypatch):
    expense(ctx, setup)
    security.set_password(PW, PW)
    recovery = security.enable_encryption(PW)
    data = ctx.store.data_dir
    # the next start: the folder is encrypted, so the store waits for the password
    monkeypatch.setattr(context_module, "_ctx", None)
    st = security.configure(data)
    assert security.encrypted() and not security.can_open_without_password() and not st.codec.encrypting
    opened = []
    security.opener = opened.append
    with pytest.raises(ValidationError):
        await security.unlock("wrong")
    assert opened == []
    await security.unlock(PW)
    assert opened == [data] and st.codec.encrypting
    assert Store(data, st.codec).transactions.all()[0].amount == 12.5
    # the recovery key sets a new password and opens as well
    security.configure(data)
    st = security.state()
    security.opener = opened.append
    with pytest.raises(ValidationError, match="not the recovery key"):
        await security.recover("AAAA-" * 8, "brand new password")
    with pytest.raises(ValidationError, match="at least"):
        await security.recover(recovery, "short")
    nonce = await security.recover(recovery, "brand new password")
    assert security.consume(nonce) is not None and opened == [data, data]
    assert Vault.load(data).unlock("brand new password") == st.codec.key
    # once open, the opener is not called again
    context_module._ctx = ctx
    await security.unlock("brand new password")
    assert len(opened) == 2


async def test_the_file_route_is_behind_the_lock(ctx, setup, sec):
    tx = expense(ctx, setup)
    att = att_svc.add(ctx, tx.id, "receipt.pdf", PDF)
    api = FastAPI()
    att_ui.register(api)
    context_module._ctx = ctx
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://budget") as client:
            assert (await client.get(att_ui.url(att))).status_code == 200
            security.set_password(PW, PW)
            assert (await client.get(att_ui.url(att))).status_code == 404
            token, _ = security.consume(await security.unlock(PW))
            client.cookies.set(security.COOKIE, token)
            r = await client.get(att_ui.url(att))
            assert r.status_code == 200 and r.content == PDF
    finally:
        context_module._ctx = None


def test_state_needs_configuring():
    security._state = None
    with pytest.raises(RuntimeError, match="not configured"):
        security.state()
    assert not security.required() and not security.encrypted() and not security.phone_enabled()
