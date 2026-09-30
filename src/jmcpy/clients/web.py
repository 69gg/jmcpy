"""网页端客户端。

定位：补齐移动端接口不支持的能力——分类下的**副分类**搜索与浏览。
移动端接口的 ``/search`` 只认大分类（``c``），副分类参数一律被忽略，因此
``sub_genre`` 只能走网页端。

网页端在本机网络环境下会被反爬验证页拦截（见 docs/troubleshooting.md），
解析按页面通用结构编写、未经线上校准，拿不到完整字段属预期；需要完整字段时
请用移动端客户端。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..constants import (
    DEFAULT_BROWSER_USER_AGENT,
    DEFAULT_PAGE_SIZE,
    PATH_WEB_CATEGORY,
    PATH_WEB_SEARCH,
    WEB_HEADERS,
)
from ..endpoints import AsyncEndpointPool, EndpointPool, EndpointSet
from ..enums import Genre, RankingSpan, SearchTarget, SortBy, SubGenre, TimeRange
from ..errors import ConfigurationError, ParseFailed
from ..models import BookBrief, Listing
from ..parsing import (
    parse_album_page_identity,
    parse_listing_page,
    require_usable_page,
)
from ..settings import Settings
from ..transport import AsyncHttpSession, HttpSession, Reply

logger = logging.getLogger(__name__)

__all__ = ["AsyncWebClient", "WebClient"]


class _WebCore:
    """同步/异步共用的 URL 组装与解析。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings

    def headers(self) -> dict[str, str]:
        headers = dict(WEB_HEADERS)
        headers["user-agent"] = self._settings.user_agent or DEFAULT_BROWSER_USER_AGENT
        return headers

    @staticmethod
    def build_path(base: str, genre: Genre, sub_genre: SubGenre | None) -> str:
        """拼出带分类与副分类的路径。

        ``/search/photos`` 或 ``/search/photos/doujin`` 或 ``/search/photos/doujin/sub/CG``。
        """
        if genre is Genre.ALL:
            if sub_genre is not None:
                raise ConfigurationError("副分类必须配合大分类一起使用（例如 category=Genre.DOUJIN）")
            return base
        if sub_genre is None:
            return f"{base}/{genre}"
        return f"{base}/{genre}/sub/{sub_genre}"

    @staticmethod
    def search_params(
        query: str,
        page: int,
        target: SearchTarget,
        sort: SortBy,
        time_range: TimeRange,
    ) -> dict[str, Any]:
        return {
            "main_tag": int(target),
            "search_query": query,
            "page": page,
            "o": str(sort),
            "t": str(time_range),
        }

    @staticmethod
    def browse_params(page: int, sort: SortBy, time_range: TimeRange) -> dict[str, Any]:
        return {"page": page, "o": str(sort), "t": str(time_range)}

    def listing_from(self, reply: Reply, *, page: int) -> Listing[BookBrief]:
        """解析列表页；若被重定向到本子详情页则返回单条结果。"""
        if reply.redirected and "/album/" in reply.url:
            identity = parse_album_page_identity(reply.text)
            if identity is None:
                raise ParseFailed("搜索被重定向到本子页，但解析不出车号", url=reply.url)
            book_id, title = identity
            return Listing(items=(BookBrief(book_id=book_id, title=title),), total=1, page=page)

        result = require_usable_page(parse_listing_page(reply.text, page=page), reply.url)
        return result.to_listing(page=page, page_size=DEFAULT_PAGE_SIZE)

    def _apply_endpoints(self, endpoints: EndpointSet, session: HttpSession | AsyncHttpSession) -> None:
        if endpoints.web:
            session.set_endpoints(endpoints.web)


class WebClient(_WebCore):
    """网页端客户端（同步）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session: HttpSession | None = None,
    ) -> None:
        resolved = settings if settings is not None else Settings()
        super().__init__(resolved)
        self._session = (
            session if session is not None else HttpSession(resolved, default_endpoints=resolved.web_endpoints)
        )
        self._pool = EndpointPool(resolved, fetch=self._fetch_text)

    # ------------------------------------------------------------------ 基础
    @property
    def session(self) -> HttpSession:
        return self._session

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._session.endpoints

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        """注入 Cookie（例如浏览器里已通过验证的凭据）。"""
        self._session.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._session.get_cookies()

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> WebClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def refresh_endpoints(self, *, refresh: bool = True) -> EndpointSet:
        """刷新网页端可用域名（来自发布页）并应用。"""
        endpoints = self._pool.resolve(refresh=refresh)
        self._apply_endpoints(endpoints, self._session)
        if not self._session.endpoints:
            logger.warning(
                "没有可用的网页端域名：请配置 web_endpoints，或在能访问网页端的网络环境下调用 refresh_endpoints()"
            )
        return endpoints

    # ------------------------------------------------------------------ 内部
    def _fetch_text(self, url: str) -> str:
        return self._session.send("GET", url, timeout=self._settings.timeout).text

    def _require_endpoints(self) -> None:
        """网页端域名需要运行时发现；一个都没有时给出可操作的提示。"""
        if self._session.endpoints:
            return
        self.refresh_endpoints()
        if not self._session.endpoints:
            raise ConfigurationError(
                "没有可用的网页端域名：请通过 web_endpoints 显式配置，"
                "或在能访问网页端的网络环境下重试（发布页在本机会被反爬验证页拦截）"
            )

    def _get(self, path: str, *, params: Mapping[str, Any], page: int) -> Reply:
        self._require_endpoints()
        return self._session.send(
            "GET",
            path,
            params=params,
            headers=self.headers(),
            timeout=self._settings.timeout,
            allow_redirects=True,
        )

    # ------------------------------------------------------------------ 接口
    def search(
        self,
        query: str,
        *,
        page: int = 1,
        target: SearchTarget = SearchTarget.SITE,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
    ) -> Listing[BookBrief]:
        """网页端搜索；支持移动端不支持的副分类。"""
        path = self.build_path(PATH_WEB_SEARCH, genre, sub_genre)
        params = self.search_params(query, page, target, sort, time_range)
        return self.listing_from(self._get(path, params=params, page=page), page=page)

    def browse(
        self,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
    ) -> Listing[BookBrief]:
        """按分类（可带副分类）浏览。"""
        path = self.build_path(PATH_WEB_CATEGORY, genre, sub_genre)
        params = self.browse_params(page, sort, time_range)
        return self.listing_from(self._get(path, params=params, page=page), page=page)

    def ranking(
        self,
        span: RankingSpan = RankingSpan.WEEK,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
    ) -> Listing[BookBrief]:
        """排行榜：分类浏览 + 时间跨度 + 按观看数排序。"""
        return self.browse(
            page=page,
            genre=genre,
            sub_genre=sub_genre,
            sort=SortBy.VIEWS,
            time_range=TimeRange(span.value),
        )


class AsyncWebClient(_WebCore):
    """网页端客户端（异步）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session: AsyncHttpSession | None = None,
    ) -> None:
        resolved = settings if settings is not None else Settings()
        super().__init__(resolved)
        self._session = (
            session if session is not None else AsyncHttpSession(resolved, default_endpoints=resolved.web_endpoints)
        )
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

    async def close(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> AsyncWebClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def refresh_endpoints(self, *, refresh: bool = True) -> EndpointSet:
        endpoints = await self._pool.resolve(refresh=refresh)
        self._apply_endpoints(endpoints, self._session)
        if not self._session.endpoints:
            logger.warning(
                "没有可用的网页端域名：请配置 web_endpoints，或在能访问网页端的网络环境下调用 refresh_endpoints()"
            )
        return endpoints

    # ------------------------------------------------------------------ 内部
    async def _fetch_text(self, url: str) -> str:
        reply = await self._session.send("GET", url, timeout=self._settings.timeout)
        return reply.text

    async def _require_endpoints(self) -> None:
        """网页端域名需要运行时发现；一个都没有时给出可操作的提示。"""
        if self._session.endpoints:
            return
        await self.refresh_endpoints()
        if not self._session.endpoints:
            raise ConfigurationError(
                "没有可用的网页端域名：请通过 web_endpoints 显式配置，"
                "或在能访问网页端的网络环境下重试（发布页在本机会被反爬验证页拦截）"
            )

    async def _get(self, path: str, *, params: Mapping[str, Any], page: int) -> Reply:
        await self._require_endpoints()
        return await self._session.send(
            "GET",
            path,
            params=params,
            headers=self.headers(),
            timeout=self._settings.timeout,
            allow_redirects=True,
        )

    # ------------------------------------------------------------------ 接口
    async def search(
        self,
        query: str,
        *,
        page: int = 1,
        target: SearchTarget = SearchTarget.SITE,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
    ) -> Listing[BookBrief]:
        path = self.build_path(PATH_WEB_SEARCH, genre, sub_genre)
        params = self.search_params(query, page, target, sort, time_range)
        return self.listing_from(await self._get(path, params=params, page=page), page=page)

    async def browse(
        self,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
    ) -> Listing[BookBrief]:
        path = self.build_path(PATH_WEB_CATEGORY, genre, sub_genre)
        params = self.browse_params(page, sort, time_range)
        return self.listing_from(await self._get(path, params=params, page=page), page=page)

    async def ranking(
        self,
        span: RankingSpan = RankingSpan.WEEK,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
    ) -> Listing[BookBrief]:
        return await self.browse(
            page=page,
            genre=genre,
            sub_genre=sub_genre,
            sort=SortBy.VIEWS,
            time_range=TimeRange(span.value),
        )
