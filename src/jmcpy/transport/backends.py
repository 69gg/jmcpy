"""传输后端：把 curl-cffi 与 httpx（可选）适配成同一组接口。

后端只负责「发一次请求」，不含重试与端点切换逻辑。
curl-cffi 会伪装浏览器 TLS/JA3 指纹，是默认选择；httpx 作为纯 Python 回退。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from ..enums import Backend
from ..errors import ConfigurationError, NetworkIssue
from .response import HttpRequest, Reply

__all__ = [
    "AsyncCurlCffiBackend",
    "AsyncHttpBackend",
    "AsyncHttpxBackend",
    "CurlCffiBackend",
    "HttpBackend",
    "HttpxBackend",
    "create_async_backend",
    "create_backend",
]


class HttpBackend(ABC):
    """同步传输后端。"""

    @abstractmethod
    def send(self, request: HttpRequest) -> Reply:
        """发送一次请求。链路异常统一抛 :class:`~jmcpy.errors.NetworkIssue`。"""

    @abstractmethod
    def close(self) -> None:
        """释放底层连接资源。"""

    @abstractmethod
    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        """写入 Cookie（登录后使用）。"""

    @abstractmethod
    def get_cookies(self) -> dict[str, str]:
        """读取当前 Cookie。"""

    def __enter__(self) -> HttpBackend:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AsyncHttpBackend(ABC):
    """异步传输后端。Cookie 操作是同步的，其余与 :class:`HttpBackend` 对齐。"""

    @abstractmethod
    async def send(self, request: HttpRequest) -> Reply:
        """发送一次请求。"""

    @abstractmethod
    async def close(self) -> None:
        """释放底层连接资源。"""

    @abstractmethod
    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        """写入 Cookie。"""

    @abstractmethod
    def get_cookies(self) -> dict[str, str]:
        """读取当前 Cookie。"""

    async def __aenter__(self) -> AsyncHttpBackend:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


def _cookie_dict(cookies: Any) -> dict[str, str]:
    if cookies is None:
        return {}
    try:
        return {str(key): str(value) for key, value in dict(cookies).items()}
    except (TypeError, ValueError):
        return {}


def _reply_from_curl(raw: Any) -> Reply:
    return Reply(
        status=int(raw.status_code),
        url=str(raw.url),
        content=raw.content,
        headers={str(key): str(value) for key, value in raw.headers.items()},
        cookies=_cookie_dict(raw.cookies),
        history=tuple(str(item.url) for item in (raw.history or ())),
        elapsed=raw.elapsed.total_seconds() if raw.elapsed else None,
    )


def _reply_from_httpx(raw: Any) -> Reply:
    return Reply(
        status=int(raw.status_code),
        url=str(raw.url),
        content=raw.content,
        headers={str(key): str(value) for key, value in raw.headers.items()},
        cookies=_cookie_dict(raw.cookies),
        history=tuple(str(item.url) for item in raw.history),
        elapsed=raw.elapsed.total_seconds() if raw.elapsed else None,
    )


def _curl_kwargs(request: HttpRequest) -> dict[str, Any]:
    return {
        "params": dict(request.params) if request.params else None,
        "data": dict(request.data) if request.data else None,
        "headers": dict(request.headers) if request.headers else None,
        "timeout": request.timeout,
        "allow_redirects": request.allow_redirects,
    }


class CurlCffiBackend(HttpBackend):
    """基于 curl-cffi 的实现，默认伪装 Chrome 指纹。"""

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        proxy: str | None = None,
        verify: bool = True,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        from curl_cffi import requests as curl_requests

        self._session: Any = curl_requests.Session(
            impersonate=impersonate,
            proxy=proxy,
            verify=verify,
            headers=dict(default_headers or {}),
        )

    def send(self, request: HttpRequest) -> Reply:
        from curl_cffi.requests.exceptions import RequestException

        try:
            raw = self._session.request(request.method, request.url, **_curl_kwargs(request))
        except UnicodeEncodeError as exc:
            raise ConfigurationError("请求头或 Cookie 的值必须是 latin-1 可编码的字节，当前取值含非法字符") from exc
        except RequestException as exc:
            raise NetworkIssue("请求发送失败", url=request.url, cause=exc) from exc
        except OSError as exc:  # 连接被重置、DNS 失败等
            raise NetworkIssue("网络不可达", url=request.url, cause=exc) from exc
        return _reply_from_curl(raw)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        for name, value in cookies.items():
            self._session.cookies.set(name, value)

    def get_cookies(self) -> dict[str, str]:
        return _cookie_dict(self._session.cookies)

    def close(self) -> None:
        self._session.close()


class AsyncCurlCffiBackend(AsyncHttpBackend):
    """curl-cffi 的异步实现。"""

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        proxy: str | None = None,
        verify: bool = True,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        from curl_cffi import requests as curl_requests

        self._session: Any = curl_requests.AsyncSession(
            impersonate=impersonate,
            proxy=proxy,
            verify=verify,
            headers=dict(default_headers or {}),
        )

    async def send(self, request: HttpRequest) -> Reply:
        from curl_cffi.requests.exceptions import RequestException

        try:
            raw = await self._session.request(request.method, request.url, **_curl_kwargs(request))
        except UnicodeEncodeError as exc:
            raise ConfigurationError("请求头或 Cookie 的值必须是 latin-1 可编码的字节，当前取值含非法字符") from exc
        except RequestException as exc:
            raise NetworkIssue("请求发送失败", url=request.url, cause=exc) from exc
        except OSError as exc:
            raise NetworkIssue("网络不可达", url=request.url, cause=exc) from exc
        return _reply_from_curl(raw)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        for name, value in cookies.items():
            self._session.cookies.set(name, value)

    def get_cookies(self) -> dict[str, str]:
        return _cookie_dict(self._session.cookies)

    async def close(self) -> None:
        await self._session.close()


def _require_httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - 取决于是否安装 extra
        raise ConfigurationError(
            "backend='httpx' 需要额外安装 httpx，请执行 pip install 'jmcpy[httpx]' 或改用 backend='curl_cffi'"
        ) from exc
    return httpx


class HttpxBackend(HttpBackend):
    """基于 httpx 的回退实现（不伪装 TLS 指纹）。"""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        verify: bool = True,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        httpx = _require_httpx()
        self._client: Any = httpx.Client(
            proxy=proxy,
            verify=verify,
            headers=dict(default_headers or {}),
            follow_redirects=True,
        )

    def send(self, request: HttpRequest) -> Reply:
        httpx = _require_httpx()
        try:
            raw = self._client.request(
                request.method,
                request.url,
                params=dict(request.params) if request.params else None,
                data=dict(request.data) if request.data else None,
                headers=dict(request.headers) if request.headers else None,
                timeout=request.timeout,
                follow_redirects=request.allow_redirects,
            )
        except httpx.HTTPError as exc:
            raise NetworkIssue("请求发送失败", url=request.url, cause=exc) from exc
        return _reply_from_httpx(raw)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        for name, value in cookies.items():
            self._client.cookies.set(name, value)

    def get_cookies(self) -> dict[str, str]:
        return _cookie_dict(self._client.cookies)

    def close(self) -> None:
        self._client.close()


class AsyncHttpxBackend(AsyncHttpBackend):
    """httpx 的异步实现。"""

    def __init__(
        self,
        *,
        proxy: str | None = None,
        verify: bool = True,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        httpx = _require_httpx()
        self._client: Any = httpx.AsyncClient(
            proxy=proxy,
            verify=verify,
            headers=dict(default_headers or {}),
            follow_redirects=True,
        )

    async def send(self, request: HttpRequest) -> Reply:
        httpx = _require_httpx()
        try:
            raw = await self._client.request(
                request.method,
                request.url,
                params=dict(request.params) if request.params else None,
                data=dict(request.data) if request.data else None,
                headers=dict(request.headers) if request.headers else None,
                timeout=request.timeout,
                follow_redirects=request.allow_redirects,
            )
        except httpx.HTTPError as exc:
            raise NetworkIssue("请求发送失败", url=request.url, cause=exc) from exc
        return _reply_from_httpx(raw)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        for name, value in cookies.items():
            self._client.cookies.set(name, value)

    def get_cookies(self) -> dict[str, str]:
        return _cookie_dict(self._client.cookies)

    async def close(self) -> None:
        await self._client.aclose()


def create_backend(
    backend: Backend,
    *,
    impersonate: str = "chrome",
    proxy: str | None = None,
    verify: bool = True,
    default_headers: Mapping[str, str] | None = None,
) -> HttpBackend:
    """按配置创建同步后端。"""
    if backend is Backend.HTTPX:
        return HttpxBackend(proxy=proxy, verify=verify, default_headers=default_headers)
    return CurlCffiBackend(
        impersonate=impersonate,
        proxy=proxy,
        verify=verify,
        default_headers=default_headers,
    )


def create_async_backend(
    backend: Backend,
    *,
    impersonate: str = "chrome",
    proxy: str | None = None,
    verify: bool = True,
    default_headers: Mapping[str, str] | None = None,
) -> AsyncHttpBackend:
    """按配置创建异步后端。"""
    if backend is Backend.HTTPX:
        return AsyncHttpxBackend(proxy=proxy, verify=verify, default_headers=default_headers)
    return AsyncCurlCffiBackend(
        impersonate=impersonate,
        proxy=proxy,
        verify=verify,
        default_headers=default_headers,
    )
