"""HTTP 请求与响应的统一表示，隔离各传输后端（curl-cffi / httpx）的差异。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast, get_args

from ..errors import ConfigurationError, ResponseInvalid

__all__ = ["HttpMethod", "HttpRequest", "Reply"]

HttpMethod = Literal["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "TRACE", "PATCH", "QUERY"]
HTTP_METHODS: frozenset[str] = frozenset(get_args(HttpMethod))


def _require_latin1_headers(headers: Mapping[str, str] | None) -> None:
    """HTTP 头的名字与值只能是 latin-1 字节。

    curl-cffi 会直接抛 ``UnicodeEncodeError``，httpx 则会悄悄用 utf-8 编码，
    两者行为不一致且报错难以理解，因此在这里统一拦下并给出提示。
    """
    for name, value in (headers or {}).items():
        for part in (name, value):
            try:
                str(part).encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ConfigurationError(
                    f"请求头只能用 latin-1 能表示的字符，"
                    f"{name!r} 的值含非法字符（如需传中文请改用请求体或 Cookie 之外的机制）"
                ) from exc


def as_http_method(method: str) -> HttpMethod:
    """把字符串收敛成受支持的 HTTP 方法。"""
    normalized = method.strip().upper()
    if normalized not in HTTP_METHODS:
        raise ValueError(f"不支持的 HTTP 方法: {method}")
    return cast(HttpMethod, normalized)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """一次待发送的请求。"""

    method: HttpMethod
    url: str
    params: Mapping[str, Any] | None = None
    data: Mapping[str, Any] | None = None
    headers: Mapping[str, str] | None = None
    timeout: float | None = None
    allow_redirects: bool = True

    def __post_init__(self) -> None:
        # 方法在这里就收敛成受支持的写法，避免不同后端各写一套校验
        object.__setattr__(self, "method", as_http_method(self.method))
        _require_latin1_headers(self.headers)

    def with_url(self, url: str) -> HttpRequest:
        """换一个目标地址（用于端点切换）。"""
        return HttpRequest(
            method=self.method,
            url=url,
            params=self.params,
            data=self.data,
            headers=self.headers,
            timeout=self.timeout,
            allow_redirects=self.allow_redirects,
        )


@dataclass(frozen=True, slots=True)
class Reply:
    """一次已完成请求的结果。"""

    status: int
    url: str
    content: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    #: 重定向链上的中间地址
    history: tuple[str, ...] = ()
    elapsed: float | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def redirected(self) -> bool:
        """发生过重定向。"""
        return bool(self.history)

    @property
    def text(self) -> str:
        """按 UTF-8 解码（非法字节替换），页面与 JSON 都是 UTF-8。"""
        return self.content.decode("utf-8", errors="replace")

    def header(self, name: str, default: str | None = None) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return default

    def json(self) -> Any:
        """解析 JSON，失败时抛 :class:`~jmcpy.errors.ResponseInvalid`。"""
        try:
            return json.loads(self.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ResponseInvalid("响应不是合法 JSON", url=self.url, snippet=self.text) from exc

    def snippet(self, length: int = 200) -> str:
        """截取一段响应文本，用于异常信息。"""
        return self.text[:length]
