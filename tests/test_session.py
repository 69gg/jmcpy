"""会话层：重试循环、端点切换、响应分诊与异常语义。

全部用假后端驱动，不产生任何网络请求；sleep 也被替换成记录器，测试不会真的等待。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import pytest

from jmcpy.enums import Backend, RetryMode
from jmcpy.errors import (
    AttemptFailure,
    BadStatus,
    ChallengeBlocked,
    NetworkIssue,
    ParseFailed,
    RegionBlocked,
    RequestFailed,
    ResponseInvalid,
)
from jmcpy.settings import Settings
from jmcpy.transport.backends import AsyncHttpBackend, HttpBackend
from jmcpy.transport.response import HttpRequest, Reply
from jmcpy.transport.session import AsyncHttpSession, HttpSession

ENDPOINTS = ("a.example", "b.example")


def make_reply(status: int = 200, body: bytes = b'{"ok":true}', url: str = "https://a.example/x") -> Reply:
    return Reply(status=status, url=url, content=body, headers={"content-type": "application/json"})


class ScriptedBackend(HttpBackend):
    """按脚本依次返回响应或抛异常；脚本用完后重复最后一项。"""

    def __init__(self, script: Sequence[Reply | Exception]) -> None:
        self.script = list(script)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}
        self.closed = False

    def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return replace(item, url=request.url)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    def close(self) -> None:
        self.closed = True


class AsyncScriptedBackend(AsyncHttpBackend):
    """异步版的 :class:`ScriptedBackend`。"""

    def __init__(self, script: Sequence[Reply | Exception]) -> None:
        self.script = list(script)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}

    async def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return replace(item, url=request.url)

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    async def close(self) -> None:
        return None


class SleepRecorder:
    """记录退避时长，避免测试真的等待。"""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_session(
    script: Sequence[Reply | Exception],
    *,
    retry_times: int = 1,
    retry_mode: RetryMode = RetryMode.RETRY_FIRST,
    **overrides: Any,
) -> tuple[HttpSession, ScriptedBackend, SleepRecorder]:
    # 关掉抖动，退避时长才是确定的，便于断言
    settings = Settings(
        backend=Backend.CURL_CFFI,
        retry_times=retry_times,
        retry_mode=retry_mode,
        backoff_jitter=0.0,
        **overrides,
    )
    backend = ScriptedBackend(script)
    sleeper = SleepRecorder()
    session = HttpSession(settings, backend=backend, default_endpoints=ENDPOINTS, sleeper=sleeper)
    return session, backend, sleeper


def test_successful_request_returns_reply_without_sleeping() -> None:
    session, backend, sleeper = make_session([make_reply()])

    reply = session.send("GET", "/album", params={"id": 1})

    assert reply.status == 200
    assert len(backend.requests) == 1
    assert backend.requests[0].url == "https://a.example/album"
    assert backend.requests[0].params == {"id": 1}
    assert sleeper.delays == []


def test_retryable_status_retries_same_endpoint_after_backoff() -> None:
    session, backend, sleeper = make_session([make_reply(503), make_reply(200)])

    reply = session.send("GET", "/album")

    assert reply.status == 200
    assert [request.url for request in backend.requests] == ["https://a.example/album"] * 2
    assert sleeper.delays == [0.5]


def test_exhausted_retries_switch_to_next_endpoint() -> None:
    session, backend, _ = make_session([make_reply(503), make_reply(200)], retry_times=0)

    reply = session.send("GET", "/album")

    assert reply.status == 200
    assert [request.url for request in backend.requests] == [
        "https://a.example/album",
        "https://b.example/album",
    ]


def test_rotate_first_tries_each_endpoint_before_second_round() -> None:
    session, backend, _ = make_session(
        [make_reply(503), make_reply(503), make_reply(200)],
        retry_times=1,
        retry_mode=RetryMode.ROTATE_FIRST,
    )

    reply = session.send("GET", "/album")

    assert reply.status == 200
    assert [request.url for request in backend.requests] == [
        "https://a.example/album",
        "https://b.example/album",
        "https://a.example/album",
    ]


def test_all_attempts_failing_raises_request_failed_with_every_failure() -> None:
    session, backend, _ = make_session([make_reply(503)], retry_times=1)

    with pytest.raises(RequestFailed) as excinfo:
        session.send("GET", "/album")

    failures = excinfo.value.failures
    assert len(failures) == 4  # 2 端点 × (1 + 1 次重试)
    assert all(isinstance(item, AttemptFailure) for item in failures)
    assert [item.endpoint for item in failures] == ["a.example", "a.example", "b.example", "b.example"]
    assert len(backend.requests) == 4
    assert "共尝试 4 次" in str(excinfo.value)


def test_fatal_status_is_not_retried() -> None:
    session, backend, sleeper = make_session([make_reply(404)])

    with pytest.raises(BadStatus) as excinfo:
        session.send("GET", "/album/1")

    assert excinfo.value.status == 404
    assert len(backend.requests) == 1
    assert sleeper.delays == []


def test_challenge_page_fails_fast_with_actionable_message() -> None:
    body = b"<html><title>Just a moment...</title>Enable JavaScript and cookies to continue</html>"
    session, backend, _ = make_session([make_reply(403, body)])

    with pytest.raises(ChallengeBlocked, match="反爬验证页"):
        session.send("GET", "/search/photos")

    assert len(backend.requests) == 1


def test_region_block_page_fails_fast() -> None:
    session, backend, _ = make_session([make_reply(403, b"Restricted Access!")])

    with pytest.raises(RegionBlocked, match="地区限制"):
        session.send("GET", "/album")

    assert len(backend.requests) == 1


def test_challenge_detection_wins_over_retryable_status() -> None:
    # 403 同时在 retry_status 里，但验证页必须直接失败而不是重试
    body = b"<html><script>__cf_chl_opt</script></html>"
    session, backend, _ = make_session([make_reply(403, body)], retry_times=3)

    with pytest.raises(ChallengeBlocked):
        session.send("GET", "/x")

    assert len(backend.requests) == 1


def test_network_issue_is_retried() -> None:
    session, backend, sleeper = make_session([NetworkIssue("连接被重置"), make_reply(200)])

    reply = session.send("GET", "/album")

    assert reply.status == 200
    assert len(backend.requests) == 2
    assert sleeper.delays == [0.5]


def test_validator_failure_is_retried_then_reported() -> None:
    def reject_non_json(reply: Reply) -> None:
        if not reply.text.startswith("{"):
            raise ResponseInvalid("响应不是 JSON", url=reply.url, snippet=reply.text)

    session, backend, _ = make_session([make_reply(200, b"<html>oops</html>")], retry_times=0)

    with pytest.raises(RequestFailed):
        session.send("GET", "/album", validator=reject_non_json)

    assert len(backend.requests) == 2


def test_validator_fatal_error_stops_immediately() -> None:
    def reject(reply: Reply) -> None:
        raise ParseFailed("结构不对")

    session, backend, _ = make_session([make_reply()], retry_times=3)

    with pytest.raises(ParseFailed):
        session.send("GET", "/album", validator=reject)

    assert len(backend.requests) == 1


def test_unexpected_exception_from_backend_propagates() -> None:
    session, _, _ = make_session([ValueError("后端炸了")])

    with pytest.raises(ValueError, match="后端炸了"):
        session.send("GET", "/album")


def test_absolute_url_without_placeholder_retries_in_place() -> None:
    # 没有 {endpoint} 占位符时不会换端点，但同址重试依然生效
    session, backend, _ = make_session([NetworkIssue("down")], retry_times=2)

    with pytest.raises(RequestFailed):
        session.send("GET", "https://cdn.example/media/photos/1/00001.webp")

    assert [request.url for request in backend.requests] == ["https://cdn.example/media/photos/1/00001.webp"] * 3


def test_endpoint_placeholder_is_substituted_and_rotated() -> None:
    session, backend, _ = make_session(
        [NetworkIssue("down"), NetworkIssue("down"), make_reply(200)],
        retry_times=0,
    )

    session.send(
        "GET",
        "https://{endpoint}/media/photos/1/00001.webp",
        endpoints=("cdn1.example", "cdn2.example", "cdn3.example"),
    )

    assert [request.url for request in backend.requests] == [
        "https://cdn1.example/media/photos/1/00001.webp",
        "https://cdn2.example/media/photos/1/00001.webp",
        "https://cdn3.example/media/photos/1/00001.webp",
    ]


def test_per_request_retry_times_overrides_settings() -> None:
    session, backend, _ = make_session([make_reply(503)], retry_times=5)

    with pytest.raises(RequestFailed):
        session.send("GET", "/album", retry_times=0)

    # retry_times=0 表示每个端点只试一次
    assert len(backend.requests) == 2


def test_headers_merge_settings_then_request() -> None:
    session, backend, _ = make_session([make_reply()], headers={"X-Base": "1", "X-Both": "base"})

    session.send("GET", "/album", headers={"X-Extra": "2", "X-Both": "request"})

    headers = backend.requests[0].headers or {}
    assert headers["X-Base"] == "1"
    assert headers["X-Extra"] == "2"
    assert headers["X-Both"] == "request"


def test_timeout_defaults_to_settings_and_can_be_overridden() -> None:
    session, backend, _ = make_session([make_reply(), make_reply()], timeout=20.0, image_timeout=60.0)

    session.send("GET", "/album")
    session.send("GET", "/image", timeout=60.0)

    assert [request.timeout for request in backend.requests] == [20.0, 60.0]


def test_session_delegates_cookies_and_close() -> None:
    session, backend, _ = make_session([make_reply()])

    session.set_cookies({"AVS": "token"})
    assert session.get_cookies() == {"AVS": "token"}

    session.close()
    assert backend.closed is True


def test_set_endpoints_replaces_pool() -> None:
    session, backend, _ = make_session([make_reply(503), make_reply(200)], retry_times=0)

    session.set_endpoints(("z.example", "y.example"))

    assert session.endpoints == ("z.example", "y.example")

    session.send("GET", "/album")

    assert [request.url for request in backend.requests] == [
        "https://z.example/album",
        "https://y.example/album",
    ]


async def test_async_session_mirrors_sync_behaviour() -> None:
    settings = Settings(retry_times=1, backoff_jitter=0.0)
    backend = AsyncScriptedBackend([make_reply(503), make_reply(200)])
    delays: list[float] = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    session = AsyncHttpSession(settings, backend=backend, default_endpoints=ENDPOINTS, sleeper=sleeper)

    reply = await session.send("GET", "/album")

    assert reply.status == 200
    assert [request.url for request in backend.requests] == ["https://a.example/album"] * 2
    assert delays == [0.5]


async def test_async_session_raises_request_failed_after_exhaustion() -> None:
    settings = Settings(retry_times=0, backoff_jitter=0.0)

    async def sleeper(delay: float) -> None:
        return None

    backend = AsyncScriptedBackend([make_reply(503)])
    session = AsyncHttpSession(settings, backend=backend, default_endpoints=ENDPOINTS, sleeper=sleeper)

    with pytest.raises(RequestFailed) as excinfo:
        await session.send("GET", "/album")

    assert [item.endpoint for item in excinfo.value.failures] == ["a.example", "b.example"]
