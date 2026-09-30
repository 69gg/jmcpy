"""门面客户端：能力路由、委托与资源管理。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import pytest

from jmcpy.clients.facade import Client, _resolve_settings
from jmcpy.clients.mobile import MobileClient
from jmcpy.clients.web import WebClient
from jmcpy.constants import MOBILE_PAYLOAD_SALT
from jmcpy.crypto import seal_payload
from jmcpy.endpoints import EndpointSet
from jmcpy.enums import Genre, RankingSpan, SearchTarget, SortBy, SubGenre, TimeRange
from jmcpy.errors import ConfigurationError
from jmcpy.models import Account
from jmcpy.settings import Settings
from jmcpy.transport.backends import HttpBackend
from jmcpy.transport.response import HttpRequest, Reply
from jmcpy.transport.session import HttpSession

MOBILE_ENDPOINTS = ("mobile.example",)
WEB_ENDPOINTS = ("web.example",)

SCRAMBLE_HTML = "<html><script>var scramble_id = 220980;</script></html>"

LISTING_HTML = '<span class="text-white">7</span> A漫.<a href="/album/4242/x" title="网页结果">网页结果</a>'


class FakeServer(HttpBackend):
    """同时扮演移动端与网页端：按路径前缀决定用哪种应答。"""

    def __init__(self, routes: Mapping[str, Any]) -> None:
        self.routes = dict(routes)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}
        self.closed = False

    def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        path = urlparse(request.url).path
        route = self.routes.get(path)
        if callable(route):
            route = route(request)
        if route is None:
            return Reply(status=404, url=request.url, content=b"{}")
        if isinstance(route, str):
            return Reply(status=200, url=request.url, content=route.encode("utf-8"))
        timestamp = str((request.headers or {}).get("tokenparam", "0,0")).split(",")[0]
        body = {"code": 200, "data": seal_payload(str(route), timestamp, MOBILE_PAYLOAD_SALT)}
        import json

        if isinstance(route, dict):
            body = {
                "code": 200,
                "data": seal_payload(json.dumps(route, ensure_ascii=False), timestamp, MOBILE_PAYLOAD_SALT),
            }
        return Reply(status=200, url=request.url, content=json.dumps(body).encode("utf-8"))

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    def close(self) -> None:
        self.closed = True

    def paths(self) -> list[str]:
        return [urlparse(item.url).path for item in self.requests]


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mobile_endpoints": MOBILE_ENDPOINTS,
        "web_endpoints": WEB_ENDPOINTS,
        "cdn_endpoints": ("cdn.example",),
        "auto_update_endpoints": False,
        "retry_times": 0,
        "backoff_jitter": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def make_pair(load_fixture: Callable[[str], Any], **overrides: Any) -> tuple[Settings, FakeServer, FakeServer]:
    """建一对（移动端、网页端）假服务端与共享配置。"""
    resolved = make_settings(**overrides)
    mobile_server = FakeServer(
        {
            "/setting": {"jm3_version": "2.1.9"},
            "/search": load_fixture("mobile_search.json"),
            "/album": load_fixture("mobile_album.json"),
            "/chapter": load_fixture("mobile_chapter.json"),
            "/forum": load_fixture("mobile_comments.json"),
            "/categories/filter": load_fixture("mobile_search.json"),
            "/chapter_view_template": SCRAMBLE_HTML,
            "/login": {"uid": "9", "username": "u", "s": "AVS-X"},
        }
    )
    web_server = FakeServer(
        {
            "/search/photos/doujin/sub/CG": LISTING_HTML,
            "/search/photos/doujin": LISTING_HTML,
            "/albums": LISTING_HTML,
            "/albums/doujin/sub/CG": LISTING_HTML,
        }
    )
    return resolved, mobile_server, web_server


def make_client(resolved: Settings, mobile_server: FakeServer, web_server: FakeServer | None) -> Client:
    mobile = MobileClient(
        resolved,
        session=HttpSession(
            resolved, backend=mobile_server, default_endpoints=MOBILE_ENDPOINTS, sleeper=lambda _d: None
        ),
    )
    web = None
    if web_server is not None:
        web = WebClient(
            resolved,
            session=HttpSession(resolved, backend=web_server, default_endpoints=WEB_ENDPOINTS, sleeper=lambda _d: None),
        )
    return Client(resolved, mobile=mobile, web=web)


def test_resolve_settings_accepts_none_settings_and_mapping() -> None:
    assert isinstance(_resolve_settings(None), Settings)
    assert _resolve_settings({"timeout": 7}).timeout == 7
    assert _resolve_settings(make_settings()).backend is Settings().backend


def test_resolve_settings_rejects_other_types() -> None:
    with pytest.raises(ConfigurationError, match="不支持的配置类型"):
        _resolve_settings("not-a-settings")  # type: ignore[arg-type]


def test_search_defaults_to_mobile(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    listing = client.search("MANA", target=SearchTarget.TAG, sort=SortBy.VIEWS, time_range=TimeRange.WEEK)

    assert listing.items
    assert "/search" in mobile_server.paths()
    assert web_server.paths() == [], "没有副分类时不应触碰网页端"


def test_search_with_sub_genre_routes_to_web(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    listing = client.search("关键词", genre=Genre.DOUJIN, sub_genre=SubGenre.CG)

    assert [item.book_id for item in listing.items] == [4242]
    assert mobile_server.paths() == [], "副分类搜索不应再打移动端接口"
    assert web_server.paths() == ["/search/photos/doujin/sub/CG"]


def test_browse_and_ranking_routing(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    client.ranking(RankingSpan.WEEK, page=1)
    client.ranking(RankingSpan.WEEK, page=1, genre=Genre.DOUJIN, sub_genre=SubGenre.CG)

    assert "/categories/filter" in mobile_server.paths()
    assert "/albums/doujin/sub/CG" in web_server.paths()


def test_auto_route_can_be_disabled(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture, auto_route=False)
    client = make_client(resolved, mobile_server, web_server)

    with pytest.raises(ConfigurationError, match="关闭自动路由"):
        client.search("x", genre=Genre.DOUJIN, sub_genre=SubGenre.CG)


def test_web_client_is_created_lazily(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, _ = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, None)

    client.search("MANA")

    assert client._web is None, "没有用到网页端能力时不应创建网页端客户端"
    assert client.web is not None
    assert client._web is not None


def test_delegated_methods(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    book = client.get_book(1114751)
    chapter = client.get_chapter(1114751)
    feed = client.get_comments(1472715)
    scramble = client.get_scramble_id(1114751)
    pages = list(client.iter_comments(1472715, limit=1))

    assert book.book_id == 1114751
    assert chapter.pictures
    assert feed.items
    assert scramble == 220980
    assert len(pages) == 1
    assert client.cover_url(1).endswith("/media/albums/1.jpg")


def test_login_syncs_cookies_to_web(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    account = client.login("u", "p")

    assert isinstance(account, Account)
    assert client.is_logged_in() is True
    assert web_server.cookies.get("AVS") == "AVS-X", "登录后网页端也应带上凭据"


def test_logout_clears_credentials(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)
    client.login("u", "p")

    client.logout()

    assert client.is_logged_in() is False
    assert web_server.cookies.get("AVS") == ""


def test_set_cookies_applies_to_both(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    client.set_cookies({"AVS": "T"})

    assert client.get_cookies() == {"AVS": "T"}
    assert web_server.cookies.get("AVS") == "T"


def test_refresh_endpoints_applies_web_domains(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)

    endpoints = client.refresh_endpoints()

    assert endpoints.mobile == MOBILE_ENDPOINTS
    assert client.endpoints == MOBILE_ENDPOINTS


def test_close_closes_both(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)
    _ = client.web  # 触发创建

    client.close()

    assert mobile_server.closed is True
    assert web_server.closed is True


def test_context_manager(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)

    with make_client(resolved, mobile_server, web_server) as client:
        assert client.search("MANA").items

    assert mobile_server.closed is True


def test_endpoint_set_is_shared_with_web(load_fixture: Callable[[str], Any]) -> None:
    resolved, mobile_server, web_server = make_pair(load_fixture)
    client = make_client(resolved, mobile_server, web_server)
    discovered = EndpointSet(mobile=("m.example",), cdn=(), web=("w1.example", "w2.example"))

    client.mobile._apply_endpoints(discovered, client.mobile.session)
    if discovered.web:
        client.web.session.set_endpoints(discovered.web)

    assert client.web.endpoints == ("w1.example", "w2.example")


async def test_async_facade_delegates(load_fixture: Callable[[str], Any]) -> None:
    from jmcpy.clients.facade import AsyncClient
    from jmcpy.clients.mobile import AsyncMobileClient
    from jmcpy.clients.web import AsyncWebClient
    from jmcpy.transport.backends import AsyncHttpBackend
    from jmcpy.transport.session import AsyncHttpSession

    class AsyncServer(AsyncHttpBackend):
        def __init__(self, routes: Mapping[str, Any]) -> None:
            self.routes = dict(routes)
            self.cookies: dict[str, str] = {}
            self.requests: list[HttpRequest] = []

        async def send(self, request: HttpRequest) -> Reply:
            import json

            self.requests.append(request)
            path = urlparse(request.url).path
            route = self.routes[path]
            if isinstance(route, str):
                return Reply(status=200, url=request.url, content=route.encode("utf-8"))
            timestamp = str((request.headers or {}).get("tokenparam", "0,0")).split(",")[0]
            body = {
                "code": 200,
                "data": seal_payload(json.dumps(route, ensure_ascii=False), timestamp, MOBILE_PAYLOAD_SALT),
            }
            return Reply(status=200, url=request.url, content=json.dumps(body).encode("utf-8"))

        def set_cookies(self, cookies: Mapping[str, str]) -> None:
            self.cookies.update(cookies)

        def get_cookies(self) -> dict[str, str]:
            return dict(self.cookies)

        async def close(self) -> None:
            return None

    async def noop(delay: float) -> None:
        return None

    resolved = make_settings()
    mobile_server = AsyncServer({"/setting": {}, "/search": load_fixture("mobile_search.json")})
    web_server = AsyncServer({"/search/photos/doujin/sub/CG": LISTING_HTML})
    client = AsyncClient(
        resolved,
        mobile=AsyncMobileClient(
            resolved,
            session=AsyncHttpSession(resolved, backend=mobile_server, default_endpoints=MOBILE_ENDPOINTS, sleeper=noop),
        ),
        web=AsyncWebClient(
            resolved,
            session=AsyncHttpSession(resolved, backend=web_server, default_endpoints=WEB_ENDPOINTS, sleeper=noop),
        ),
    )

    async with client:
        listing = await client.search("MANA")
        routed = await client.search("关键词", genre=Genre.DOUJIN, sub_genre=SubGenre.CG)

    assert listing.items
    assert [item.book_id for item in routed.items] == [4242]
    assert urlparse(mobile_server.requests[-1].url).path == "/search"
