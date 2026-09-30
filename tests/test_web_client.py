"""网页端解析与客户端。

网页端无法在本机网络环境下实机验证（会被反爬验证页拦截），因此这里：
结构解析用与线上页面通用形状一致的合成 HTML 校验；客户端的 URL 组装、
重定向分支、错误提示与端点缺失提示则完全可以离线断言。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import pytest

from jmcpy.clients.web import AsyncWebClient, WebClient
from jmcpy.enums import Genre, RankingSpan, SearchTarget, SortBy, SubGenre, TimeRange
from jmcpy.errors import ChallengeBlocked, ConfigurationError, ParseFailed
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

WEB_ENDPOINTS = ("web.example",)

#: 与线上列表页通用结构一致的合成页面：图片链接 + 标题链接 + 标签 + 总数
LISTING_HTML = """
<html><head><title>搜尋結果 | 站點</title></head><body>
<div class="well well-sm">
  <span class="text-white">128</span> A漫.
  <div class="row">
    <div class="col">
      <a href="/album/1114751/example-title"><img src="/media/albums/1114751.jpg"></a>
      <a href="/album/1114751/example-title" title="示例本子一">示例本子一</a>
      <div class="title-truncate tags"><a href="/search/photos?search_query=全彩">全彩</a></div>
    </div>
    <div class="col">
      <a href="/album/2222222/other" title="示例本子二"><img src="/media/albums/2222222.jpg"></a>
      <a href="/album/2222222/other">示例本子二</a>
    </div>
    <div class="col">
      <a href="/album/1114751/example-title" title="示例本子一">重复链接不应产生第二条</a>
    </div>
    <div class="pagination"><li class="active"><a data-page="3">3</a></li></div>
  </div>
</div>
</body></html>
"""

ALBUM_HTML = """
<html><head>
<title>示例本子一 | 禁漫天堂</title>
<meta property="og:url" content="https://web.example/album/1114751/example-title">
</head><body>正文</body></html>
"""

ERROR_HTML = """
<html><body><fieldset><legend>搜尋錯誤</legend><div>關鍵字過短，請至少輸入兩個字以上。</div></fieldset></body></html>
"""


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


def make_client(routes: Mapping[str, str], **overrides: Any) -> tuple[WebClient, FakeWebServer]:
    resolved = make_settings(**overrides)
    server = FakeWebServer(routes)
    session = HttpSession(resolved, backend=server, default_endpoints=WEB_ENDPOINTS, sleeper=lambda _delay: None)
    return WebClient(resolved, session=session), server


# --------------------------------------------------------------------------- 解析
def test_parse_listing_page_extracts_albums_in_order() -> None:
    result = parse_listing_page(LISTING_HTML, page=1)

    assert [item.book_id for item in result.items] == [1114751, 2222222]
    assert [item.title for item in result.items] == ["示例本子一", "示例本子二"]
    assert result.total == 128
    assert result.page == 3
    assert result.error is None


def test_parse_listing_page_prefers_title_attribute() -> None:
    html = '<a href="/album/42/x"><img></a><a href="/album/42/x" title="属性标题">文字标题</a>'

    result = parse_listing_page(html)

    assert result.items[0].title == "属性标题"


def test_parse_listing_page_falls_back_to_anchor_text() -> None:
    html = '<a href="/album/42/x">文字标题</a>'

    assert parse_listing_page(html).items[0].title == "文字标题"


def test_parse_listing_page_without_total() -> None:
    html = '<a href="/album/42/x" title="t">t</a>'

    result = parse_listing_page(html)

    assert result.total is None
    assert result.items


def test_parse_listing_page_detects_error_notice() -> None:
    result = parse_listing_page(ERROR_HTML)

    assert result.error is not None
    assert not result.items


def test_require_usable_page_raises_on_error_notice() -> None:
    result = parse_listing_page(ERROR_HTML)

    with pytest.raises(ParseFailed, match="错误提示"):
        require_usable_page(result, "https://web.example/search/photos")


def test_parse_listing_page_dedupes_repeated_links() -> None:
    html = '<a href="/album/7/a" title="A">A</a><a href="/album/7/a"><img></a>'

    assert len(parse_listing_page(html).items) == 1


def test_parse_listing_page_ignores_non_album_links() -> None:
    html = '<a href="/search/photos?x=1">搜索</a><a href="/album/notanumber/x">坏链接</a>'

    assert parse_listing_page(html).items == ()


def test_parse_album_page_identity_uses_og_url() -> None:
    assert parse_album_page_identity(ALBUM_HTML) == (1114751, "示例本子一")


def test_parse_album_page_identity_falls_back_to_link() -> None:
    html = '<html><head><title>标题 - 站点</title></head><body><a href="/album/99/x">x</a></body></html>'

    assert parse_album_page_identity(html) == (99, "标题")


def test_parse_album_page_identity_returns_none() -> None:
    assert parse_album_page_identity("<html><body>空</body></html>") is None


def test_parse_current_page_variants() -> None:
    assert parse_current_page(LISTING_HTML) == 3
    assert parse_current_page("<html>无分页</html>") is None


# --------------------------------------------------------------------------- 客户端
def test_search_builds_url_with_genre_and_sub_genre() -> None:
    client, server = make_client({"/search/photos/doujin/sub/CG": LISTING_HTML})

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
    assert listing.total == 128
    assert len(listing.items) == 2


def test_search_without_genre_uses_base_path() -> None:
    client, server = make_client({"/search/photos": LISTING_HTML})

    client.search("关键词")

    assert urlparse(server.requests[-1].url).path == "/search/photos"


def test_search_genre_without_sub_genre() -> None:
    client, server = make_client({"/search/photos/hanman": LISTING_HTML})

    client.search("关键词", genre=Genre.HANMAN)

    assert urlparse(server.requests[-1].url).path == "/search/photos/hanman"


def test_sub_genre_requires_genre() -> None:
    client, _ = make_client({})

    with pytest.raises(ConfigurationError, match="副分类必须配合大分类"):
        client.search("关键词", sub_genre=SubGenre.CG)


def test_browse_and_ranking_paths() -> None:
    client, server = make_client(
        {
            "/albums/doujin/sub/CG": LISTING_HTML,
            "/albums": LISTING_HTML,
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
    client, _ = make_client({"/search/photos": ERROR_HTML})

    with pytest.raises(ParseFailed):
        client.search("x")


def test_redirect_to_album_returns_single_item() -> None:
    redirect = Reply(
        status=200,
        url="https://web.example/album/1114751/example-title",
        content=ALBUM_HTML.encode("utf-8"),
        history=("https://web.example/search/photos?search_query=1114751",),
    )
    client, _ = make_client({"/search/photos": redirect})

    listing = client.search("1114751")

    assert listing.total == 1
    assert listing.items[0].book_id == 1114751
    assert listing.items[0].title == "示例本子一"


def test_missing_web_endpoints_raise_actionable_error() -> None:
    resolved = make_settings(web_endpoints=())
    server = FakeWebServer({})
    session = HttpSession(resolved, backend=server, default_endpoints=(), sleeper=lambda _delay: None)
    client = WebClient(resolved, session=session)

    with pytest.raises(ConfigurationError, match="没有可用的网页端域名"):
        client.search("x")


def test_configured_endpoints_are_used() -> None:
    client, server = make_client({"/search/photos": LISTING_HTML})

    client.search("x")

    assert server.requests[0].url.startswith("https://web.example/")


def test_request_carries_browser_headers() -> None:
    client, server = make_client({"/search/photos": LISTING_HTML})

    client.search("x")

    headers = {key.lower(): value for key, value in (server.requests[0].headers or {}).items()}
    assert "user-agent" in headers
    assert headers["accept-language"] == "zh-CN,zh;q=0.9"


def test_refresh_endpoints_without_sources_keeps_empty() -> None:
    resolved = make_settings(web_endpoints=())
    session = HttpSession(resolved, backend=FakeWebServer({}), default_endpoints=(), sleeper=lambda _d: None)
    client = WebClient(resolved, session=session)

    endpoints = client.refresh_endpoints()

    assert endpoints.web == ()


def test_challenge_page_is_reported() -> None:
    challenge = Reply(
        status=403,
        url="https://web.example/search/photos",
        content=b"<html><title>Just a moment...</title></html>",
    )
    client, _ = make_client({"/search/photos": challenge})

    with pytest.raises(ChallengeBlocked):
        client.search("x")


async def test_async_web_client_matches_sync() -> None:
    resolved = make_settings()
    server = AsyncFakeWebServer({"/search/photos/doujin": LISTING_HTML, "/albums": LISTING_HTML})
    session = AsyncHttpSession(resolved, backend=server, default_endpoints=WEB_ENDPOINTS, sleeper=_noop_sleep)
    client = AsyncWebClient(resolved, session=session)

    listing = await client.search("关键词", genre=Genre.DOUJIN)
    ranking = await client.ranking(RankingSpan.MONTH)

    assert listing.total == 128
    assert ranking.items
    assert urlparse(server.requests[0].url).path == "/search/photos/doujin"
    await client.close()


async def _noop_sleep(delay: float) -> None:
    return None


def test_json_dump_helper_keeps_signature_stable() -> None:
    # 保证解析结果里没有不可序列化的对象（便于调用方直接落盘）
    result = parse_listing_page(LISTING_HTML)

    assert json.loads(json.dumps({"total": result.total, "ids": [item.book_id for item in result.items]}))
