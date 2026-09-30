"""登录凭据的持久化。

设计目标：**默认安全、失败可用**。

* 会话内容（Cookie、AVS、资料快照）写到用户配置目录下的 ``session.json``；
* 文件用一个随机主密钥加密（Fernet：AES-128-CBC + HMAC-SHA256，带认证），
  主密钥存放在操作系统钥匙串里——Windows 凭据管理器、macOS 钥匙串、
  Linux Secret Service；
* 钥匙串不可用时（例如无桌面会话的服务器、没装 ``keyring``）自动降级为
  ``0600`` 权限的明文文件，并记一条 warning，功能不受影响；
* 文件损坏、版本不符、主密钥丢失都只当作「没有登录」，不会让调用方拿到异常。

跨平台说明：POSIX 下会显式把文件设为 ``0600``、目录设为 ``0700``；
Windows 没有等价的 POSIX 权限位，依赖用户配置目录本身的访问控制（``%LOCALAPPDATA%``
仅当前用户可读）。
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from .errors import CredentialError
from .settings import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "KEYRING_KEY_NAME",
    "KEYRING_SERVICE",
    "CredentialStore",
    "KeyringMasterKey",
    "LoginSession",
    "MasterKeySource",
]

KEYRING_SERVICE = "jmcpy"
KEYRING_KEY_NAME = "session-key"

#: 会话文件格式版本
_FORMAT_VERSION = 1

_FILE_MODE = 0o600
_DIR_MODE = 0o700


class MasterKeySource(Protocol):
    """主密钥来源（真实实现走操作系统钥匙串，测试可注入假实现）。"""

    def get(self) -> bytes | None:
        """读取已有主密钥；不存在返回 ``None``，不可用抛 :class:`~jmcpy.errors.CredentialError`。"""

    def get_or_create(self) -> bytes:
        """读取或生成主密钥；不可用抛 :class:`~jmcpy.errors.CredentialError`。"""

    def delete(self) -> None:
        """删除主密钥；不存在时静默返回。"""


@dataclass(frozen=True, slots=True)
class LoginSession:
    """一次登录的本地快照。"""

    username: str = ""
    uid: str = ""
    cookies: Mapping[str, str] = field(default_factory=dict)
    account: Mapping[str, Any] = field(default_factory=dict)
    saved_at: float = 0.0

    @property
    def avs(self) -> str | None:
        """会话令牌；没有它就不算已登录。"""
        value = self.cookies.get("AVS")
        return value or None

    def is_usable(self) -> bool:
        """是否含有可用于鉴权的信息。"""
        return bool(self.avs)

    def to_json(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "uid": self.uid,
            "cookies": dict(self.cookies),
            "account": dict(self.account),
            "saved_at": self.saved_at,
        }

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> LoginSession:
        cookies = document.get("cookies")
        account = document.get("account")
        return cls(
            username=str(document.get("username") or ""),
            uid=str(document.get("uid") or ""),
            cookies={str(key): str(value) for key, value in (cookies or {}).items()},
            account=dict(account) if isinstance(account, Mapping) else {},
            saved_at=float(document.get("saved_at") or 0.0),
        )

    @classmethod
    def from_account(cls, account: Any, cookies: Mapping[str, str]) -> LoginSession:
        """从 :class:`~jmcpy.models.Account` 与当前 Cookie 生成快照。"""
        snapshot: dict[str, Any] = {}
        if account is not None:
            snapshot = {key: value for key, value in asdict(account).items() if key != "raw"}
        return cls(
            username=str(snapshot.get("username") or ""),
            uid=str(snapshot.get("uid") or ""),
            cookies=dict(cookies),
            account=snapshot,
            saved_at=time.time(),
        )


class KeyringMasterKey:
    """把主密钥交给操作系统钥匙串保管。"""

    def __init__(
        self,
        service: str = KEYRING_SERVICE,
        name: str = KEYRING_KEY_NAME,
        *,
        module: Any = None,
    ) -> None:
        self._service = service
        self._name = name
        self._module = module

    @property
    def _keyring(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            import keyring
        except ImportError as exc:
            raise CredentialError(
                "未安装 keyring，无法把会话主密钥交给系统钥匙串；"
                "可安装 'jmcpy[keyring]'，或设置 use_keyring=False 使用 0600 权限的明文会话文件"
            ) from exc
        return keyring

    def get(self) -> bytes | None:
        """读取主密钥；不存在返回 ``None``；钥匙串不可用抛异常。"""
        try:
            stored = self._keyring.get_password(self._service, self._name)
        except CredentialError:
            raise
        except Exception as exc:  # 钥匙串后端缺失/被锁/无权限
            raise CredentialError(f"读取系统钥匙串失败: {type(exc).__name__}: {exc}") from exc
        return None if not stored else stored.encode("ascii")

    def get_or_create(self) -> bytes:
        """读取主密钥，不存在则生成并写入。"""
        existing = self.get()
        if existing:
            return existing

        key = Fernet.generate_key()
        try:
            self._keyring.set_password(self._service, self._name, key.decode("ascii"))
        except Exception as exc:
            raise CredentialError(f"写入系统钥匙串失败: {type(exc).__name__}: {exc}") from exc
        return key

    def delete(self) -> None:
        """删除主密钥（已不存在时静默返回）。"""
        try:
            self._keyring.delete_password(self._service, self._name)
        except CredentialError:
            raise
        except Exception as exc:  # 多数后端在条目不存在时会抛，视为已删除
            logger.debug("删除钥匙串条目失败（可能是本就不存在）: %s", exc)


class CredentialStore:
    """会话文件的读写。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        keys: MasterKeySource | None = None,
    ) -> None:
        self._settings = settings if settings is not None else Settings()
        if keys is not None:
            self._keys: MasterKeySource | None = keys
        elif self._settings.use_keyring:
            self._keys = KeyringMasterKey()
        else:
            self._keys = None
        self._plaintext_warned = False

    @property
    def path(self) -> Path:
        """会话文件路径。"""
        return self._settings.resolved_session_path

    def load(self) -> LoginSession | None:
        """读取会话；不存在、损坏或无法解密都返回 ``None``。"""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("会话文件读取失败: %s (%s)", self.path, exc)
            return None

        try:
            document = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("会话文件内容无法解析，将忽略: %s (%s)", self.path, exc)
            return None
        if not isinstance(document, dict) or document.get("version") != _FORMAT_VERSION:
            logger.warning("会话文件版本不受支持，将忽略: %s", self.path)
            return None

        payload = document.get("payload")
        if document.get("encrypted"):
            token = payload if isinstance(payload, str) else ""
            decrypted = self._decrypt(token)
            if decrypted is None:
                return None
            payload = decrypted
        if not isinstance(payload, Mapping):
            logger.warning("会话文件缺少有效内容，将忽略: %s", self.path)
            return None

        session = LoginSession.from_json(payload)
        if not session.is_usable():
            logger.info("会话文件里没有可用的登录凭据: %s", self.path)
            return None
        return session

    def save(self, session: LoginSession) -> bool:
        """写入会话；返回是否加密落盘。"""
        token = self._encrypt(session)
        if token is not None:
            document: dict[str, Any] = {
                "version": _FORMAT_VERSION,
                "encrypted": True,
                "payload": token,
            }
        else:
            if not self._plaintext_warned:
                logger.warning(
                    "系统钥匙串不可用，会话将以 0600 权限的明文文件保存: %s；"
                    "如需避免落盘明文，请设置 use_keyring=False 之外的可用钥匙串，或不保存会话",
                    self.path,
                )
                self._plaintext_warned = True
            document = {
                "version": _FORMAT_VERSION,
                "encrypted": False,
                "payload": session.to_json(),
            }

        self._write(document)
        return token is not None

    def clear(self) -> None:
        """删除会话文件（不存在时静默返回）。"""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CredentialError(f"删除会话文件失败: {self.path} ({exc})") from exc

    # ------------------------------------------------------------------ 内部
    def _encrypt(self, session: LoginSession) -> str | None:
        if self._keys is None:
            return None
        try:
            key = self._keys.get_or_create()
        except CredentialError as exc:
            logger.warning("无法从系统钥匙串获取主密钥，会话将明文保存: %s", exc)
            return None
        plaintext = json.dumps(session.to_json(), ensure_ascii=False).encode("utf-8")
        return Fernet(key).encrypt(plaintext).decode("ascii")

    def _decrypt(self, token: str) -> Mapping[str, Any] | None:
        if self._keys is None or not token:
            logger.warning("会话文件是加密的，但当前没有可用的主密钥来源: %s", self.path)
            return None
        try:
            key = self._keys.get()
        except CredentialError as exc:
            logger.warning("无法读取系统钥匙串里的主密钥: %s", exc)
            return None
        if key is None:
            logger.warning("系统钥匙串里没有会话主密钥，无法解密会话文件: %s", self.path)
            return None
        try:
            plaintext = Fernet(key).decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError) as exc:
            logger.warning("会话文件解密失败（主密钥已更换或文件被改动）: %s (%s)", self.path, exc)
            return None
        try:
            document = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("会话文件解密后内容异常: %s (%s)", self.path, exc)
            return None
        return document if isinstance(document, Mapping) else None

    def _write(self, document: Mapping[str, Any]) -> None:
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _restrict(path.parent, _DIR_MODE)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            _restrict(temporary, _FILE_MODE)
            temporary.replace(path)
            _restrict(path, _FILE_MODE)
        except OSError as exc:
            raise CredentialError(f"会话文件写入失败: {path} ({exc})") from exc


def _restrict(path: Path, mode: int) -> None:
    """在 POSIX 上收紧权限；Windows 没有等价权限位，交给用户目录的访问控制。"""
    if os.name != "posix":
        return
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        if current != mode:
            path.chmod(mode)
    except OSError as exc:  # pragma: no cover - 取决于文件系统
        logger.debug("设置权限失败: %s (%s)", path, exc)
