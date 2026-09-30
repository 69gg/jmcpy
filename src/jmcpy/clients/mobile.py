"""移动端接口客户端。

协议要点（与传输层配合）：

* 每次请求现取时间戳，``token = md5(ts + 盐值)``，``tokenparam = f"{ts},{版本}"``；
* ``/chapter_view_template`` 必须换用另一个盐值，否则 403；
* 响应体 ``data`` 需要用同一时间戳推导的密钥解密；
* 首次请求前先用 ``/setting`` 播种服务端下发的 Cookie（服务端要求带 Cookie），
  顺便同步最新接口版本号。

同步与异步两份实现的差别只在 I/O 与 ``await``，解析与组装逻辑共用
:class:`_MobileCore` 与 :mod:`jmcpy.parsing`。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from ..constants import (
    CHAPTER_TOKEN_SALT,
    COVER_URL_TEMPLATE,
    DEFAULT_MOBILE_USER_AGENT,
    MOBILE_HEADERS,
    MOBILE_PAYLOAD_SALT,
    MOBILE_TOKEN_SALT,
    PATH_BOOK,
    PATH_CATEGORY,
    PATH_CHAPTER,
    PATH_CHAPTER_TOKEN,
    PATH_COMMENTS,
    PATH_LOGIN,
    PATH_PROFILE,
    PATH_SEARCH,
    PATH_SETTING,
    PICTURE_HEADERS,
    PICTURE_URL_TEMPLATE,
)
from ..crypto import sign_request, unseal_payload
from ..endpoints import AsyncEndpointPool, EndpointPool, EndpointSet
from ..enums import Genre, RankingSpan, SearchTarget, SortBy, TimeRange
from ..errors import ApiRejected, AuthRejected, InvalidArgument, JmcpyError, NotFound, ResponseInvalid
from ..models import Account, Book, BookBrief, Chapter, CommentFeed, Listing, Picture
from ..parsing import (
    parse_account,
    parse_book_detail,
    parse_book_listing,
    parse_chapter_detail,
    parse_comment_feed,
    parse_redirect_book_id,
    parse_scramble_id,
    require_payload_json,
)
from ..settings import Settings
from ..texts import normalize_book_id
from ..transport import AsyncHttpSession, HttpSession, Reply
from ..values import as_int, as_optional_str, as_str

logger = logging.getLogger(__name__)

__all__ = ["AsyncMobileClient", "MobileClient"]


def _require_json_object(reply: Reply) -> None:
    """移动端接口的响应必须是 JSON 对象；否则视为服务端临时异常，可重试。"""
    text = reply.text.lstrip()
    if not text.startswith("{"):
        raise ResponseInvalid("移动端接口返回的不是 JSON，可能是服务端临时异常", url=reply.url, snippet=text)


def resolve_book_id(value: int | str) -> int:
    """把车号规范成整数，非法输入抛 :class:`~jmcpy.errors.InvalidArgument`。"""
    return int(normalize_book_id(value))


def _decode_reply(reply: Reply, timestamp: int) -> Mapping[str, Any]:
    """校验 code 并解密 data 字段。"""
    document = reply.json()
    code = as_int(document.get("code"), 0)
    if code != 200:
        message = as_str(document.get("error_msg") or document.get("msg") or "").strip() or "接口返回失败"
        if code in {401, 403}:
            raise AuthRejected(f"{message}（需要登录或会话已失效）")
        if code == 404:
            raise NotFound(message, code=code, url=reply.url)
        raise ApiRejected(message, code=code, url=reply.url)

    data = document.get("data")
    if not isinstance(data, str) or not data:
        raise ResponseInvalid("响应缺少 data 字段", url=reply.url, snippet=reply.text)
    return require_payload_json(unseal_payload(data, timestamp, MOBILE_PAYLOAD_SALT))


class _MobileCore:
    """同步/异步客户端共用的无 I/O 逻辑。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._version = settings.mobile_version
        self._cdn_endpoint: str = settings.cdn_endpoints[0]
        self._scramble_cache: dict[int, int] = {}
        self._account: Account | None = None
        self._prepared = False

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def version(self) -> str:
        """当前使用的接口版本（可能被 ``/setting`` 更新过）。"""
        return self._version

    @property
    def cdn_endpoint(self) -> str:
        """当前使用的图片 CDN 端点。"""
        return self._cdn_endpoint

    def cover_url(self, book_id: int | str, *, size: str = "") -> str:
        """按当前 CDN 端点生成封面地址。"""
        return COVER_URL_TEMPLATE.format(endpoint=self._cdn_endpoint, book_id=resolve_book_id(book_id), size=size)

    # ------------------------------------------------------------------ 组装
    def _headers(self, salt: str, timestamp: int) -> dict[str, str]:
        token, tokenparam = sign_request(timestamp, self._version, salt)
        headers = dict(MOBILE_HEADERS)
        headers["token"] = token
        headers["tokenparam"] = tokenparam
        headers["user-agent"] = self._settings.user_agent or DEFAULT_MOBILE_USER_AGENT
        return headers

    @staticmethod
    def _search_params(
        query: str,
        page: int,
        target: SearchTarget,
        sort: SortBy,
        time_range: TimeRange,
        genre: Genre,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "main_tag": int(target),
            "search_query": query,
            "page": page,
            "o": str(sort),
            "t": str(time_range),
        }
        # 全部类别时不下发 c，避免被当成「分类名为 0」查询
        if genre is not Genre.ALL:
            params["c"] = str(genre)
        return params

    @staticmethod
    def _browse_params(page: int, genre: Genre, sort: SortBy, time_range: TimeRange) -> dict[str, Any]:
        # 分类接口把「排序 + 时间范围」合并成一个参数，例如 mv_w
        order = str(sort) if time_range is TimeRange.ALL else f"{sort}_{time_range}"
        return {"page": page, "order": "", "c": str(genre), "o": order}

    def _apply_endpoints(self, endpoints: EndpointSet, session: HttpSession | AsyncHttpSession) -> None:
        if endpoints.mobile:
            session.set_endpoints(endpoints.mobile)
        if endpoints.cdn:
            self._cdn_endpoint = endpoints.cdn[0]

    def _adopt_version(self, payload: Mapping[str, Any]) -> None:
        if not self._settings.auto_update_mobile_version:
            return
        version = as_optional_str(payload.get("jm3_version"))
        if version and version != self._version:
            logger.info("接口版本从 %s 更新为 %s", self._version, version)
            self._version = version

    def _picture_endpoints(self) -> tuple[str, ...]:
        """把当前 CDN 端点排在首位，其余作为切换备选。"""
        current = (self._cdn_endpoint,) if self._cdn_endpoint else ()
        return current + tuple(item for item in self._settings.cdn_endpoints if item != self._cdn_endpoint)

    def _picture_headers(self) -> dict[str, str]:
        headers = dict(PICTURE_HEADERS)
        headers["user-agent"] = self._settings.user_agent or DEFAULT_MOBILE_USER_AGENT
        return headers

    def _listing(self, payload: Mapping[str, Any], page: int) -> Listing[BookBrief]:
        return parse_book_listing(payload, page=page, cdn_endpoint=self._cdn_endpoint)

    def _single_book_listing(self, book: Book, page: int) -> Listing[BookBrief]:
        brief = BookBrief(
            book_id=book.book_id,
            title=book.title,
            author=book.author,
            description=book.description,
            cover_url=self.cover_url(book.book_id),
            category=book.category,
            sub_category=book.sub_category,
            liked=book.liked,
            is_favorite=book.is_favorite,
            updated_at=book.added_at,
        )
        return Listing(items=(brief,), total=1, page=page)


class MobileClient(_MobileCore):
    """移动端接口客户端（同步）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session: HttpSession | None = None,
    ) -> None:
        resolved = settings if settings is not None else Settings()
        super().__init__(resolved)
        self._session = session if session is not None else HttpSession(resolved)
        self._pool = EndpointPool(resolved, fetch=self._fetch_text)

    # ------------------------------------------------------------------ 基础
    @property
    def session(self) -> HttpSession:
        return self._session

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._session.endpoints

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        """注入 Cookie（会话恢复用）。"""
        self._session.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._session.get_cookies()

    def is_logged_in(self) -> bool:
        """是否持有登录凭证（AVS）。"""
        return bool(self._session.get_cookies().get("AVS"))

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> MobileClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def refresh_endpoints(self, *, refresh: bool = True) -> EndpointSet:
        """刷新端点池并应用；``refresh=False`` 时优先用缓存。"""
        endpoints = self._pool.resolve(refresh=refresh)
        self._apply_endpoints(endpoints, self._session)
        self._prepared = True
        return endpoints

    # ------------------------------------------------------------------ 内部
    def _fetch_text(self, url: str) -> str:
        return self._session.send("GET", url, timeout=self._settings.timeout).text

    def _send(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        method: str = "GET",
        salt: str = MOBILE_TOKEN_SALT,
        validate_json: bool = True,
    ) -> tuple[Mapping[str, Any], int]:
        """发一次请求并解密，返回 ``(payload, 时间戳)``。"""
        timestamp = int(time.time())
        reply = self._session.send(
            method,
            path,
            params=params,
            data=data,
            headers=self._headers(salt, timestamp),
            validator=_require_json_object if validate_json else None,
        )
        return _decode_reply(reply, timestamp), timestamp

    def _api(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        method: str = "GET",
    ) -> Mapping[str, Any]:
        """对外方法统一入口：确保初始化后发请求。"""
        self._prepare()
        return self._send(path, params=params, data=data, method=method)[0]

    def _prepare(self) -> None:
        """解析端点并用 ``/setting`` 播种 Cookie；只执行一次。"""
        if self._prepared:
            return
        self._prepared = True

        endpoints = self._pool.resolve()
        self._apply_endpoints(endpoints, self._session)

        if self._session.get_cookies():
            return
        try:
            payload, _ = self._send(PATH_SETTING)
        except JmcpyError as exc:
            logger.warning("初始化会话失败（将继续尝试携带空 Cookie 请求）: %s", exc)
            return
        self._adopt_version(payload)

    # ------------------------------------------------------------------ 登录
    def login(self, username: str, password: str) -> Account:
        """账号密码登录；成功后把 AVS 写入 Cookie。"""
        if not username or not password:
            raise InvalidArgument("用户名与密码不能为空")
        self._prepare()
        payload, _ = self._send(PATH_LOGIN, data={"username": username, "password": password}, method="POST")
        return self._store_account(payload)

    def account(self) -> Account:
        """获取当前登录用户资料。"""
        self._prepare()
        payload, _ = self._send(PATH_PROFILE, method="POST")
        return self._store_account(payload)

    def logout(self) -> None:
        """清除本地登录态。"""
        self._account = None
        self._session.set_cookies({"AVS": ""})

    def _store_account(self, payload: Mapping[str, Any]) -> Account:
        avs = as_optional_str(payload.get("s"))
        if avs:
            self._session.set_cookies({"AVS": avs})
        self._account = parse_account(payload, cdn_endpoint=self._cdn_endpoint)
        return self._account

    @property
    def cached_account(self) -> Account | None:
        """最近一次登录/查询得到的资料（不再发请求）。"""
        return self._account

    # ------------------------------------------------------------------ 搜索
    def search(
        self,
        query: str,
        *,
        page: int = 1,
        target: SearchTarget = SearchTarget.SITE,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
        genre: Genre = Genre.ALL,
    ) -> Listing[BookBrief]:
        """站内搜索。

        ``target`` 决定搜索维度（站内/作品/作者/标签/登场角色），
        ``genre`` 限定大分类；副分类只有网页端支持。
        """
        params = self._search_params(query, page, target, sort, time_range, genre)
        payload = self._api(PATH_SEARCH, params=params)
        redirect = parse_redirect_book_id(payload)
        if redirect is not None:
            return self._single_book_listing(self.get_book(redirect), page)
        return self._listing(payload, page)

    def browse(
        self,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
    ) -> Listing[BookBrief]:
        """按分类浏览（带排序与时间范围）。"""
        payload = self._api(PATH_CATEGORY, params=self._browse_params(page, genre, sort, time_range))
        return self._listing(payload, page)

    def ranking(
        self,
        span: RankingSpan = RankingSpan.WEEK,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
    ) -> Listing[BookBrief]:
        """排行榜：日/周/月 = 分类浏览 + 时间跨度 + 按观看数排序。"""
        return self.browse(page=page, genre=genre, sort=SortBy.VIEWS, time_range=TimeRange(span.value))

    # ------------------------------------------------------------------ 详情
    def get_book(self, book_id: int | str) -> Book:
        """本子详情（含章节列表与相关推荐）。"""
        payload = self._api(PATH_BOOK, params={"id": resolve_book_id(book_id)})
        return parse_book_detail(payload, cdn_endpoint=self._cdn_endpoint)

    def get_chapter(self, chapter_id: int | str, *, with_scramble_id: bool = True) -> Chapter:
        """章节详情（含图片文件名与解扰参数）。"""
        cid = resolve_book_id(chapter_id)
        payload = self._api(PATH_CHAPTER, params={"id": cid})
        scramble = self.get_scramble_id(cid) if with_scramble_id else None
        return parse_chapter_detail(payload, scramble_id=scramble)

    def get_scramble_id(self, chapter_id: int | str) -> int:
        """取章节的解扰参数（带进程内缓存）。"""
        cid = resolve_book_id(chapter_id)
        cached = self._scramble_cache.get(cid)
        if cached is not None:
            return cached

        self._prepare()
        timestamp = int(time.time())
        reply = self._session.send(
            "GET",
            PATH_CHAPTER_TOKEN,
            params={
                "id": cid,
                "mode": "vertical",
                "page": 0,
                "app_img_shunt": 1,
                "express": "off",
                "v": timestamp,
            },
            headers=self._headers(CHAPTER_TOKEN_SALT, timestamp),
        )
        value = parse_scramble_id(reply.text)
        self._scramble_cache[cid] = value
        return value

    # ------------------------------------------------------------------ 图片
    def picture(self, chapter: Chapter, index: int) -> Picture:
        """按章节与序号构造图片定位（使用当前 CDN 端点）。"""
        return chapter.picture(index, self._cdn_endpoint)

    def fetch_picture(self, picture: Picture) -> bytes:
        """取回图片原始字节。

        失败会在 CDN 端点之间切换重试；服务端偶发返回空响应时，带上时间戳再试一次。
        """
        self._prepare()
        template = PICTURE_URL_TEMPLATE.format(
            endpoint="{endpoint}", chapter_id=picture.chapter_id, filename=picture.filename
        )
        endpoints = self._picture_endpoints()
        data = self._fetch_picture_once(template, endpoints, None)
        if not data:
            data = self._fetch_picture_once(template, endpoints, {"v": int(time.time())})
        if not data:
            raise ResponseInvalid("图片响应为空（带时间戳重试后仍为空）", url=picture.url)
        return data

    def _fetch_picture_once(self, template: str, endpoints: tuple[str, ...], params: Mapping[str, Any] | None) -> bytes:
        reply = self._session.send(
            "GET",
            template,
            params=params,
            headers=self._picture_headers(),
            timeout=self._settings.image_timeout,
            endpoints=endpoints,
            retry_times=1,
        )
        return reply.content

    # ------------------------------------------------------------------ 评论

    def get_comments(self, book_id: int | str, *, page: int = 1) -> CommentFeed:
        """本子评论分页（含回评与剧透标记）。"""
        payload = self._api(PATH_COMMENTS, params={"mode": "all", "page": page, "aid": resolve_book_id(book_id)})
        return parse_comment_feed(payload, page=page)

    def iter_comments(
        self,
        book_id: int | str,
        *,
        start: int = 1,
        limit: int | None = None,
    ) -> Iterator[CommentFeed]:
        """逐页遍历评论。"""
        page = start
        yielded = 0
        while True:
            feed = self.get_comments(book_id, page=page)
            yield feed
            yielded += 1
            if limit is not None and yielded >= limit:
                return
            if not feed.has_next:
                return
            page += 1


class AsyncMobileClient(_MobileCore):
    """移动端接口客户端（异步）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session: AsyncHttpSession | None = None,
    ) -> None:
        resolved = settings if settings is not None else Settings()
        super().__init__(resolved)
        self._session = session if session is not None else AsyncHttpSession(resolved)
        self._pool = AsyncEndpointPool(resolved, fetch=self._fetch_text)

    # ------------------------------------------------------------------ 基础
    @property
    def session(self) -> AsyncHttpSession:
        return self._session

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._session.endpoints

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self._session.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._session.get_cookies()

    def is_logged_in(self) -> bool:
        return bool(self._session.get_cookies().get("AVS"))

    async def close(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> AsyncMobileClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def refresh_endpoints(self, *, refresh: bool = True) -> EndpointSet:
        endpoints = await self._pool.resolve(refresh=refresh)
        self._apply_endpoints(endpoints, self._session)
        self._prepared = True
        return endpoints

    # ------------------------------------------------------------------ 内部
    async def _fetch_text(self, url: str) -> str:
        reply = await self._session.send("GET", url, timeout=self._settings.timeout)
        return reply.text

    async def _send(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        method: str = "GET",
        salt: str = MOBILE_TOKEN_SALT,
        validate_json: bool = True,
    ) -> tuple[Mapping[str, Any], int]:
        timestamp = int(time.time())
        reply = await self._session.send(
            method,
            path,
            params=params,
            data=data,
            headers=self._headers(salt, timestamp),
            validator=_require_json_object if validate_json else None,
        )
        return _decode_reply(reply, timestamp), timestamp

    async def _api(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        method: str = "GET",
    ) -> Mapping[str, Any]:
        await self._prepare()
        return (await self._send(path, params=params, data=data, method=method))[0]

    async def _prepare(self) -> None:
        if self._prepared:
            return
        self._prepared = True

        endpoints = await self._pool.resolve()
        self._apply_endpoints(endpoints, self._session)

        if self._session.get_cookies():
            return
        try:
            payload, _ = await self._send(PATH_SETTING)
        except JmcpyError as exc:
            logger.warning("初始化会话失败（将继续尝试携带空 Cookie 请求）: %s", exc)
            return
        self._adopt_version(payload)

    # ------------------------------------------------------------------ 登录
    async def login(self, username: str, password: str) -> Account:
        """账号密码登录；成功后把 AVS 写入 Cookie。"""
        if not username or not password:
            raise InvalidArgument("用户名与密码不能为空")
        await self._prepare()
        payload, _ = await self._send(PATH_LOGIN, data={"username": username, "password": password}, method="POST")
        return self._store_account(payload)

    async def account(self) -> Account:
        await self._prepare()
        payload, _ = await self._send(PATH_PROFILE, method="POST")
        return self._store_account(payload)

    def logout(self) -> None:
        self._account = None
        self._session.set_cookies({"AVS": ""})

    def _store_account(self, payload: Mapping[str, Any]) -> Account:
        avs = as_optional_str(payload.get("s"))
        if avs:
            self._session.set_cookies({"AVS": avs})
        self._account = parse_account(payload, cdn_endpoint=self._cdn_endpoint)
        return self._account

    @property
    def cached_account(self) -> Account | None:
        return self._account

    # ------------------------------------------------------------------ 搜索
    async def search(
        self,
        query: str,
        *,
        page: int = 1,
        target: SearchTarget = SearchTarget.SITE,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
        genre: Genre = Genre.ALL,
    ) -> Listing[BookBrief]:
        """站内搜索（语义同同步版）。"""
        params = self._search_params(query, page, target, sort, time_range, genre)
        payload = await self._api(PATH_SEARCH, params=params)
        redirect = parse_redirect_book_id(payload)
        if redirect is not None:
            return self._single_book_listing(await self.get_book(redirect), page)
        return self._listing(payload, page)

    async def browse(
        self,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
    ) -> Listing[BookBrief]:
        payload = await self._api(PATH_CATEGORY, params=self._browse_params(page, genre, sort, time_range))
        return self._listing(payload, page)

    async def ranking(
        self,
        span: RankingSpan = RankingSpan.WEEK,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
    ) -> Listing[BookBrief]:
        return await self.browse(page=page, genre=genre, sort=SortBy.VIEWS, time_range=TimeRange(span.value))

    # ------------------------------------------------------------------ 详情
    async def get_book(self, book_id: int | str) -> Book:
        payload = await self._api(PATH_BOOK, params={"id": resolve_book_id(book_id)})
        return parse_book_detail(payload, cdn_endpoint=self._cdn_endpoint)

    async def get_chapter(self, chapter_id: int | str, *, with_scramble_id: bool = True) -> Chapter:
        cid = resolve_book_id(chapter_id)
        payload = await self._api(PATH_CHAPTER, params={"id": cid})
        scramble = await self.get_scramble_id(cid) if with_scramble_id else None
        return parse_chapter_detail(payload, scramble_id=scramble)

    async def get_scramble_id(self, chapter_id: int | str) -> int:
        cid = resolve_book_id(chapter_id)
        cached = self._scramble_cache.get(cid)
        if cached is not None:
            return cached

        await self._prepare()
        timestamp = int(time.time())
        reply = await self._session.send(
            "GET",
            PATH_CHAPTER_TOKEN,
            params={
                "id": cid,
                "mode": "vertical",
                "page": 0,
                "app_img_shunt": 1,
                "express": "off",
                "v": timestamp,
            },
            headers=self._headers(CHAPTER_TOKEN_SALT, timestamp),
        )
        value = parse_scramble_id(reply.text)
        self._scramble_cache[cid] = value
        return value

    # ------------------------------------------------------------------ 图片
    def picture(self, chapter: Chapter, index: int) -> Picture:
        """按章节与序号构造图片定位（使用当前 CDN 端点）。"""
        return chapter.picture(index, self._cdn_endpoint)

    async def fetch_picture(self, picture: Picture) -> bytes:
        """取回图片原始字节（语义同同步版）。"""
        await self._prepare()
        template = PICTURE_URL_TEMPLATE.format(
            endpoint="{endpoint}", chapter_id=picture.chapter_id, filename=picture.filename
        )
        endpoints = self._picture_endpoints()
        data = await self._fetch_picture_once(template, endpoints, None)
        if not data:
            data = await self._fetch_picture_once(template, endpoints, {"v": int(time.time())})
        if not data:
            raise ResponseInvalid("图片响应为空（带时间戳重试后仍为空）", url=picture.url)
        return data

    async def _fetch_picture_once(
        self, template: str, endpoints: tuple[str, ...], params: Mapping[str, Any] | None
    ) -> bytes:
        reply = await self._session.send(
            "GET",
            template,
            params=params,
            headers=self._picture_headers(),
            timeout=self._settings.image_timeout,
            endpoints=endpoints,
            retry_times=1,
        )
        return reply.content

    # ------------------------------------------------------------------ 评论
    async def get_comments(self, book_id: int | str, *, page: int = 1) -> CommentFeed:
        payload = await self._api(PATH_COMMENTS, params={"mode": "all", "page": page, "aid": resolve_book_id(book_id)})
        return parse_comment_feed(payload, page=page)

    async def iter_comments(
        self,
        book_id: int | str,
        *,
        start: int = 1,
        limit: int | None = None,
    ) -> AsyncIterator[CommentFeed]:
        """逐页遍历评论（异步）。"""
        page = start
        yielded = 0
        while True:
            feed = await self.get_comments(book_id, page=page)
            yield feed
            yielded += 1
            if limit is not None and yielded >= limit:
                return
            if not feed.has_next:
                return
            page += 1
