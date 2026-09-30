"""端点池（多源）。

可用端点来自四个地方，按优先级合并：

1. 内置默认值（:mod:`jmcpy.constants`）
2. 端点源：服务端下发的 JSON，解密后含 ``Server`` / ``jm3_Server`` 两个线路表
3. 网页发布页：页面里列出的可用域名
4. 用户配置（显式传入的端点池）

远端结果会带时间戳缓存到缓存目录，默认 12 小时内不重复拉取。任何一步失败都只是
降级（记一条 warning 后继续用下一优先级），不会让请求失败——端点更新本身是尽力而为。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .constants import ENDPOINT_FEED_URLS, PUBLISH_PAGE_URL
from .crypto import open_endpoint_feed
from .errors import JmcpyError
from .settings import Settings

__all__ = [
    "AsyncEndpointPool",
    "EndpointPool",
    "EndpointSet",
    "apply_to_settings",
    "is_domain_like",
    "parse_endpoint_feed",
    "parse_publish_page",
    "read_cache",
    "write_cache",
]

logger = logging.getLogger(__name__)

#: 获取远端文本的函数（同步 / 异步）
Fetcher = Callable[[str], str]
AsyncFetcher = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class EndpointSet:
    """一组可用端点。"""

    mobile: tuple[str, ...]
    cdn: tuple[str, ...]
    web: tuple[str, ...] = ()
    updated_at: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at,
            "mobile": list(self.mobile),
            "cdn": list(self.cdn),
            "web": list(self.web),
        }

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> EndpointSet:
        return cls(
            mobile=_as_tuple(document.get("mobile")),
            cdn=_as_tuple(document.get("cdn")),
            web=_as_tuple(document.get("web")),
            updated_at=float(document.get("updated_at") or 0.0),
        )


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        cleaned = item.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if cleaned and cleaned not in seen:
            seen[cleaned] = None
    return tuple(seen)


def is_domain_like(text: str) -> bool:
    """判断一段文本是否像域名（``18comio.sbs``、``www.cdngwc.cc``）。"""
    candidate = text.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if not candidate or "/" in candidate or " " in candidate:
        return False
    labels = candidate.split(".")
    if len(labels) < 2:
        return False
    if not labels[-1].isalpha() or len(labels[-1]) < 2:
        return False
    return all(label and all(char.isalnum() or char == "-" for char in label) for label in labels)


def parse_endpoint_feed(text: str) -> tuple[str, ...]:
    """解析端点源响应，返回线路表中的端点。

    ``jm3_Server`` 带线路标签且更完整（含备用线路），优先使用；缺失时退回 ``Server``。
    """
    document = json.loads(open_endpoint_feed(text))
    if not isinstance(document, dict):
        return ()

    hosts: list[str] = []
    for entry in document.get("jm3_Server") or ():
        if isinstance(entry, (list, tuple)) and entry:
            hosts.append(str(entry[0]))
        elif isinstance(entry, str):
            hosts.append(entry)
    hosts.extend(str(item) for item in (document.get("Server") or ()))
    return _dedupe(hosts)


class _PublishPageParser(HTMLParser):
    """从发布页里收集所有「像域名」的文本与链接。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[str] = []
        self._capturing = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() in {"href", "data-url", "data-domain"} and value:
                self.candidates.append(value)
        if tag in {"span", "p", "li", "td", "strong", "a", "code"}:
            self._capturing = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._capturing and tag in {"span", "p", "li", "td", "strong", "a", "code"}:
            self.candidates.append("".join(self._buffer))
            self._capturing = False
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)


def parse_publish_page(html: str) -> tuple[str, ...]:
    """解析发布页，返回页面上列出的域名。

    只按「是否像域名」筛选，不按关键字筛选——发布页上的域名未必含 ``jm`` 或 ``comic``。
    """
    parser = _PublishPageParser()
    parser.feed(html)
    parser.close()
    return _dedupe(item for item in parser.candidates if is_domain_like(item))


def read_cache(path: Path, ttl: float) -> EndpointSet | None:
    """读取缓存；不存在、过期或损坏都返回 ``None``。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:  # pragma: no cover - 依赖文件系统状态
        logger.warning("端点缓存读取失败: %s (%s)", path, exc)
        return None

    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("端点缓存内容损坏，忽略: %s", path)
        return None
    if not isinstance(document, dict):
        return None

    cached = EndpointSet.from_json(document)
    if ttl > 0 and time.time() - cached.updated_at > ttl:
        return None
    if not cached.mobile and not cached.web:
        return None
    return cached


def write_cache(path: Path, endpoints: EndpointSet) -> None:
    """原子写入缓存；失败只记 warning（缓存不是关键路径）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(endpoints.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:  # pragma: no cover - 依赖文件系统状态
        logger.warning("端点缓存写入失败: %s (%s)", path, exc)


class _PoolCore:
    """同步/异步端点池共用的合并与缓存逻辑。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def cache_path(self) -> Path:
        return self._settings.resolved_cache_dir() / "endpoints.json"

    def builtin(self) -> EndpointSet:
        """仅使用配置里的端点池。"""
        return EndpointSet(
            mobile=self._settings.mobile_endpoints,
            cdn=self._settings.cdn_endpoints,
            web=self._settings.web_endpoints,
        )

    def merge(self, discovered: EndpointSet | None, base: EndpointSet) -> EndpointSet:
        """把发现的端点合到基础池上：发现到的非空部分优先。"""
        if discovered is None:
            return base
        return EndpointSet(
            mobile=discovered.mobile or base.mobile,
            cdn=discovered.cdn or base.cdn,
            web=discovered.web or base.web,
            updated_at=discovered.updated_at,
        )

    def cached(self) -> EndpointSet | None:
        if not self._settings.auto_update_endpoints:
            return None
        return read_cache(self.cache_path, self._settings.endpoint_ttl)

    def build_from_feed(self, text: str) -> EndpointSet:
        return EndpointSet(mobile=parse_endpoint_feed(text), cdn=(), web=(), updated_at=time.time())

    def build_from_publish_page(self, html: str) -> EndpointSet:
        return EndpointSet(mobile=(), cdn=(), web=parse_publish_page(html), updated_at=time.time())


def _combine(*sets: EndpointSet) -> EndpointSet:
    """按顺序合并多个发现结果（靠后的只补空缺）。"""
    mobile: tuple[str, ...] = ()
    cdn: tuple[str, ...] = ()
    web: tuple[str, ...] = ()
    updated_at = 0.0
    for item in sets:
        mobile = mobile or item.mobile
        cdn = cdn or item.cdn
        web = web or item.web
        updated_at = max(updated_at, item.updated_at)
    return EndpointSet(mobile=mobile, cdn=cdn, web=web, updated_at=updated_at)


class EndpointPool(_PoolCore):
    """同步端点池。"""

    def __init__(self, settings: Settings, fetch: Fetcher | None = None) -> None:
        super().__init__(settings)
        self._fetch = fetch

    def resolve(self, *, refresh: bool = False) -> EndpointSet:
        """返回可用端点；``refresh=True`` 时忽略缓存强制刷新。"""
        base = self.builtin()
        if not self._settings.auto_update_endpoints or self._fetch is None:
            return base

        if not refresh:
            cached = self.cached()
            if cached is not None:
                return self.merge(cached, base)

        discovered = self._discover()
        if discovered is None:
            logger.warning("端点自动更新失败，沿用当前端点池: %s", base.mobile)
            return base

        write_cache(self.cache_path, discovered)
        return self.merge(discovered, base)

    def _discover(self) -> EndpointSet | None:
        assert self._fetch is not None
        found: list[EndpointSet] = []
        for url in ENDPOINT_FEED_URLS:
            try:
                found.append(self.build_from_feed(self._fetch(url)))
                break
            except (JmcpyError, ValueError, OSError) as exc:
                logger.warning("端点源不可用: %s (%s)", url, exc)
        try:
            found.append(self.build_from_publish_page(self._fetch(PUBLISH_PAGE_URL)))
        except (JmcpyError, ValueError, OSError) as exc:
            logger.warning("发布页不可用: %s (%s)", PUBLISH_PAGE_URL, exc)

        combined = _combine(*found)
        if not combined.mobile and not combined.web:
            return None
        return combined


class AsyncEndpointPool(_PoolCore):
    """异步端点池。"""

    def __init__(self, settings: Settings, fetch: AsyncFetcher | None = None) -> None:
        super().__init__(settings)
        self._fetch = fetch

    async def resolve(self, *, refresh: bool = False) -> EndpointSet:
        """返回可用端点；``refresh=True`` 时忽略缓存强制刷新。"""
        base = self.builtin()
        if not self._settings.auto_update_endpoints or self._fetch is None:
            return base

        if not refresh:
            cached = self.cached()
            if cached is not None:
                return self.merge(cached, base)

        discovered = await self._discover()
        if discovered is None:
            logger.warning("端点自动更新失败，沿用当前端点池: %s", base.mobile)
            return base

        write_cache(self.cache_path, discovered)
        return self.merge(discovered, base)

    async def _discover(self) -> EndpointSet | None:
        assert self._fetch is not None
        found: list[EndpointSet] = []
        for url in ENDPOINT_FEED_URLS:
            try:
                found.append(self.build_from_feed(await self._fetch(url)))
                break
            except (JmcpyError, ValueError, OSError) as exc:
                logger.warning("端点源不可用: %s (%s)", url, exc)
        try:
            found.append(self.build_from_publish_page(await self._fetch(PUBLISH_PAGE_URL)))
        except (JmcpyError, ValueError, OSError) as exc:
            logger.warning("发布页不可用: %s (%s)", PUBLISH_PAGE_URL, exc)

        combined = _combine(*found)
        if not combined.mobile and not combined.web:
            return None
        return combined


def apply_to_settings(settings: Settings, endpoints: EndpointSet) -> Settings:
    """把解析出的端点池写回配置（保留用户已显式配置的部分）。"""
    return replace(
        settings,
        mobile_endpoints=endpoints.mobile or settings.mobile_endpoints,
        cdn_endpoints=endpoints.cdn or settings.cdn_endpoints,
        web_endpoints=endpoints.web or settings.web_endpoints,
    )
