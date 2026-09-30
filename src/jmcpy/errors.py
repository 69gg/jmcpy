"""异常体系。

所有异常都继承 :class:`JmcpyError`，调用方可以只捕获这一个基类。
异常消息统一用中文，并在属性里保留结构化信息（状态码、接口路径、尝试记录等），
便于调用方自行判断与重试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ApiRejected",
    "AttemptFailure",
    "AuthRejected",
    "AuthRequired",
    "BadStatus",
    "ChallengeBlocked",
    "ConfigurationError",
    "CredentialError",
    "CryptoError",
    "InvalidArgument",
    "JmcpyError",
    "NetworkIssue",
    "NotFound",
    "ParseFailed",
    "RegionBlocked",
    "RequestFailed",
    "ResponseInvalid",
]


class JmcpyError(Exception):
    """本包所有异常的基类。"""


class ConfigurationError(JmcpyError):
    """配置项不合法或缺失。"""


class InvalidArgument(JmcpyError, ValueError):
    """调用方传入的参数不合法（例如车号格式不对）。

    同时继承 :class:`ValueError`，因此按惯例 ``except ValueError`` 也能捕获。
    """


@dataclass(frozen=True, slots=True)
class AttemptFailure:
    """一次失败尝试的记录。"""

    attempt: int
    endpoint: str | None
    url: str
    error: Exception

    def __str__(self) -> str:
        where = self.endpoint or "-"
        return f"第 {self.attempt} 次尝试 [端点 {where}] {self.url} 失败: {self.error}"


class RequestFailed(JmcpyError):
    """重试与换端点全部失败。"""

    def __init__(self, url: str, failures: Sequence[AttemptFailure]) -> None:
        self.url = url
        self.failures = tuple(failures)
        detail = "; ".join(str(item) for item in self.failures[-3:]) or "无尝试记录"
        super().__init__(f"请求最终失败，共尝试 {len(self.failures)} 次: {url} | {detail}")


class ResponseInvalid(JmcpyError):
    """响应不是预期的结构（非 JSON、字段缺失、截断等）。"""

    def __init__(self, message: str, *, url: str | None = None, snippet: str | None = None) -> None:
        self.url = url
        self.snippet = snippet
        parts = [message]
        if url:
            parts.append(f"url={url}")
        if snippet:
            parts.append(f"响应片段={snippet[:200]}")
        super().__init__(" | ".join(parts))


class NetworkIssue(JmcpyError):
    """链路层异常：连接被重置、超时、TLS 失败等。"""

    def __init__(self, message: str, *, url: str | None = None, cause: BaseException | None = None) -> None:
        self.url = url
        self.cause = cause
        suffix = f" | url={url}" if url else ""
        if cause is not None:
            suffix += f" | {type(cause).__name__}: {cause}"
        super().__init__(f"{message}{suffix}")


class BadStatus(JmcpyError):
    """服务端返回了不可重试的 HTTP 状态码。"""

    def __init__(self, status: int, *, url: str | None = None, snippet: str | None = None) -> None:
        self.status = status
        self.url = url
        self.snippet = snippet
        parts = [f"服务端返回 HTTP {status}"]
        if url:
            parts.append(f"url={url}")
        if snippet:
            parts.append(f"响应片段={snippet[:200]}")
        super().__init__(" | ".join(parts))


class ApiRejected(JmcpyError):
    """接口明确返回业务错误。"""

    def __init__(self, message: str, *, code: int | None = None, url: str | None = None) -> None:
        self.code = code
        self.url = url
        suffix = f"（code={code}）" if code is not None else ""
        super().__init__(f"{message}{suffix}" + (f" | url={url}" if url else ""))


class NotFound(ApiRejected):
    """本子或章节不存在。"""


class AuthRequired(JmcpyError):
    """该操作需要先登录。"""


class AuthRejected(JmcpyError):
    """登录失败，或会话已失效。"""


class ChallengeBlocked(JmcpyError):
    """被反爬验证页拦截（网页端常见）。

    重试无法解决，需要换网络环境，或提供浏览器中已通过验证的 Cookie。
    """


class RegionBlocked(JmcpyError):
    """当前出口 IP 的地区被拒绝访问。"""


class CryptoError(JmcpyError):
    """签名、加解密或密钥推导失败。"""


class ParseFailed(JmcpyError):
    """响应能拿到，但解析不出目标结构。"""

    def __init__(self, message: str, *, url: str | None = None, snippet: str | None = None) -> None:
        self.url = url
        self.snippet = snippet
        parts = [message]
        if url:
            parts.append(f"url={url}")
        if snippet:
            parts.append(f"响应片段={snippet[:200]}")
        super().__init__(" | ".join(parts))


class CredentialError(JmcpyError):
    """会话凭据读写失败。"""
