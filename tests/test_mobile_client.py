"""移动端客户端：请求签名、参数组装、端点/版本协商与解析串起来的端到端行为。

假后端扮演服务端：它从请求头里读回时间戳，用**同一套协议**加密应答，
因此这些用例会真正走一遍签名 → 加解密 → 解析的完整链路。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import pytest

from jmcpy.clients.mobile import AsyncMobileClient, MobileClient, resolve_book_id
from jmcpy.constants import CHAPTER_TOKEN_SALT, MOBILE_PAYLOAD_SALT, MOBILE_TOKEN_SALT, PATH_CHAPTER_TOKEN
from jmcpy.crypto import md5_hex, seal_payload
from jmcpy.enums import Genre, RankingSpan, SearchTarget, SortBy, TimeRange
from jmcpy.errors import ApiRejected, AuthRejected, InvalidArgument, ParseFailed, RequestFailed
from jmcpy.settings import Settings
from jmcpy.transport.backends import AsyncHttpBackend, HttpBackend
from jmcpy.transport.response import HttpRequest, Reply
from jmcpy.transport.session import AsyncHttpSession, HttpSession

ENDPOINTS = ("mobile.example",)
CDN = "cdn.example"
SCRAMBLE_HTML = "<html><script>var scramble_id = 268850;</script></html>"


class FakeServer(HttpBackend):
    """按路径返回夹具/合成数据，并用请求头里的时间戳加密应答。"""

    def __init__(self, routes: Mapping[str, Any], *, cookies_on_setting: bool = True) -> None:
        self.routes = dict(routes)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}
        self._cookies_on_setting = cookies_on_setting
        self.salts: list[str | None] = None  # type: ignore[assignment]
        self.salts = []

    # -- 服务端行为 --
    def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        headers = {key.lower(): value for key, value in (request.headers or {}).items()}
        timestamp, _, version = headers["tokenparam"].partition(",")
        path = urlparse(request.url).path
        salt = CHAPTER_TOKEN_SALT if path == PATH_CHAPTER_TOKEN else MOBILE_TOKEN_SALT
        self.salts.append(CHAPTER_TOKEN_SALT if salt == CHAPTER_TOKEN_SALT else None)
        assert headers["token"] == md5_hex(f"{timestamp}{salt}"), "请求头签名不符合协议"
        assert version, "tokenparam 必须带上接口版本"

        if path == "/setting" and self._cookies_on_setting:
            self.cookies.setdefault("JM_SESSION", "seeded")

        route = self.routes.get(path)
        if route is None:
            return Reply(status=404, url=request.url, content=b"{}")
        if callable(route):
            route = route(request)
        if isinstance(route, str):
            return Reply(status=200, url=request.url, content=route.encode("utf-8"))
        if isinstance(route, dict) and route.get("code", 200) != 200:
            body = json.dumps({"code": route["code"], "error_msg": route.get("error_msg", "")})
            return Reply(status=200, url=request.url, content=body.encode("utf-8"))
        return Reply(
            status=200,
            url=request.url,
            content=json.dumps(
                {
                    "code": 200,
                    "data": seal_payload(json.dumps(route, ensure_ascii=False), timestamp, MOBILE_PAYLOAD_SALT),
                }
            ).encode("utf-8"),
        )

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    def close(self) -> None:
        return None

    # -- 断言辅助 --
    def path_params(self, path: str, index: int = 0) -> dict[str, Any]:
        matches = [item for item in self.requests if urlparse(item.url).path == path]
        return dict(matches[index].params or {})

    def count(self, path: str) -> int:
        return sum(1 for item in self.requests if urlparse(item.url).path == path)


class AsyncFakeServer(AsyncHttpBackend):
    """异步版假服务端。"""

    def __init__(self, routes: Mapping[str, Any]) -> None:
        self.routes = dict(routes)
        self.requests: list[HttpRequest] = []
        self.cookies: dict[str, str] = {}

    async def send(self, request: HttpRequest) -> Reply:
        self.requests.append(request)
        headers = {key.lower(): value for key, value in (request.headers or {}).items()}
        timestamp, _, _ = headers["tokenparam"].partition(",")
        path = urlparse(request.url).path
        route = self.routes[path]
        if callable(route):
            route = route(request)
        if isinstance(route, str):
            return Reply(status=200, url=request.url, content=route.encode("utf-8"))
        return Reply(
            status=200,
            url=request.url,
            content=json.dumps(
                {
                    "code": 200,
                    "data": seal_payload(json.dumps(route, ensure_ascii=False), timestamp, MOBILE_PAYLOAD_SALT),
                }
            ).encode("utf-8"),
        )

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self.cookies.update(cookies)

    def get_cookies(self) -> dict[str, str]:
        return dict(self.cookies)

    async def close(self) -> None:
        return None


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "mobile_endpoints": ENDPOINTS,
        "cdn_endpoints": (CDN,),
        "auto_update_endpoints": False,
        "auto_update_mobile_version": True,
        "backoff_jitter": 0.0,
        "retry_times": 0,
    }
    base.update(overrides)
    return Settings(**base)


def make_client(routes: Mapping[str, Any], **overrides: Any) -> tuple[MobileClient, FakeServer]:
    resolved = settings(**overrides)
    server = FakeServer(routes)
    session = HttpSession(resolved, backend=server, default_endpoints=ENDPOINTS, sleeper=lambda _delay: None)
    return MobileClient(resolved, session=session), server


def base_routes(load_fixture: Callable[[str], Any]) -> dict[str, Any]:
    return {
        "/setting": {"jm3_version": "2.1.9"},
        "/search": load_fixture("mobile_search.json"),
        "/album": load_fixture("mobile_album.json"),
        "/chapter": load_fixture("mobile_chapter.json"),
        "/forum": load_fixture("mobile_comments.json"),
        "/categories/filter": load_fixture("mobile_search.json"),
        PATH_CHAPTER_TOKEN: SCRAMBLE_HTML,
    }


def test_search_parses_listing_and_seeds_session(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    with client:
        listing = client.search("MANA")

    assert listing.total
    assert listing.items
    assert server.count("/setting") == 1, "初始化只应发生一次"
    assert client.get_cookies() == {"JM_SESSION": "seeded"}


def test_search_sends_all_supported_options(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    client.search(
        "关键词",
        page=3,
        target=SearchTarget.AUTHOR,
        sort=SortBy.VIEWS,
        time_range=TimeRange.WEEK,
        genre=Genre.DOUJIN,
    )

    assert server.path_params("/search") == {
        "main_tag": 2,
        "search_query": "关键词",
        "page": 3,
        "o": "mv",
        "t": "w",
        "c": "doujin",
    }


def test_search_omits_genre_when_all(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    client.search("查询")

    assert "c" not in server.path_params("/search")


def test_search_redirect_fetches_book(load_fixture: Callable[[str], Any]) -> None:
    routes = base_routes(load_fixture)
    routes["/search"] = {"search_query": "123", "total": 1, "redirect_aid": "1114751", "content": []}
    client, server = make_client(routes)

    listing = client.search("1114751")

    assert len(listing) == 1
    assert listing.total == 1
    assert listing.items[0].book_id == 1114751
    assert server.count("/album") == 1


def test_browse_and_ranking_params(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    client.browse(page=2, genre=Genre.HANMAN, sort=SortBy.LIKES, time_range=TimeRange.MONTH)
    client.ranking(RankingSpan.DAY, page=1)
    client.browse(page=1)

    assert server.path_params("/categories/filter", 0) == {
        "page": 2,
        "order": "",
        "c": "hanman",
        "o": "tf_m",
    }
    assert server.path_params("/categories/filter", 1)["o"] == "mv_t"
    assert server.path_params("/categories/filter", 2)["o"] == "mr"


def test_get_book_and_cover_url(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    book = client.get_book("JM1114751")

    assert book.book_id == 1114751
    assert book.chapters
    assert server.path_params("/album") == {"id": 1114751}
    assert client.cover_url(book.book_id) == f"https://{CDN}/media/albums/1114751.jpg"
    assert client.cover_url(book.book_id, size="_3x4").endswith("1114751_3x4.jpg")


def test_get_chapter_uses_chapter_salt_for_scramble(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    chapter = client.get_chapter(1114751)

    assert chapter.pictures
    assert chapter.scramble_id == 268850
    assert server.count(PATH_CHAPTER_TOKEN) == 1
    assert server.salts[-1] == CHAPTER_TOKEN_SALT, "解扰接口必须使用独立的盐值"
    assert server.path_params(PATH_CHAPTER_TOKEN)["id"] == 1114751


def test_scramble_id_is_cached(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    assert client.get_scramble_id(1) == 268850
    assert client.get_scramble_id(1) == 268850

    assert server.count(PATH_CHAPTER_TOKEN) == 1


def test_get_chapter_can_skip_scramble(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    chapter = client.get_chapter(1114751, with_scramble_id=False)

    assert chapter.scramble_id is None
    assert server.count(PATH_CHAPTER_TOKEN) == 0


def test_get_comments_and_iteration(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_comments.json")
    routes = base_routes(load_fixture)
    routes["/forum"] = payload
    client, server = make_client(routes)

    feed = client.get_comments(1472715, page=2)

    assert feed.page == 2
    assert any(comment.replies for comment in feed.items)
    assert server.path_params("/forum") == {"mode": "all", "page": 2, "aid": 1472715}


def test_iter_comments_stops_at_limit(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))

    feeds = list(client.iter_comments(1472715, limit=2))

    assert len(feeds) == 2
    assert server.count("/forum") == 2


def test_iter_comments_stops_at_last_page(load_fixture: Callable[[str], Any]) -> None:
    payload = dict(load_fixture("mobile_comments.json"), total=5)  # 每页 10 条 → 只有一页
    routes = base_routes(load_fixture)
    routes["/forum"] = payload
    client, server = make_client(routes)

    feeds = list(client.iter_comments(1))

    assert len(feeds) == 1
    assert server.count("/forum") == 1


def test_login_posts_credentials_and_sets_avs() -> None:
    profile = {"uid": "9", "username": "u", "s": "AVS-VALUE", "photo": "9.gif", "coin": "1"}
    client, server = make_client({"/setting": {"jm3_version": "2.1.9"}, "/login": profile})

    account = client.login("u", "p")

    assert account.uid == "9"
    assert account.avatar_url == f"https://{CDN}/media/users/9.gif"
    assert client.get_cookies()["AVS"] == "AVS-VALUE"
    assert client.is_logged_in() is True
    request = [item for item in server.requests if urlparse(item.url).path == "/login"][-1]
    assert request.method == "POST"
    assert request.data == {"username": "u", "password": "p"}


def test_login_requires_both_fields() -> None:
    client, _ = make_client({"/setting": {}})

    with pytest.raises(InvalidArgument):
        client.login("", "p")
    with pytest.raises(InvalidArgument):
        client.login("u", "")


def test_logout_clears_state() -> None:
    profile = {"uid": "9", "username": "u", "s": "AVS-VALUE"}
    client, _ = make_client({"/setting": {}, "/login": profile})
    client.login("u", "p")

    client.logout()

    assert client.is_logged_in() is False
    assert client.cached_account is None


def test_account_uses_post_profile() -> None:
    client, server = make_client({"/setting": {}, "/login": {"uid": "1", "username": "u"}})

    account = client.account()

    assert account.username == "u"
    request = [item for item in server.requests if urlparse(item.url).path == "/login"][-1]
    assert request.method == "POST"
    assert request.data is None


def test_setting_version_is_adopted(load_fixture: Callable[[str], Any]) -> None:
    routes = base_routes(load_fixture)
    routes["/setting"] = {"jm3_version": "9.9.9"}
    client, server = make_client(routes)

    client.search("x")
    client.search("y")

    versions = [str(item.headers["tokenparam"]).split(",")[1] for item in server.requests]  # type: ignore[index]
    assert versions[0] == "2.1.9", "取版本号的那次请求本身用的还是旧版本"
    assert set(versions[1:]) == {"9.9.9"}, "拿到新版本后，后续请求都应带上它"
    assert client.version == "9.9.9"


def test_version_auto_update_can_be_disabled(load_fixture: Callable[[str], Any]) -> None:
    routes = base_routes(load_fixture)
    routes["/setting"] = {"jm3_version": "9.9.9"}
    client, _ = make_client(routes, auto_update_mobile_version=False, mobile_version="2.1.9")

    client.search("x")

    assert client.version == "2.1.9"


def test_existing_cookies_skip_setting(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client(base_routes(load_fixture))
    client.set_cookies({"AVS": "already"})

    client.search("x")

    assert server.count("/setting") == 0
    assert client.is_logged_in() is True


def test_setting_failure_is_not_fatal(load_fixture: Callable[[str], Any]) -> None:
    routes = base_routes(load_fixture)
    del routes["/setting"]
    client, server = make_client(routes)

    listing = client.search("x")

    assert listing.items
    assert server.count("/setting") == 1


@pytest.mark.parametrize(
    ("code", "expected"),
    [(403, AuthRejected), (401, AuthRejected), (500, ApiRejected)],
)
def test_api_error_code_maps_to_exception(code: int, expected: type[Exception]) -> None:
    client, _ = make_client({"/setting": {}, "/album": {"code": code, "error_msg": "接口报错"}})

    with pytest.raises(expected):
        client.get_book(1)


def test_non_json_response_is_retried_then_reported(load_fixture: Callable[[str], Any]) -> None:
    client, server = make_client({"/setting": {}, "/album": "<html>服务异常</html>"})

    with pytest.raises(RequestFailed):
        client.get_book(1)

    assert server.count("/album") >= 1


def test_invalid_book_id_raises_invalid_argument() -> None:
    with pytest.raises(InvalidArgument):
        resolve_book_id("not-an-id")


def test_refresh_endpoints_ignores_remote_when_disabled(load_fixture: Callable[[str], Any]) -> None:
    client, _ = make_client(base_routes(load_fixture))

    resolved = client.refresh_endpoints()

    assert resolved.mobile == ENDPOINTS
    assert client.endpoints == ENDPOINTS
    assert client.cdn_endpoint == CDN


async def test_async_client_matches_sync_behaviour(load_fixture: Callable[[str], Any]) -> None:
    resolved = settings()
    server = AsyncFakeServer(base_routes(load_fixture))
    session = AsyncHttpSession(resolved, backend=server, default_endpoints=ENDPOINTS, sleeper=_noop_sleep)
    client = AsyncMobileClient(resolved, session=session)

    listing = await client.search("MANA")
    chapter = await client.get_chapter(1114751)
    feed = await client.get_comments(1472715)

    assert listing.items
    assert chapter.scramble_id == 268850
    assert any(comment.replies for comment in feed.items)
    await client.close()


async def test_async_client_login_and_probe() -> None:
    resolved = settings()
    server = AsyncFakeServer({"/setting": {"jm3_version": "2.1.9"}, "/login": {"uid": "1", "s": "AVS"}})
    session = AsyncHttpSession(resolved, backend=server, default_endpoints=ENDPOINTS, sleeper=_noop_sleep)

    async with AsyncMobileClient(resolved, session=session) as client:
        account = await client.login("u", "p")

        assert account.uid == "1"
        assert client.is_logged_in() is True


async def _noop_sleep(delay: float) -> None:
    return None


def test_payload_tolerates_unstable_field_types(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_search.json")
    payload = dict(payload, content=[dict(payload["content"][0], id=12345, update_at="1700000000")])
    client, _ = make_client({"/setting": {}, "/search": payload})

    listing = client.search("x")

    assert listing.items[0].book_id == 12345
    assert listing.items[0].updated_at == 1700000000


def test_decrypted_payload_must_be_object() -> None:
    client, _ = make_client({"/setting": {}, "/album": [1, 2, 3]})

    with pytest.raises(ParseFailed):
        client.get_book(1)
