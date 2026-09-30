"""配置。

:class:`Settings` 是唯一配置入口：超时、重试、端点池、并发、代理、会话路径都在这里。
所有字段都可以用 ``JMCPY_`` 前缀的环境变量覆盖，也可以写在 TOML 配置文件里
（默认位置为平台配置目录下的 ``config.toml``，可显式传路径）。

优先级：显式参数 > 环境变量 > 配置文件 > 出厂默认。
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir, user_config_dir

from .constants import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_BACKOFF_JITTER,
    DEFAULT_BACKOFF_MAX,
    DEFAULT_CDN_ENDPOINTS,
    DEFAULT_CONCURRENCY,
    DEFAULT_ENDPOINT_TTL,
    DEFAULT_IMAGE_TIMEOUT,
    DEFAULT_MOBILE_ENDPOINTS,
    DEFAULT_MOBILE_VERSION,
    DEFAULT_RETRY_STATUS,
    DEFAULT_RETRY_TIMES,
    DEFAULT_TIMEOUT,
    DEFAULT_WEB_ENDPOINTS,
)
from .enums import Backend, RetryMode
from .errors import ConfigurationError

__all__ = ["APP_DIR_NAME", "Settings", "cache_dir", "config_dir"]

APP_DIR_NAME = "jmcpy"

#: 需要转成 tuple[str, ...] 的字段
_TUPLE_FIELDS = frozenset({"mobile_endpoints", "cdn_endpoints", "web_endpoints"})
#: 需要转成 frozenset[int] 的字段
_INT_SET_FIELDS = frozenset({"retry_status"})
#: 需要转成 Path 的字段
_PATH_FIELDS = frozenset({"home", "session_path"})

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})


def config_dir(home: Path | None = None) -> Path:
    """配置目录：显式 ``home`` 优先，否则用平台标准位置。"""
    return home if home is not None else Path(user_config_dir(APP_DIR_NAME))


def cache_dir(home: Path | None = None) -> Path:
    """缓存目录（端点池等可重建的数据）。"""
    return (home / "cache") if home is not None else Path(user_cache_dir(APP_DIR_NAME))


@dataclass(frozen=True, slots=True)
class Settings:
    """一次会话的全部可调参数。"""

    # ---------------------------------------------------------------- 传输
    #: HTTP 传输层实现
    backend: Backend = Backend.CURL_CFFI
    #: curl-cffi 的浏览器指纹目标（``chrome`` / ``chrome131`` / ``safari`` …）
    impersonate: str = "chrome"
    #: 代理地址，例如 ``http://127.0.0.1:7890``；None 表示交给底层按环境变量处理
    proxy: str | None = None
    #: 是否校验 TLS 证书
    verify: bool = True
    #: 接口请求超时（秒）
    timeout: float = DEFAULT_TIMEOUT
    #: 图片下载超时（秒）
    image_timeout: float = DEFAULT_IMAGE_TIMEOUT

    # ---------------------------------------------------------------- 重试与多源
    #: 重试与换端点的优先级
    retry_mode: RetryMode = RetryMode.RETRY_FIRST
    #: 同一端点内的重试次数（``ROTATE_FIRST`` 下表示轮数）
    retry_times: int = DEFAULT_RETRY_TIMES
    backoff_base: float = DEFAULT_BACKOFF_BASE
    backoff_max: float = DEFAULT_BACKOFF_MAX
    #: 退避抖动比例，0 表示不抖动
    backoff_jitter: float = DEFAULT_BACKOFF_JITTER
    #: 视为「临时异常、值得重试」的 HTTP 状态码
    retry_status: frozenset[int] = DEFAULT_RETRY_STATUS

    # ---------------------------------------------------------------- 端点池
    mobile_endpoints: tuple[str, ...] = DEFAULT_MOBILE_ENDPOINTS
    cdn_endpoints: tuple[str, ...] = DEFAULT_CDN_ENDPOINTS
    web_endpoints: tuple[str, ...] = DEFAULT_WEB_ENDPOINTS
    #: 是否向端点源/发布页拉取最新端点
    auto_update_endpoints: bool = True
    #: 端点池缓存有效期（秒）
    endpoint_ttl: float = DEFAULT_ENDPOINT_TTL

    # ---------------------------------------------------------------- 接口
    mobile_version: str = DEFAULT_MOBILE_VERSION
    #: 是否用 ``/setting`` 返回的版本号自动更新 ``mobile_version``
    auto_update_mobile_version: bool = True
    #: 覆盖默认 User-Agent；None 表示按用途取内置默认
    user_agent: str | None = None
    #: 追加到每个请求的请求头
    headers: Mapping[str, str] = field(default_factory=dict)
    #: 会话初始 Cookie
    cookies: Mapping[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- 下载
    #: 图片并发数
    concurrency: int = DEFAULT_CONCURRENCY

    # ---------------------------------------------------------------- 会话与路由
    #: 覆盖配置/缓存根目录（默认取平台配置目录）
    home: Path | None = None
    #: 覆盖会话文件路径
    session_path: Path | None = None
    #: 是否把会话主密钥交给操作系统钥匙串
    use_keyring: bool = True
    #: 创建客户端时是否自动从会话文件恢复登录态
    restore_session: bool = True
    #: 门面客户端是否允许把移动端不支持的能力自动路由到网页端
    auto_route: bool = True

    def __post_init__(self) -> None:
        if self.timeout <= 0 or self.image_timeout <= 0:
            raise ConfigurationError("timeout 与 image_timeout 必须为正数")
        if self.retry_times < 0:
            raise ConfigurationError("retry_times 不能为负数")
        if self.concurrency < 1:
            raise ConfigurationError("concurrency 至少为 1")
        if not 0 <= self.backoff_jitter < 1:
            raise ConfigurationError("backoff_jitter 需落在 [0, 1) 区间")
        if not self.mobile_endpoints:
            raise ConfigurationError("mobile_endpoints 不能为空")
        if not self.cdn_endpoints:
            raise ConfigurationError("cdn_endpoints 不能为空")

    # ------------------------------------------------------------------ 派生属性
    @property
    def resolved_home(self) -> Path:
        """配置根目录（显式 home 优先，否则平台标准位置）。"""
        return config_dir(self.home)

    @property
    def resolved_session_path(self) -> Path:
        """会话文件路径。"""
        return self.session_path if self.session_path is not None else self.resolved_home / "session.json"

    @property
    def resolved_config_path(self) -> Path:
        """配置文件路径。"""
        return self.resolved_home / "config.toml"

    def resolved_cache_dir(self) -> Path:
        """缓存目录。"""
        return cache_dir(self.home)

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> Settings:
        """按字段名覆盖默认值，未知字段报错（避免拼错配置静默失效）。"""
        return apply_overrides(cls(), values)

    @classmethod
    def from_file(cls, path: Path) -> Settings:
        """从 TOML 文件读取配置；顶层表或 ``[jmcpy]`` 表都可以。"""
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigurationError(f"配置文件不存在: {config_path}")
        return apply_overrides(cls(), _file_overrides(config_path))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """从 ``JMCPY_`` 前缀的环境变量读取配置。"""
        source = os.environ if env is None else env
        return apply_overrides(cls(), _env_overrides(source))

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> Settings:
        """合并 出厂默认 → 配置文件 → 环境变量 → 显式覆盖。"""
        source = os.environ if env is None else env
        env_values = _env_overrides(source)

        candidate = Path(path) if path is not None else config_dir(env_values.get("home")) / "config.toml"
        result = cls()
        if candidate.is_file():
            result = apply_overrides(result, _file_overrides(candidate))
        result = apply_overrides(result, env_values)
        if overrides:
            result = apply_overrides(result, overrides)
        return result


def _file_overrides(path: Path) -> dict[str, Any]:
    """读取 TOML 配置文件里的字段覆盖值。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"配置文件读取失败: {path} | {exc}") from exc
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"配置文件解析失败: {path} | {exc}") from exc

    table = document.get(APP_DIR_NAME, document)
    if not isinstance(table, dict):
        raise ConfigurationError(f"配置文件格式错误，期望表结构: {path}")
    return {key: _coerce_file_value(key, value) for key, value in table.items()}


def apply_overrides(base: Settings, values: Mapping[str, Any]) -> Settings:
    """在 ``base`` 之上覆盖若干字段。"""
    known = {item.name for item in fields(Settings)}
    unknown = set(values) - known
    if unknown:
        raise ConfigurationError(f"未知配置项: {', '.join(sorted(unknown))}")
    return replace(base, **dict(values))


def _coerce_file_value(key: str, value: Any) -> Any:
    """把 TOML 里的值转成字段需要的类型。"""
    if key in _TUPLE_FIELDS:
        if not isinstance(value, (list, tuple)):
            raise ConfigurationError(f"配置项 {key} 需要字符串数组")
        return tuple(str(item) for item in value)
    if key in _INT_SET_FIELDS:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ConfigurationError(f"配置项 {key} 需要整数数组")
        return frozenset(int(item) for item in value)
    if key in _PATH_FIELDS:
        return Path(str(value)) if value is not None else None
    if key == "backend":
        return Backend(str(value))
    if key == "retry_mode":
        return RetryMode(str(value))
    return value


def _to_bool(text: str) -> bool:
    return text.strip().lower() in _TRUE_WORDS


def _to_sequence(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in text.replace(";", ",").split(",") if item.strip())


def _to_cookies(text: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in text.replace(";", ",").split(","):
        name, separator, value = chunk.partition("=")
        if separator and name.strip():
            cookies[name.strip()] = value.strip()
    return cookies


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """把环境变量翻译成字段覆盖；只处理显式列出的变量名。"""
    overrides: dict[str, Any] = {}
    getters: dict[str, tuple[str, Any]] = {
        "JMCPY_BACKEND": ("backend", Backend),
        "JMCPY_IMPERSONATE": ("impersonate", str),
        "JMCPY_PROXY": ("proxy", str),
        "JMCPY_VERIFY": ("verify", _to_bool),
        "JMCPY_TIMEOUT": ("timeout", float),
        "JMCPY_IMAGE_TIMEOUT": ("image_timeout", float),
        "JMCPY_RETRY_MODE": ("retry_mode", RetryMode),
        "JMCPY_RETRY_TIMES": ("retry_times", int),
        "JMCPY_BACKOFF_BASE": ("backoff_base", float),
        "JMCPY_BACKOFF_MAX": ("backoff_max", float),
        "JMCPY_BACKOFF_JITTER": ("backoff_jitter", float),
        "JMCPY_MOBILE_ENDPOINTS": ("mobile_endpoints", _to_sequence),
        "JMCPY_CDN_ENDPOINTS": ("cdn_endpoints", _to_sequence),
        "JMCPY_WEB_ENDPOINTS": ("web_endpoints", _to_sequence),
        "JMCPY_AUTO_UPDATE_ENDPOINTS": ("auto_update_endpoints", _to_bool),
        "JMCPY_ENDPOINT_TTL": ("endpoint_ttl", float),
        "JMCPY_MOBILE_VERSION": ("mobile_version", str),
        "JMCPY_AUTO_UPDATE_MOBILE_VERSION": ("auto_update_mobile_version", _to_bool),
        "JMCPY_USER_AGENT": ("user_agent", str),
        "JMCPY_COOKIES": ("cookies", _to_cookies),
        "JMCPY_CONCURRENCY": ("concurrency", int),
        "JMCPY_HOME": ("home", Path),
        "JMCPY_SESSION_PATH": ("session_path", Path),
        "JMCPY_USE_KEYRING": ("use_keyring", _to_bool),
        "JMCPY_RESTORE_SESSION": ("restore_session", _to_bool),
        "JMCPY_AUTO_ROUTE": ("auto_route", _to_bool),
    }
    for name, (target, caster) in getters.items():
        raw = env.get(name)
        if raw is None or raw == "":
            continue
        try:
            overrides[target] = caster(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(f"环境变量 {name} 取值非法: {raw!r} ({exc})") from exc
    return overrides
