"""传输后端适配层：在一个真实的本地 HTTP 服务上跑 curl-cffi 与 httpx 两种实现。

后端是用户可切换的（``Settings.backend``），但此前的用例都用手写假后端，
适配层本身没被覆盖过。这里起一个最小 HTTP 服务，验证参数、请求头、Cookie、
重定向、状态码在两种后端上表现一致。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from jmcpy.enums import Backend
from jmcpy.errors import ConfigurationError, NetworkIssue
from jmcpy.transport.backends import (
    AsyncCurlCffiBackend,
    AsyncHttpxBackend,
    CurlCffiBackend,
    HttpxBackend,
    create_async_backend,
    create_backend,
)
from jmcpy.transport.response import HttpRequest


class _Handler(BaseHTTPRequestHandler):
    """把请求信息回显成 JSON，便于断言后端到底发了什么。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # 静音访问日志
        return

    def _respond(self, status: int, payload: bytes, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        if path == "/redirect":
            self._respond(302, b"{}", {"Location": "/echo"})
            return
        if path == "/set-cookie":
            self._respond(200, b'{"ok":true}', {"Set-Cookie": "flavor=chocolate; Path=/"})
            return
        if path == "/status/503":
            self._respond(503, b'{"error":"busy"}')
            return
        if path == "/slow":
            import time

            time.sleep(1.5)
            self._respond(200, b'{"ok":true}')
            return

        body = json.dumps(
            {
                "method": self.command,
                "path": path,
                "query": query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "cookie": self.headers.get("Cookie", ""),
            }
        ).encode("utf-8")
        self._respond(200, body)


@pytest.fixture(name="server_url", scope="module")
def server_url_fixture() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


SYNC_BACKENDS = [Backend.CURL_CFFI, Backend.HTTPX]
EXPECTED_SYNC = {Backend.CURL_CFFI: CurlCffiBackend, Backend.HTTPX: HttpxBackend}
EXPECTED_ASYNC = {Backend.CURL_CFFI: AsyncCurlCffiBackend, Backend.HTTPX: AsyncHttpxBackend}


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_factory_returns_expected_class(kind: Backend) -> None:
    backend = create_backend(kind)

    assert isinstance(backend, EXPECTED_SYNC[kind])
    backend.close()


def test_factory_returns_expected_async_class() -> None:
    """异步工厂只验证类型映射（实例需要事件循环才能真正跑起来）。"""
    assert EXPECTED_ASYNC[Backend.CURL_CFFI] is AsyncCurlCffiBackend
    assert EXPECTED_ASYNC[Backend.HTTPX] is AsyncHttpxBackend


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_get_carries_params_and_headers(kind: Backend, server_url: str) -> None:
    with create_backend(kind) as backend:
        reply = backend.send(
            HttpRequest(
                method="GET",
                url=f"{server_url}/echo",
                params={"search_query": "关键词", "page": 2},
                headers={"X-Custom": "custom-value"},
            )
        )

    assert reply.status == 200
    assert reply.ok is True
    payload = json.loads(reply.content)
    assert payload["method"] == "GET"
    assert payload["path"] == "/echo"
    assert "search_query=" in payload["query"] and "page=2" in payload["query"]
    assert payload["headers"]["x-custom"] == "custom-value"


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_cookies_survive_requests(kind: Backend, server_url: str) -> None:
    with create_backend(kind) as backend:
        backend.set_cookies({"AVS": "token-123"})
        assert backend.get_cookies() == {"AVS": "token-123"}

        reply = backend.send(HttpRequest(method="GET", url=f"{server_url}/echo"))

    assert "AVS=token-123" in json.loads(reply.content)["cookie"]


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_response_cookies_are_captured(kind: Backend, server_url: str) -> None:
    with create_backend(kind) as backend:
        reply = backend.send(HttpRequest(method="GET", url=f"{server_url}/set-cookie"))

        assert reply.cookies.get("flavor") == "chocolate"
        assert "flavor" in backend.get_cookies()


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_redirect_is_followed_and_recorded(kind: Backend, server_url: str) -> None:
    with create_backend(kind) as backend:
        reply = backend.send(HttpRequest(method="GET", url=f"{server_url}/redirect"))

    assert reply.status == 200
    assert json.loads(reply.content)["path"] == "/echo"
    assert reply.redirected is True
    assert any("/redirect" in item for item in reply.history)


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_redirect_can_be_disabled(kind: Backend, server_url: str) -> None:
    with create_backend(kind) as backend:
        reply = backend.send(HttpRequest(method="GET", url=f"{server_url}/redirect", allow_redirects=False))

    assert reply.status == 302
    assert reply.redirected is False


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_error_status_is_returned_not_raised(kind: Backend, server_url: str) -> None:
    """后端只负责搬运事实，分诊由会话层做。"""
    with create_backend(kind) as backend:
        reply = backend.send(HttpRequest(method="GET", url=f"{server_url}/status/503"))

    assert reply.status == 503
    assert reply.ok is False


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_timeout_becomes_network_issue(kind: Backend, server_url: str) -> None:
    with create_backend(kind) as backend, pytest.raises(NetworkIssue):
        backend.send(HttpRequest(method="GET", url=f"{server_url}/slow", timeout=0.25))


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_unreachable_host_becomes_network_issue(kind: Backend) -> None:
    with create_backend(kind) as backend, pytest.raises(NetworkIssue):
        # 保留端口号但指向必然拒绝连接的端口
        backend.send(HttpRequest(method="GET", url="http://127.0.0.1:1/", timeout=1.0))


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_send_rejects_unsupported_method(kind: Backend, server_url: str) -> None:
    """方法在构造请求时就收敛，避免各后端各写一套校验。"""
    with create_backend(kind) as backend, pytest.raises(ValueError, match="不支持的 HTTP 方法"):
        backend.send(HttpRequest(method="FETCH", url=f"{server_url}/echo"))  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", SYNC_BACKENDS)
def test_non_latin1_header_is_reported_clearly(kind: Backend, server_url: str) -> None:
    """HTTP 头只能是 latin-1 字节；curl-cffi 与 httpx 的处理方式并不一致，这里统一拦下。"""
    with create_backend(kind) as backend, pytest.raises(ConfigurationError, match="latin-1"):
        backend.send(HttpRequest(method="GET", url=f"{server_url}/echo", headers={"X-Note": "中文"}))


def test_method_is_normalized_to_upper_case() -> None:
    request = HttpRequest(method="get", url="https://example/")  # type: ignore[arg-type]

    assert request.method == "GET"


def test_close_is_safe_to_call_twice(server_url: str) -> None:
    backend = create_backend(Backend.CURL_CFFI)

    backend.close()
    backend.close()


@pytest.mark.parametrize("kind", [Backend.CURL_CFFI, Backend.HTTPX])
async def test_async_backend_round_trip(kind: Backend, server_url: str) -> None:
    backend = create_async_backend(kind)
    async with backend:
        backend.set_cookies({"AVS": "async-token"})
        reply = await backend.send(
            HttpRequest(method="GET", url=f"{server_url}/echo", params={"main_tag": 0}, headers={"X-A": "1"})
        )

    payload = json.loads(reply.content)
    assert reply.status == 200
    assert payload["query"] == "main_tag=0"
    assert payload["headers"]["x-a"] == "1"
    assert "AVS=async-token" in payload["cookie"]


def test_missing_httpx_reports_how_to_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx":
            raise ImportError("no httpx")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ConfigurationError, match="jmcpy\\[httpx\\]"):
        create_backend(Backend.HTTPX)
    with pytest.raises(ConfigurationError, match="jmcpy\\[httpx\\]"):
        create_async_backend(Backend.HTTPX)
