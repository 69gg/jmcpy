"""登录凭据的持久化与安全降级。"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from jmcpy.credentials import CredentialStore, KeyringMasterKey, LoginSession
from jmcpy.errors import CredentialError
from jmcpy.models import Account
from jmcpy.settings import Settings


class FakeKeyring:
    """最小可用的钥匙串替身。"""

    def __init__(self, *, working: bool = True) -> None:
        self.storage: dict[tuple[str, str], str] = {}
        self.working = working

    def get_password(self, service: str, name: str) -> str | None:
        if not self.working:
            raise RuntimeError("钥匙串后端不可用")
        return self.storage.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        if not self.working:
            raise RuntimeError("钥匙串后端不可用")
        self.storage[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        if not self.working:
            raise RuntimeError("钥匙串后端不可用")
        self.storage.pop((service, name), None)


class FakeKeys:
    """直接给出固定主密钥的替身（密钥来自真实 Fernet key 格式）。"""

    def __init__(self, key: bytes | None = None, *, broken: bool = False) -> None:
        from cryptography.fernet import Fernet

        self.key = key if key is not None else Fernet.generate_key()
        self.broken = broken

    def get(self) -> bytes | None:
        if self.broken:
            raise CredentialError("钥匙串读取失败")
        return self.key

    def get_or_create(self) -> bytes:
        if self.broken:
            raise CredentialError("钥匙串不可用")
        return self.key

    def delete(self) -> None:
        return None


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    overrides.setdefault("use_keyring", False)
    return Settings(home=tmp_path, **overrides)  # type: ignore[arg-type]


def make_session() -> LoginSession:
    return LoginSession(
        username="someone",
        uid="123",
        cookies={"AVS": "SUPER-SECRET-TOKEN", "JM_SESSION": "abc"},
        account={"uid": "123", "username": "someone", "level_name": "初來乍到"},
        saved_at=time.time(),
    )


# --------------------------------------------------------------------------- 模型
def test_login_session_round_trip() -> None:
    session = make_session()

    restored = LoginSession.from_json(session.to_json())

    assert restored == session
    assert restored.avs == "SUPER-SECRET-TOKEN"
    assert restored.is_usable() is True


def test_login_session_without_avs_is_not_usable() -> None:
    session = LoginSession(username="u", cookies={"JM_SESSION": "x"})

    assert session.avs is None
    assert session.is_usable() is False


def test_login_session_from_account_uses_non_raw_fields() -> None:
    account = Account(uid="1", username="u", level_name="L", raw={"secret": "payload"})

    session = LoginSession.from_account(account, {"AVS": "T"})

    assert session.uid == "1"
    assert session.username == "u"
    assert session.account["level_name"] == "L"
    assert "raw" not in session.account
    assert session.avs == "T"


def test_login_session_from_account_with_none() -> None:
    session = LoginSession.from_account(None, {"AVS": "T"})

    assert session.username == ""
    assert session.account == {}


def test_from_json_tolerates_missing_and_wrong_types() -> None:
    session = LoginSession.from_json({"cookies": None, "account": "nope", "saved_at": None})

    assert session.cookies == {}
    assert session.account == {}
    assert session.saved_at == 0.0


# --------------------------------------------------------------------------- 加密落盘
def test_save_encrypts_and_loads_back(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=FakeKeys())
    session = make_session()

    assert store.save(session) is True

    raw = store.path.read_text(encoding="utf-8")
    assert "SUPER-SECRET-TOKEN" not in raw, "加密落盘后不应出现明文凭据"
    assert json.loads(raw)["encrypted"] is True
    assert store.load() == session


def test_file_permissions_are_restricted(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=FakeKeys())

    store.save(make_session())

    if os.name == "posix":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_plaintext_fallback_when_no_key_source(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=None)
    session = make_session()

    assert store.save(session) is False

    raw = store.path.read_text(encoding="utf-8")
    assert json.loads(raw)["encrypted"] is False
    assert store.load() == session
    if os.name == "posix":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_use_keyring_false_builds_store_without_key_source(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path))

    assert store._keys is None
    assert store.save(make_session()) is False


def test_unusable_keyring_degrades_to_plaintext(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path, use_keyring=True), keys=FakeKeys(broken=True))
    session = make_session()

    assert store.save(session) is False
    assert store.load() == session


# --------------------------------------------------------------------------- 容错
def test_load_returns_none_when_file_absent(tmp_path: Path) -> None:
    assert CredentialStore(make_settings(tmp_path), keys=FakeKeys()).load() is None


def test_load_returns_none_on_broken_json(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=FakeKeys())
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ 不是 json", encoding="utf-8")

    assert store.load() is None


def test_load_returns_none_on_unknown_version(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=FakeKeys())
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"version": 99, "encrypted": False, "payload": {}}), encoding="utf-8")

    assert store.load() is None


def test_load_returns_none_when_key_changed(tmp_path: Path) -> None:
    CredentialStore(make_settings(tmp_path), keys=FakeKeys()).save(make_session())

    other = CredentialStore(make_settings(tmp_path), keys=FakeKeys())

    assert other.load() is None, "换了一把主密钥应当无法解密，而不是抛异常"


def test_load_returns_none_when_encrypted_but_no_key(tmp_path: Path) -> None:
    CredentialStore(make_settings(tmp_path), keys=FakeKeys()).save(make_session())

    without_keys = CredentialStore(make_settings(tmp_path), keys=None)

    assert without_keys.load() is None


def test_load_returns_none_when_payload_lacks_credentials(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=None)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"version": 1, "encrypted": False, "payload": {"username": "u", "cookies": {}}}),
        encoding="utf-8",
    )

    assert store.load() is None


def test_clear_removes_file_and_is_idempotent(tmp_path: Path) -> None:
    store = CredentialStore(make_settings(tmp_path), keys=FakeKeys())
    store.save(make_session())

    store.clear()
    store.clear()

    assert not store.path.exists()
    assert store.load() is None


def test_custom_session_path(tmp_path: Path) -> None:
    custom = tmp_path / "nested" / "session.json"
    store = CredentialStore(make_settings(tmp_path, session_path=custom), keys=None)

    store.save(make_session())

    assert custom.is_file()


# --------------------------------------------------------------------------- 钥匙串
def test_keyring_master_key_round_trip() -> None:
    keyring = FakeKeyring()
    source = KeyringMasterKey(module=keyring)

    assert source.get() is None

    created = source.get_or_create()

    assert created and source.get() == created
    assert source.get_or_create() == created, "已存在时不应重新生成"

    source.delete()
    assert source.get() is None


def test_keyring_master_key_reports_unavailable_backend() -> None:
    source = KeyringMasterKey(module=FakeKeyring(working=False))

    with pytest.raises(CredentialError, match="读取系统钥匙串失败"):
        source.get()
    with pytest.raises(CredentialError, match=r"写入系统钥匙串失败|读取系统钥匙串失败"):
        source.get_or_create()
    source.delete()  # 删除失败只记日志


def test_keyring_master_key_without_keyring_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "keyring":
            raise ImportError("no keyring")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(CredentialError, match="未安装 keyring"):
        KeyringMasterKey().get()


def test_encrypted_round_trip_through_keyring_backed_store(tmp_path: Path) -> None:
    keyring = FakeKeyring()
    settings = make_settings(tmp_path, use_keyring=True)

    session = make_session()
    first = CredentialStore(settings, keys=KeyringMasterKey(module=keyring))
    assert first.save(session) is True

    second = CredentialStore(settings, keys=KeyringMasterKey(module=keyring))
    assert second.load() == session
