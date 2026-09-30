"""门面客户端：一套接口，按能力在移动端与网页端实现之间自动路由。

默认走移动端接口（结构化 JSON、字段最全）。只有一处能力缺口需要网页端补：
**分类下的副分类**（移动端接口会忽略副分类参数）。因此当调用方传入 ``sub_genre``
且开启了 ``auto_route``（默认开启）时，搜索/浏览会自动改用网页端。

自动路由的代价是网络要求不同：网页端在部分网络环境会被反爬验证页拦截，
此时会抛出 :class:`~jmcpy.errors.ChallengeBlocked`，可以选择改用移动端能表达的
查询条件，或自行配置可用的网页端域名与浏览器 Cookie。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import replace
from typing import Any

from ..credentials import CredentialStore, LoginSession
from ..endpoints import EndpointSet
from ..enums import ExportFormat, Genre, RankingSpan, SearchTarget, SortBy, SubGenre, TimeRange
from ..errors import ConfigurationError
from ..exporting import download_chapter, download_chapter_async
from ..imaging import DEFAULT_JPEG_QUALITY, DEFAULT_PDF_DPI
from ..models import Account, Book, BookBrief, Chapter, CommentFeed, Listing
from ..models.artifacts import ChapterDownload
from ..settings import Settings, apply_overrides
from .mobile import AsyncMobileClient, MobileClient
from .web import AsyncWebClient, WebClient

logger = logging.getLogger(__name__)

__all__ = ["AsyncClient", "Client"]


def _resolve_settings(settings: Settings | Mapping[str, Any] | None) -> Settings:
    """接受 Settings、字段映射或 None。"""
    if settings is None:
        return Settings.load()
    if isinstance(settings, Settings):
        return settings
    if isinstance(settings, Mapping):
        return apply_overrides(Settings.load(), settings)
    raise ConfigurationError(f"不支持的配置类型: {type(settings).__name__}")


def _store_login(
    store: CredentialStore,
    cookies: Mapping[str, str],
    account: Account | None,
    fallback_username: str = "",
) -> LoginSession:
    """把当前凭据写成会话快照并落盘。"""
    session = LoginSession.from_account(account, cookies)
    if not session.username and fallback_username:
        session = replace(session, username=fallback_username)
    store.save(session)
    return session


class Client:
    """同步门面客户端。"""

    def __init__(
        self,
        settings: Settings | Mapping[str, Any] | None = None,
        *,
        mobile: MobileClient | None = None,
        web: WebClient | None = None,
        store: CredentialStore | None = None,
    ) -> None:
        self._settings = _resolve_settings(settings)
        self._mobile = mobile if mobile is not None else MobileClient(self._settings)
        self._web = web
        self._store = store if store is not None else CredentialStore(self._settings)
        self._session: LoginSession | None = None
        if self._settings.restore_session:
            self.restore_session()

    # ------------------------------------------------------------------ 基础
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def mobile(self) -> MobileClient:
        """移动端实现（始终可用）。"""
        return self._mobile

    @property
    def web(self) -> WebClient:
        """网页端实现；首次访问时才创建，避免多开一份连接资源。"""
        if self._web is None:
            self._web = WebClient(self._settings)
        return self._web

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._mobile.endpoints

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        """把凭据同时应用到两种实现。"""
        self._mobile.set_cookies(cookies)
        if self._web is not None:
            self._web.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._mobile.get_cookies()

    def close(self) -> None:
        self._mobile.close()
        if self._web is not None:
            self._web.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def session(self) -> LoginSession | None:
        """本地保存的登录快照（没有则为 ``None``）。"""
        return self._session

    @property
    def credential_store(self) -> CredentialStore:
        """会话文件读写器。"""
        return self._store

    def restore_session(self) -> LoginSession | None:
        """从会话文件恢复登录态并把凭据应用到两种实现。

        文件不存在、损坏或无法解密都返回 ``None``，不影响未登录使用。
        """
        session = self._store.load()
        if session is None:
            return None
        self._apply_session(session)
        return session

    def save_session(self) -> LoginSession | None:
        """把当前凭据写入会话文件；未登录时返回 ``None``。"""
        cookies = self._mobile.get_cookies()
        if not cookies.get("AVS"):
            logger.debug("当前没有登录凭据，跳过保存会话")
            return None
        self._session = _store_login(self._store, cookies, self._mobile.cached_account)
        return self._session

    def _apply_session(self, session: LoginSession) -> None:
        self._session = session
        self._mobile.set_cookies(session.cookies)
        if self._web is not None:
            self._web.set_cookies(session.cookies)

    def refresh_endpoints(self, *, refresh: bool = True) -> EndpointSet:
        """刷新移动端与网页端域名（网页端域名来自同一次发现）。"""
        endpoints = self._mobile.refresh_endpoints(refresh=refresh)
        if self._web is not None and endpoints.web:
            self._web.session.set_endpoints(endpoints.web)
        return endpoints

    # ------------------------------------------------------------------ 登录
    def login(self, username: str, password: str, *, remember: bool = True) -> Account:
        """账号密码登录（走移动端接口），把凭据同步给网页端并按需落盘。

        :param remember: 是否写入会话文件（默认写入；文件加密方式见
            :mod:`jmcpy.credentials`）
        """
        account = self._mobile.login(username, password)
        self._sync_cookies_to_web()
        if remember:
            self._session = _store_login(self._store, self._mobile.get_cookies(), account, username)
        return account

    def logout(self) -> None:
        """退出登录并清除本地凭据（包括会话文件）。"""
        self._mobile.logout()
        self._sync_cookies_to_web()
        self._session = None
        self._store.clear()

    def account(self) -> Account:
        """获取当前登录用户资料。"""
        return self._mobile.account()

    def is_logged_in(self) -> bool:
        """是否持有登录凭据。"""
        return self._mobile.is_logged_in()

    def _sync_cookies_to_web(self) -> None:
        if self._web is not None:
            self._web.set_cookies(self._mobile.get_cookies())

    # ------------------------------------------------------------------ 搜索
    def _web_or_raise(self, capability: str) -> WebClient:
        if not self._settings.auto_route:
            raise ConfigurationError(
                f"{capability}只有网页端接口支持，而当前已关闭自动路由；"
                f"请设置 auto_route=True，或改用 client.web 直接调用"
            )
        logger.debug("能力 %s 需要网页端接口，已自动路由", capability)
        return self.web

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
        """搜索；传入 ``sub_genre`` 时自动改用网页端。"""
        if sub_genre is not None:
            return self._web_or_raise("副分类搜索").search(
                query,
                page=page,
                target=target,
                sort=sort,
                time_range=time_range,
                genre=genre,
                sub_genre=sub_genre,
            )
        return self._mobile.search(
            query,
            page=page,
            target=target,
            sort=sort,
            time_range=time_range,
            genre=genre,
        )

    def browse(
        self,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
    ) -> Listing[BookBrief]:
        """分类浏览；传入 ``sub_genre`` 时自动改用网页端。"""
        if sub_genre is not None:
            return self._web_or_raise("副分类浏览").browse(
                page=page,
                genre=genre,
                sub_genre=sub_genre,
                sort=sort,
                time_range=time_range,
            )
        return self._mobile.browse(page=page, genre=genre, sort=sort, time_range=time_range)

    def ranking(
        self,
        span: RankingSpan = RankingSpan.WEEK,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
    ) -> Listing[BookBrief]:
        """排行榜（日/周/月）。"""
        if sub_genre is not None:
            return self._web_or_raise("副分类排行榜").ranking(span, page=page, genre=genre, sub_genre=sub_genre)
        return self._mobile.ranking(span, page=page, genre=genre)

    # ------------------------------------------------------------------ 详情
    def get_book(self, book_id: int | str) -> Book:
        """本子详情。"""
        return self._mobile.get_book(book_id)

    def get_chapter(self, chapter_id: int | str, *, with_scramble_id: bool = True) -> Chapter:
        """章节详情（含图片与解扰参数）。"""
        return self._mobile.get_chapter(chapter_id, with_scramble_id=with_scramble_id)

    def get_scramble_id(self, chapter_id: int | str) -> int:
        """解扰参数（带缓存）。"""
        return self._mobile.get_scramble_id(chapter_id)

    # ------------------------------------------------------------------ 评论
    def get_comments(self, book_id: int | str, *, page: int = 1) -> CommentFeed:
        """本子评论（含回评与剧透标记）。"""
        return self._mobile.get_comments(book_id, page=page)

    def iter_comments(
        self,
        book_id: int | str,
        *,
        start: int = 1,
        limit: int | None = None,
    ) -> Iterator[CommentFeed]:
        """逐页遍历评论。"""
        return self._mobile.iter_comments(book_id, start=start, limit=limit)

    # ------------------------------------------------------------------ 下载
    def fetch_picture(self, picture: Any) -> bytes:
        """取回单张图片的原始字节。"""
        return self._mobile.fetch_picture(picture)

    def download(
        self,
        chapter: Chapter | int | str,
        *,
        output: ExportFormat = ExportFormat.PATH,
        dest: str | Any | None = None,
        decode: bool = True,
        concurrency: int | None = None,
        overwrite: bool = False,
        quality: int = DEFAULT_JPEG_QUALITY,
        dpi: float = DEFAULT_PDF_DPI,
        strict: bool = False,
        on_progress: Any = None,
    ) -> ChapterDownload:
        """下载章节并交付为 bytes / base64 / 文件 / PDF。

        ``chapter`` 可以是 :class:`~jmcpy.models.Chapter`，也可以是章节车号
        （此时会先取一次章节详情）。
        """
        resolved = chapter if isinstance(chapter, Chapter) else self.get_chapter(chapter)
        return download_chapter(
            self._mobile,
            resolved,
            output=output,
            dest=dest,
            decode=decode,
            concurrency=concurrency,
            overwrite=overwrite,
            quality=quality,
            dpi=dpi,
            strict=strict,
            on_progress=on_progress,
        )

    # ------------------------------------------------------------------ 其他
    def cover_url(self, book_id: int | str, *, size: str = "") -> str:
        """封面地址。"""
        return self._mobile.cover_url(book_id, size=size)


class AsyncClient:
    """异步门面客户端。"""

    def __init__(
        self,
        settings: Settings | Mapping[str, Any] | None = None,
        *,
        mobile: AsyncMobileClient | None = None,
        web: AsyncWebClient | None = None,
        store: CredentialStore | None = None,
    ) -> None:
        self._settings = _resolve_settings(settings)
        self._mobile = mobile if mobile is not None else AsyncMobileClient(self._settings)
        self._web = web
        self._store = store if store is not None else CredentialStore(self._settings)
        self._session: LoginSession | None = None
        if self._settings.restore_session:
            self.restore_session()

    # ------------------------------------------------------------------ 基础
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def mobile(self) -> AsyncMobileClient:
        return self._mobile

    @property
    def web(self) -> AsyncWebClient:
        if self._web is None:
            self._web = AsyncWebClient(self._settings)
        return self._web

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._mobile.endpoints

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        self._mobile.set_cookies(cookies)
        if self._web is not None:
            self._web.set_cookies(cookies)

    def get_cookies(self) -> dict[str, str]:
        return self._mobile.get_cookies()

    async def close(self) -> None:
        await self._mobile.close()
        if self._web is not None:
            await self._web.close()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def session(self) -> LoginSession | None:
        """本地保存的登录快照（没有则为 ``None``）。"""
        return self._session

    @property
    def credential_store(self) -> CredentialStore:
        """会话文件读写器。"""
        return self._store

    def restore_session(self) -> LoginSession | None:
        """从会话文件恢复登录态并把凭据应用到两种实现。"""
        session = self._store.load()
        if session is None:
            return None
        self._apply_session(session)
        return session

    def save_session(self) -> LoginSession | None:
        """把当前凭据写入会话文件；未登录时返回 ``None``。"""
        cookies = self._mobile.get_cookies()
        if not cookies.get("AVS"):
            logger.debug("当前没有登录凭据，跳过保存会话")
            return None
        self._session = _store_login(self._store, cookies, self._mobile.cached_account)
        return self._session

    def _apply_session(self, session: LoginSession) -> None:
        self._session = session
        self._mobile.set_cookies(session.cookies)
        if self._web is not None:
            self._web.set_cookies(session.cookies)

    async def refresh_endpoints(self, *, refresh: bool = True) -> EndpointSet:
        endpoints = await self._mobile.refresh_endpoints(refresh=refresh)
        if self._web is not None and endpoints.web:
            self._web.session.set_endpoints(endpoints.web)
        return endpoints

    # ------------------------------------------------------------------ 登录
    async def login(self, username: str, password: str, *, remember: bool = True) -> Account:
        """账号密码登录；``remember=True`` 时写入会话文件。"""
        account = await self._mobile.login(username, password)
        self._sync_cookies_to_web()
        if remember:
            self._session = _store_login(self._store, self._mobile.get_cookies(), account, username)
        return account

    def logout(self) -> None:
        """退出登录并清除本地凭据（包括会话文件）。"""
        self._mobile.logout()
        self._sync_cookies_to_web()
        self._session = None
        self._store.clear()

    async def account(self) -> Account:
        return await self._mobile.account()

    def is_logged_in(self) -> bool:
        return self._mobile.is_logged_in()

    def _sync_cookies_to_web(self) -> None:
        if self._web is not None:
            self._web.set_cookies(self._mobile.get_cookies())

    # ------------------------------------------------------------------ 搜索
    def _web_or_raise(self, capability: str) -> AsyncWebClient:
        if not self._settings.auto_route:
            raise ConfigurationError(
                f"{capability}只有网页端接口支持，而当前已关闭自动路由；"
                f"请设置 auto_route=True，或改用 client.web 直接调用"
            )
        logger.debug("能力 %s 需要网页端接口，已自动路由", capability)
        return self.web

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
        """搜索；传入 ``sub_genre`` 时自动改用网页端。"""
        if sub_genre is not None:
            return await self._web_or_raise("副分类搜索").search(
                query,
                page=page,
                target=target,
                sort=sort,
                time_range=time_range,
                genre=genre,
                sub_genre=sub_genre,
            )
        return await self._mobile.search(
            query,
            page=page,
            target=target,
            sort=sort,
            time_range=time_range,
            genre=genre,
        )

    async def browse(
        self,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
        sort: SortBy = SortBy.LATEST,
        time_range: TimeRange = TimeRange.ALL,
    ) -> Listing[BookBrief]:
        """分类浏览；传入 ``sub_genre`` 时自动改用网页端。"""
        if sub_genre is not None:
            return await self._web_or_raise("副分类浏览").browse(
                page=page,
                genre=genre,
                sub_genre=sub_genre,
                sort=sort,
                time_range=time_range,
            )
        return await self._mobile.browse(page=page, genre=genre, sort=sort, time_range=time_range)

    async def ranking(
        self,
        span: RankingSpan = RankingSpan.WEEK,
        *,
        page: int = 1,
        genre: Genre = Genre.ALL,
        sub_genre: SubGenre | None = None,
    ) -> Listing[BookBrief]:
        """排行榜（日/周/月）。"""
        if sub_genre is not None:
            return await self._web_or_raise("副分类排行榜").ranking(span, page=page, genre=genre, sub_genre=sub_genre)
        return await self._mobile.ranking(span, page=page, genre=genre)

    # ------------------------------------------------------------------ 详情
    async def get_book(self, book_id: int | str) -> Book:
        return await self._mobile.get_book(book_id)

    async def get_chapter(self, chapter_id: int | str, *, with_scramble_id: bool = True) -> Chapter:
        return await self._mobile.get_chapter(chapter_id, with_scramble_id=with_scramble_id)

    async def get_scramble_id(self, chapter_id: int | str) -> int:
        return await self._mobile.get_scramble_id(chapter_id)

    # ------------------------------------------------------------------ 评论
    async def get_comments(self, book_id: int | str, *, page: int = 1) -> CommentFeed:
        return await self._mobile.get_comments(book_id, page=page)

    def iter_comments(
        self,
        book_id: int | str,
        *,
        start: int = 1,
        limit: int | None = None,
    ) -> AsyncIterator[CommentFeed]:
        """逐页遍历评论（返回异步迭代器）。"""
        return self._mobile.iter_comments(book_id, start=start, limit=limit)

    # ------------------------------------------------------------------ 下载
    def fetch_picture(self, picture: Any) -> Any:
        """返回取图的协程（单张图片的原始字节）。"""
        return self._mobile.fetch_picture(picture)

    async def download(
        self,
        chapter: Chapter | int | str,
        *,
        output: ExportFormat = ExportFormat.PATH,
        dest: Any | None = None,
        decode: bool = True,
        concurrency: int | None = None,
        overwrite: bool = False,
        quality: int = DEFAULT_JPEG_QUALITY,
        dpi: float = DEFAULT_PDF_DPI,
        strict: bool = False,
        on_progress: Any = None,
    ) -> ChapterDownload:
        """异步版下载；语义同同步版。"""
        resolved = chapter if isinstance(chapter, Chapter) else await self.get_chapter(chapter)
        return await download_chapter_async(
            self._mobile,
            resolved,
            output=output,
            dest=dest,
            decode=decode,
            concurrency=concurrency,
            overwrite=overwrite,
            quality=quality,
            dpi=dpi,
            strict=strict,
            on_progress=on_progress,
        )

    # ------------------------------------------------------------------ 其他
