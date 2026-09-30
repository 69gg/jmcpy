"""网页端解析与客户端。

结构类断言用**真实录制的夹具**（``scripts/record_web_fixture.py`` 生成：标签结构与数字保真、
可读文本替换为占位符）；少数边界分支（未知版式、仅有 title 属性的版式、分页控件）用
明确标注的合成片段覆盖。客户端的 URL 组装、重定向分支、错误提示与端点缺失提示全部离线断言。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from jmcpy.clients.web import AsyncWebClient, WebClient
from jmcpy.enums import Genre, RankingSpan, SearchTarget, SortBy, SubGenre, TimeRange
from jmcpy.errors import ChallengeBlocked, ConfigurationError, InvalidArgument, ParseFailed
from jmcpy.parsing.web import (
    parse_album_page_identity,
    parse_current_page,
    parse_listing_page,
    require_usable_page,
)
from jmcpy.settings import Settings
from jmcpy.transport.backends import AsyncHttpBackend, HttpBackend
from jmcpy.transport.response import HttpRequest, Reply
from jmcpy.transport.session import AsyncHttpSession, HttpSession

FIXTURES = Path(__file__).parent / "fixtures"
REAL_SEARCH = (FIXTURES / "web_search.html").read_text(encoding="utf-8")
REAL_ERROR = (FIXTURES / "web_error.html").read_text(encoding="utf-8")
REAL_ALBUM = (FIXTURES / "web_album.html").read_text(encoding="utf-8")

#: 真实夹具里前三个车号（与录制顺序一致）
REAL_IDS = [1472715, 1465333, 1458349]

WEB_ENDPOINTS = ("web.example",)

# --- 以下为合成片段，只覆盖夹具里没有的边界分支 ---

#: 少数版式把标题放在链接的 title 属性上
TITLE_ATTR_HTML = '<a href="/album/42/x" title="属性标题"><img></a>'

#: 完全不认识的版式：只有链接，没有任何结构信息
BARE_LINKS_HTML = '<a href="/album/42/x"><img></a><a href="/album/43/x"><img></a>'

#: 分页控件处于第 3 页
PAGINATION_HTML = '<ul class="pagination"><li class="active"><span>3</span></li><li><a href="?page=4">4</a></li></ul>'

CHALLENGE_BODY = b"<html><title>Just a moment...</title></html>"


class FakeWebServer(HttpBackend):
    """按路径返回 HTML，并记录请求。"""

    def __init__(self, routes: Mapping[str, Reply | str]) -> None:
        self.routes = dict(routes)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}

    def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        route = self.routes.get(urlparse(request.url).path)
        if route is None:
            return Reply(status=404, url=request.url, content=b"not found")
        if isinstance(route, Reply):
            return route
        return Reply(status=200, url=request.url, content=route.encode("utf-8"))

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    def close(self) -> None:
        return None


class ChallengeFirstServer(FakeWebServer):
    """第一条线路永远返回验证页，其余正常。"""

    def send(self, request: HttpRequest) -> Reply:
        if "blocked.example" in request.url:
            self.requests.append(request)
            return Reply(status=403, url=request.url, content=CHALLENGE_BODY)
        return super().send(request)


class AsyncFakeWebServer(AsyncHttpBackend):
    """异步版。"""

    def __init__(self, routes: Mapping[str, str]) -> None:
        self.routes = dict(routes)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}

    async def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        body = self.routes.get(urlparse(request.url).path, "")
        return Reply(status=200, url=request.url, content=body.encode("utf-8"))

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    async def close(self) -> None:
        return None


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "web_endpoints": WEB_ENDPOINTS,
        "auto_update_endpoints": False,
        "retry_times": 0,
        "backoff_jitter": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


def make_client(
    routes: Mapping[str, Reply | str],
    endpoints: tuple[str, ...] = WEB_ENDPOINTS,
    **overrides: Any,
) -> tuple[WebClient, FakeWebServer]:
    resolved = make_settings(**overrides, web_endpoints=endpoints)
    server = FakeWebServer(routes) if endpoints == WEB_ENDPOINTS else ChallengeFirstServer(routes)
    session = HttpSession(resolved, backend=server, default_endpoints=endpoints, sleeper=lambda _delay: None)
    return WebClient(resolved, session=session), server


# --------------------------------------------------------------------------- 解析
def test_real_search_page_yields_cards_in_order() -> None:
    result = parse_listing_page(REAL_SEARCH, page=1)

    assert [item.book_id for item in result.items] == REAL_IDS
    assert all(item.title for item in result.items), "真实页面的标题来自 video-title 元素"
    assert all(item.author for item in result.items), "作者来自 main_tag=2 的搜索链接"
    assert result.total == 255, "总数来自 search-pagination-total"
    assert result.page == 1
    assert result.error is None


def test_real_search_fixture_keeps_structure_but_not_content() -> None:
    """夹具只保留结构与数字：不应出现脚本，也没有原始可读文本。"""
    assert "<script" not in REAL_SEARCH
    assert "示例" in REAL_SEARCH


def test_real_error_page_is_reported_with_detail() -> None:
    result = parse_listing_page(REAL_ERROR)

    assert result.error is not None
    assert "过短" in result.error
    assert result.items == (), "错误页不应被当成结果页（侧栏链接不算卡片）"

    with pytest.raises(ParseFailed, match="错误提示"):
        require_usable_page(result, "https://web.example/search/photos")


def test_title_attribute_is_used_when_no_video_title() -> None:
    result = parse_listing_page(TITLE_ATTR_HTML)

    assert [(item.book_id, item.title) for item in result.items] == [(42, "属性标题")]


def test_unknown_layout_falls_back_to_book_ids() -> None:
    result = parse_listing_page(BARE_LINKS_HTML)

    assert [item.book_id for item in result.items] == [42, 43]
    assert [item.title for item in result.items] == ["JM42", "JM43"]


def test_repeated_links_do_not_duplicate_cards() -> None:
    html = '<a href="/album/7/a" title="A">A</a><a href="/album/7/a"><img></a>'

    assert len(parse_listing_page(html).items) == 1


def test_non_album_links_are_ignored() -> None:
    assert parse_listing_page('<a href="/search/photos?x=1">搜索</a>').items == ()


def test_pagination_and_album_identity() -> None:
    assert parse_current_page(PAGINATION_HTML) == 3
    assert parse_current_page("<html>无分页</html>") is None

    assert parse_album_page_identity(REAL_ALBUM) == (1472715, "示例标题")

    fallback = '<html><head><title>标题 - 站点</title></head><a href="/album/99/x">x</a></html>'
    assert parse_album_page_identity(fallback) == (99, "标题")
    assert parse_album_page_identity("<html><body>空</body></html>") is None


# --------------------------------------------------------------------------- 客户端
def test_search_builds_url_with_genre_and_sub_genre() -> None:
    client, server = make_client({"/search/photos/doujin/sub/CG": REAL_SEARCH})

    listing = client.search(
        "关键词",
        page=2,
        target=SearchTarget.TAG,
        sort=SortBy.VIEWS,
        time_range=TimeRange.MONTH,
        genre=Genre.DOUJIN,
        sub_genre=SubGenre.CG,
    )

    request = server.requests[-1]
    assert urlparse(request.url).path == "/search/photos/doujin/sub/CG"
    assert request.params == {"main_tag": 3, "search_query": "关键词", "page": 2, "o": "mv", "t": "m"}
    assert listing.total == 255
    assert [item.book_id for item in listing.items] == REAL_IDS


def test_search_without_genre_uses_base_path() -> None:
    client, server = make_client({"/search/photos": REAL_SEARCH})

    client.search("关键词")

    assert urlparse(server.requests[-1].url).path == "/search/photos"


def test_search_genre_without_sub_genre() -> None:
    client, server = make_client({"/search/photos/hanman": REAL_SEARCH})

    client.search("关键词", genre=Genre.HANMAN)

    assert urlparse(server.requests[-1].url).path == "/search/photos/hanman"


def test_empty_query_is_rejected_with_hint() -> None:
    """网页端搜索页需要关键词；分类浏览要走 browse()。"""
    client, server = make_client({})

    with pytest.raises(InvalidArgument, match="browse"):
        client.search("   ")

    assert server.requests == []


def test_sub_genre_requires_genre() -> None:
    client, _ = make_client({})

    with pytest.raises(ConfigurationError, match="副分类必须配合大分类"):
        client.search("关键词", sub_genre=SubGenre.CG)


def test_browse_and_ranking_paths() -> None:
    client, server = make_client(
        {
            "/albums/doujin/sub/CG": REAL_SEARCH,
            "/albums": REAL_SEARCH,
        }
    )

    client.browse(page=1, genre=Genre.DOUJIN, sub_genre=SubGenre.CG, sort=SortBy.LIKES, time_range=TimeRange.WEEK)
    client.ranking(RankingSpan.DAY, page=3)

    first = server.requests[0]
    assert urlparse(first.url).path == "/albums/doujin/sub/CG"
    assert first.params == {"page": 1, "o": "tf", "t": "w"}
    second = server.requests[1]
    assert urlparse(second.url).path == "/albums"
    assert second.params == {"page": 3, "o": "mv", "t": "t"}


def test_search_error_page_becomes_parse_failed() -> None:
    client, _ = make_client({"/search/photos": REAL_ERROR})

    with pytest.raises(ParseFailed):
        client.search("x")


def test_redirect_to_album_returns_single_item() -> None:
    redirect = Reply(
        status=200,
        url="https://web.example/album/1472715/",
        content=REAL_ALBUM.encode("utf-8"),
        history=("https://web.example/search/photos?search_query=1472715",),
    )
    client, _ = make_client({"/search/photos": redirect})

    listing = client.search("1472715")

    assert listing.total == 1
    assert listing.items[0].book_id == 1472715
    assert listing.items[0].title == "示例标题"


def test_missing_web_endpoints_raise_actionable_error() -> None:
    resolved = make_settings(web_endpoints=())
    server = FakeWebServer({})
    session = HttpSession(resolved, backend=server, default_endpoints=(), sleeper=lambda _delay: None)
    client = WebClient(resolved, session=session)

    with pytest.raises(ConfigurationError, match="没有可用的网页端域名"):
        client.search("x")


def test_configured_endpoints_are_used() -> None:
    client, server = make_client({"/search/photos": REAL_SEARCH})

    client.search("x")

    assert server.requests[0].url.startswith("https://web.example/")


def test_request_carries_browser_headers() -> None:
    client, server = make_client({"/search/photos": REAL_SEARCH})

    client.search("x")

    headers = {key.lower(): value for key, value in (server.requests[0].headers or {}).items()}
    assert "user-agent" in headers
    assert headers["accept-language"] == "zh-CN,zh;q=0.9"


def test_refresh_endpoints_without_sources_keeps_empty() -> None:
    resolved = make_settings(web_endpoints=())
    session = HttpSession(resolved, backend=FakeWebServer({}), default_endpoints=(), sleeper=lambda _d: None)
    client = WebClient(resolved, session=session)

    assert client.refresh_endpoints().web == ()


def test_challenge_on_one_endpoint_uses_the_other() -> None:
    """被验证页拦截的只是这条线路：换下一个域名继续，而不是整个请求失败。"""
    client, server = make_client(
        {"/search/photos": REAL_SEARCH},
        endpoints=("blocked.example", "ok.example"),
        auto_update_endpoints=False,
    )

    listing = client.search("x")

    assert [item.book_id for item in listing.items] == REAL_IDS
    assert [urlparse(item.url).netloc for item in server.requests] == ["blocked.example", "ok.example"]


def test_all_endpoints_challenged_raises_challenge() -> None:
    challenge = Reply(status=403, url="https://web.example/search/photos", content=CHALLENGE_BODY)
    client, _ = make_client({"/search/photos": challenge})

    with pytest.raises(ChallengeBlocked):
        client.search("x")


def test_json_dump_helper_keeps_signature_stable() -> None:
    # 解析结果里不应有不可序列化的对象（便于调用方直接落盘）
    result = parse_listing_page(REAL_SEARCH)

    assert json.loads(json.dumps({"total": result.total, "ids": [item.book_id for item in result.items]}))


async def test_async_web_client_matches_sync() -> None:
    resolved = make_settings()
    server = AsyncFakeWebServer({"/search/photos/doujin": REAL_SEARCH, "/albums": REAL_SEARCH})
    session = AsyncHttpSession(resolved, backend=server, default_endpoints=WEB_ENDPOINTS, sleeper=_noop_sleep)
    client = AsyncWebClient(resolved, session=session)

    listing = await client.search("关键词", genre=Genre.DOUJIN)
    ranking = await client.ranking(RankingSpan.MONTH)

    assert listing.total == 255
    assert [item.book_id for item in ranking.items] == REAL_IDS
    assert urlparse(server.requests[0].url).path == "/search/photos/doujin"
    await client.close()


async def _noop_sleep(delay: float) -> None:
    return None
