"""会话：把「传输后端 + 重试/换端点决策 + 响应分诊」拼成一次可用的请求。

对外只有 :meth:`HttpSession.send`（异步版为 :meth:`AsyncHttpSession.send`）。
成功返回 :class:`~jmcpy.transport.response.Reply`，失败统一抛
:class:`~jmcpy.errors.RequestFailed`（不可重试的错误会直接抛出原始异常）。

响应分诊在这里完成：

* 反爬验证页 → :class:`~jmcpy.errors.ChallengeBlocked`（重试无用，直接失败）
* 地区封锁页 → :class:`~jmcpy.errors.RegionBlocked`（重试无用，直接失败）
* 配置里列为「临时异常」的状态码 → :class:`~jmcpy.errors.ResponseInvalid`（可重试）
* 其余 4xx/5xx → :class:`~jmcpy.errors.BadStatus`（直接失败）
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ..constants import DEFAULT_MOBILE_ENDPOINTS
from ..errors import (
    AttemptFailure,
    BadStatus,
    ChallengeBlocked,
    CryptoError,
    JmcpyError,
    NetworkIssue,
    RegionBlocked,
    RequestFailed,
    ResponseInvalid,
)
from ..settings import Settings
from .backends import AsyncHttpBackend, HttpBackend, create_async_backend, create_backend
from .response import HttpRequest, Reply, as_http_method
from .retry import Attempt, RetryPlanner

__all__ = ["AsyncHttpSession", "HttpSession", "Validator"]

#: 校验响应内容是否可用；不可用时抛异常，异常类型决定是否重试
Validator = Callable[[Reply], None]

#: 出现这些片段说明撞上了反爬验证页：这条线路在这段时间内不可用，换线路才有意义。
#: 只用「验证页独有」的片段——像 ``challenge-platform``、``cdn-cgi/challenge``
#: 这类 Cloudflare 通用脚本在正常页面里也会出现，拿它们当判据会把好页面误判成拦截。
_CHALLENGE_MARKERS = (
    "just a moment",
    "enable javascript and cookies",
    "__cf_chl",
    "cf_chl_opt",
)

#: 出现这些片段说明当前出口 IP 被地区封锁
_REGION_MARKERS = ("restricted access",)

#: 判定为「可重试」的异常类型
_RETRYABLE_ERRORS = (NetworkIssue, ResponseInvalid, CryptoError)

#: 判定为「这个端点不可用，换下一个」的异常类型。
#: 它们不是请求级失败：同一条线路被反爬验证页拦截或地区封锁时，别的线路可能完全正常，
#: 所以既不重试当前端点，也不立刻放弃整个请求。
_ENDPOINT_FATAL_ERRORS = (ChallengeBlocked, RegionBlocked)


class _SessionCore:
    """同步/异步会话共用的构造与分诊逻辑。"""

    def __init__(
        self,
        settings: Settings,
        *,
        default_endpoints: Sequence[str] | None = None,
    ) -> None:
        self._settings = settings
        # 显式传入空元组表示「这个客户端暂时没有可用端点」，不能回退成默认值
        self._default_endpoints = DEFAULT_MOBILE_ENDPOINTS if default_endpoints is None else tuple(default_endpoints)
        # 线路记忆：上一次成功的线路优先，最近失败的线路排到最后。没有它的话，
        # 每个请求都要重新踩一遍失效线路，代价是数秒的退避等待。
        self._preferred: str | None = None
        self._unhealthy: set[str] = set()

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._default_endpoints

    def set_endpoints(self, endpoints: Sequence[str]) -> None:
        """替换默认端点池（端点自动更新后调用）。"""
        if endpoints:
            self._default_endpoints = tuple(endpoints)

    # ------------------------------------------------------------------ 分诊
    def _evaluate(self, reply: Reply) -> None:
        """按 HTTP 状态与响应内容判断本次尝试是否可用。"""
        lowered = reply.text.lower()
        for marker in _CHALLENGE_MARKERS:
            if marker in lowered:
                raise ChallengeBlocked(
                    "被反爬验证页拦截，需要换网络环境，或提供浏览器中已通过验证的 Cookie"
                    f" | url={reply.url} | 片段={reply.snippet(120)}"
                )
        for marker in _REGION_MARKERS:
            if marker in lowered:
                raise RegionBlocked(
                    f"当前出口 IP 被地区限制，请更换网络或使用代理 | url={reply.url} | 片段={reply.snippet(120)}"
                )

        if reply.status in self._settings.retry_status:
            raise ResponseInvalid(f"服务端返回临时异常 HTTP {reply.status}", url=reply.url, snippet=reply.snippet())
        if not reply.ok:
            raise BadStatus(reply.status, url=reply.url, snippet=reply.snippet())

    @staticmethod
    def _retryable(error: JmcpyError) -> bool:
        return isinstance(error, _RETRYABLE_ERRORS)

    @staticmethod
    def _endpoint_unusable(error: JmcpyError) -> bool:
        return isinstance(error, _ENDPOINT_FATAL_ERRORS)

    # ------------------------------------------------------------------ 组装
    def _merge_headers(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        headers = dict(self._settings.headers)
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _resolve(url: str, endpoint: str) -> str:
        """把路径或含 ``{endpoint}`` 的模板解析成完整地址。"""
        if url.startswith("/"):
            return f"https://{endpoint}{url}"
        if "{endpoint}" in url:
            return url.format(endpoint=endpoint)
        return url

    def _endpoints_for(self, url: str, endpoints: Sequence[str] | None) -> tuple[str, ...]:
        """决定这次请求的端点顺序。

        * 调用方显式传入端点（例如图片的 CDN 轮换）：完全按传入顺序，不做记忆重排；
        * 绝对地址且不含占位符：只有一次机会；
        * 其余走默认池，并按「上次成功的优先、最近失败的靠后」重排。
        """
        if endpoints is not None:
            return tuple(endpoints)
        if not (url.startswith("/") or "{endpoint}" in url):
            return ("",)
        return self._ordered(self._default_endpoints)

    def _ordered(self, endpoints: Sequence[str]) -> tuple[str, ...]:
        """把上一次成功的端点提到最前，把最近失败的端点放到最后。"""
        if len(endpoints) < 2:
            return tuple(endpoints)
        preferred = tuple(item for item in endpoints if item == self._preferred)
        rest = tuple(item for item in endpoints if item != self._preferred and item not in self._unhealthy)
        unhealthy = tuple(item for item in endpoints if item != self._preferred and item in self._unhealthy)
        return preferred + rest + unhealthy

    def _remember_success(self, endpoint: str) -> None:
        if endpoint:
            self._preferred = endpoint
            self._unhealthy.discard(endpoint)

    def _remember_failure(self, endpoint: str) -> None:
        if endpoint:
            self._unhealthy.add(endpoint)
            if self._preferred == endpoint:
                self._preferred = None

    def _planner(
        self,
        url: str,
        endpoints: Sequence[str] | None,
        retry_times: int | None,
    ) -> RetryPlanner:
        return RetryPlanner(
            self._endpoints_for(url, endpoints),
            mode=self._settings.retry_mode,
            retry_times=self._settings.retry_times if retry_times is None else retry_times,
            backoff_base=self._settings.backoff_base,
            backoff_max=self._settings.backoff_max,
            backoff_jitter=self._settings.backoff_jitter,
        )

    def _build_request(
        self,
        method: str,
        url: str,
        attempt: Attempt,
        *,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
        timeout: float | None,
        allow_redirects: bool,
    ) -> HttpRequest:
        return HttpRequest(
            method=as_http_method(method),
            url=self._resolve(url, attempt.endpoint),
            params=params,
            data=data,
            headers=self._merge_headers(headers),
            timeout=self._settings.timeout if timeout is None else timeout,
            allow_redirects=allow_redirects,
        )

    def _exhausted(self, url: str, endpoints: Sequence[str], failures: Sequence[AttemptFailure]) -> RequestFailed:
        resolved = self._resolve(url, endpoints[0]) if endpoints and endpoints[0] else url
        return RequestFailed(resolved, failures)


class HttpSession(_SessionCore):
    """同步会话。"""

    def __init__(
        self,
        settings: Settings,
        *,
        backend: HttpBackend | None = None,
        default_endpoints: Sequence[str] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(settings, default_endpoints=default_endpoints)
        self._backend = (
            backend
            if backend is not None
            else create_backend(
                settings.backend,
                impersonate=settings.impersonate,
                proxy=settings.proxy,
                verify=settings.verify,
            )
        )
        self._sleeper = sleeper if sleeper is not None else time.sleep

    @property
    def backend(self) -> HttpBackend:
        return self._backend

    def send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        endpoints: Sequence[str] | None = None,
        retry_times: int | None = None,
        validator: Validator | None = None,
    ) -> Reply:
        """发送请求；失败时按配置重试并在端点之间切换。"""
        planner = self._planner(url, endpoints, retry_times)
        chosen = self._endpoints_for(url, endpoints)
        endpoint_fatal: JmcpyError | None = None

        while (attempt := planner.plan()) is not None:
            request = self._build_request(
                method,
                url,
                attempt,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if attempt.delay:
                self._sleeper(attempt.delay)
            try:
                reply = self._backend.send(request)
                self._evaluate(reply)
                if validator is not None:
                    validator(reply)
            except JmcpyError as error:
                self._remember_failure(attempt.endpoint)
                if self._endpoint_unusable(error):
                    # 换一条线路继续，但不重试当前这条
                    planner.record(attempt, request.url, error)
                    planner.skip_endpoint(attempt.endpoint_index)
                    endpoint_fatal = error
                    continue
                if not self._retryable(error):
                    raise
                planner.record(attempt, request.url, error)
                endpoint_fatal = None
                continue
            self._remember_success(attempt.endpoint)
            return reply

        if endpoint_fatal is not None:
            # 所有尝试过的线路都不可用，抛出可操作的那个原因（而不是笼统的「重试失败」）
            raise endpoint_fatal
        raise self._exhausted(url, chosen, planner.failures)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self._backend.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._backend.get_cookies()

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> HttpSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AsyncHttpSession(_SessionCore):
    """异步会话。"""

    def __init__(
        self,
        settings: Settings,
        *,
        backend: AsyncHttpBackend | None = None,
        default_endpoints: Sequence[str] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(settings, default_endpoints=default_endpoints)
        self._backend = (
            backend
            if backend is not None
            else create_async_backend(
                settings.backend,
                impersonate=settings.impersonate,
                proxy=settings.proxy,
                verify=settings.verify,
            )
        )
        self._sleeper = sleeper

    @property
    def backend(self) -> AsyncHttpBackend:
        return self._backend

    async def _sleep(self, delay: float) -> None:
        if self._sleeper is not None:
            await self._sleeper(delay)
            return
        import asyncio

        await asyncio.sleep(delay)

    async def send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        endpoints: Sequence[str] | None = None,
        retry_times: int | None = None,
        validator: Validator | None = None,
    ) -> Reply:
        """异步版的 :meth:`HttpSession.send`。"""
        planner = self._planner(url, endpoints, retry_times)
        chosen = self._endpoints_for(url, endpoints)
        endpoint_fatal: JmcpyError | None = None

        while (attempt := planner.plan()) is not None:
            request = self._build_request(
                method,
                url,
                attempt,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if attempt.delay:
                await self._sleep(attempt.delay)
            try:
                reply = await self._backend.send(request)
                self._evaluate(reply)
                if validator is not None:
                    validator(reply)
            except JmcpyError as error:
                self._remember_failure(attempt.endpoint)
                if self._endpoint_unusable(error):
                    # 换一条线路继续，但不重试当前这条
                    planner.record(attempt, request.url, error)
                    planner.skip_endpoint(attempt.endpoint_index)
                    endpoint_fatal = error
                    continue
                if not self._retryable(error):
                    raise
                planner.record(attempt, request.url, error)
                endpoint_fatal = None
                continue
            self._remember_success(attempt.endpoint)
            return reply

        if endpoint_fatal is not None:
            raise endpoint_fatal
        raise self._exhausted(url, chosen, planner.failures)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self._backend.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._backend.get_cookies()

    async def close(self) -> None:
        await self._backend.close()

    async def __aenter__(self) -> AsyncHttpSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
