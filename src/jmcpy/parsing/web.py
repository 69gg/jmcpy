"""网页端页面的解析。

**重要说明**：网页端在本机所处的网络环境下会被反爬验证页拦截，因此这里的解析
没有经过线上校准。为了在这种前提下仍然可用，解析只依赖页面上最稳定的结构：

* 指向 ``/album/{id}`` 的链接（图片链接与标题链接都算），按出现顺序去重；
* 标题取链接的 ``title`` 属性，没有则取链接文字；
* 结果总数：数字文本紧邻「A漫」「個結果」这类文案时采用，取不到就是 ``None``；
* 页面上的「關鍵字過短」这类提示会被识别成解析错误。

代价是拿不到页面上的全部字段（例如标签、作者）；要完整字段请用移动端接口
（:class:`jmcpy.clients.mobile.MobileClient`），它的响应本身就是结构化 JSON。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from ..errors import ParseFailed
from ..models import BookBrief, Listing
from ..values import as_int

__all__ = [
    "WebParseResult",
    "parse_album_page_identity",
    "parse_current_page",
    "parse_listing_page",
    "require_usable_page",
]

_ALBUM_HREF = re.compile(r"/album/(\d+)")
_TITLE_TAG = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_OG_URL = re.compile(r"""property=["']og:url["'][^>]*content=["'][^"']*?/album/(\d+)""")
_NUMBER = re.compile(r"\d[\d,]*")

#: 页面上的错误提示，命中即认为这次搜索/浏览没有成功
_ERROR_HINTS = ("關鍵字過短", "关键字过短", "關鍵字太短", "搜尋錯誤", "搜索错误", "沒有找到")

#: 总数文案，例如「共 123 本」「123 個結果」
_TOTAL_HINTS = ("A漫", "個結果", "个结果", "本本", "筆結果", "条结果")


@dataclass
class WebParseResult:
    """一页网页的解析结果。"""

    items: tuple[BookBrief, ...] = ()
    total: int | None = None
    page: int | None = None
    #: 页面上的错误提示（如果有）
    error: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_listing(self, *, page: int, page_size: int) -> Listing[BookBrief]:
        """转成统一的分页模型。"""
        return Listing(
            items=self.items,
            total=self.total,
            page=self.page or page,
            page_size=page_size,
            raw=self.raw,
        )


class _ListingParser(HTMLParser):
    """按文档顺序收集本子链接与标题，并尽力识别总数与错误提示。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.albums: dict[int, str] = {}
        self.total: int | None = None
        self.error: str | None = None

        self._in_anchor = False
        self._anchor_book_id: int | None = None
        self._anchor_title: str | None = None
        self._anchor_text: list[str] = []
        self._last_number: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        match = _ALBUM_HREF.search(attributes.get("href", ""))
        self._in_anchor = True
        self._anchor_book_id = int(match.group(1)) if match else None
        self._anchor_title = attributes.get("title") or None
        self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            self._finish_anchor()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._in_anchor:
            self._anchor_text.append(text)
        self._scan_text(text)

    # ------------------------------------------------------------------ 内部
    def _finish_anchor(self) -> None:
        self._in_anchor = False
        book_id = self._anchor_book_id
        self._anchor_book_id = None
        if book_id is None:
            return

        title = (self._anchor_title or " ".join(self._anchor_text)).strip()
        existing = self.albums.get(book_id)
        if existing is None or (not existing and title):
            self.albums[book_id] = title

    def _scan_text(self, text: str) -> None:
        for hint in _ERROR_HINTS:
            if hint in text:
                self.error = text
                return

        numbers = _NUMBER.findall(text)
        if self.total is not None:
            return
        if any(hint in text for hint in _TOTAL_HINTS):
            candidate = numbers[-1] if numbers else None
            if candidate is None and self._last_number is not None:
                self.total = self._last_number
            elif candidate is not None:
                self.total = as_int(candidate.replace(",", ""))
            return
        if numbers and not self._in_anchor:
            self._last_number = as_int(numbers[-1].replace(",", ""))


def parse_listing_page(html: str, *, page: int = 1, page_size: int = 80) -> WebParseResult:
    """解析搜索页或分类页。"""
    parser = _ListingParser()
    parser.feed(html)
    parser.close()

    items = tuple(BookBrief(book_id=book_id, title=title) for book_id, title in parser.albums.items())
    return WebParseResult(
        items=items,
        total=parser.total,
        page=parse_current_page(html) or page,
        error=parser.error,
        raw={"html_length": len(html)},
    )


def parse_album_page_identity(html: str) -> tuple[int, str] | None:
    """从本子详情页取出 ``(车号, 标题)``，用于搜索被重定向到单个本子的情况。"""
    match = _OG_URL.search(html) or _ALBUM_HREF.search(html)
    if match is None:
        return None

    book_id = int(match.group(1))
    title = ""
    title_match = _TITLE_TAG.search(html)
    if title_match:
        title = re.split(r"[|｜\-–]", title_match.group(1))[0].strip()
    return book_id, title or f"JM{book_id}"


def parse_current_page(html: str) -> int | None:
    """页面上的当前页码（尽力而为；取不到返回 ``None``）。"""
    for pattern in (
        r"(?:pagination|pager|page)[\s\S]{0,600}?"
        r'class=["\'][^"\']*active[^"\']*["\'][\s\S]{0,200}?(\d+)',
        r'class=["\'][^"\']*active[^"\']*["\'][\s\S]{0,200}?data-page=["\'](\d+)',
    ):
        match = re.search(pattern, html, re.I)
        if match:
            return as_int(match.group(1))
    return None


def require_usable_page(result: WebParseResult, url: str) -> WebParseResult:
    """把「页面看起来是错误页」的情况转成异常。"""
    if result.error:
        raise ParseFailed(f"网页端返回错误提示: {result.error}", url=url)
    return result
