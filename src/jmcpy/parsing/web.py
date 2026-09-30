"""网页端页面的解析。

页面结构（已按线上页面校准）：

.. code-block:: html

    <a href="/album/1472715/mana-...">          <!-- 封面链接，车号在这里 -->
      <img data-original=".../1472715_3x4.jpg?v=..." title="…">
    </a>
    <div class="label-loveicon"><span id="albim_likes_1472715" class="text-white">…</span></div>
    <div class="label-category">…</div>
    <div class="label-sub">…</div>
    <span class="video-title title-truncate m-t-5">标题</span>
    <div class="title-truncate"><a href="/search/photos?...&amp;main_tag=2">作者</a></div>
    <div class="title-truncate tags p-b-5"><a class="tag" href="...">标签</a>…</div>
    <span class="search-pagination-total">共255部</span>

要点：

* 车号来自指向 ``/album/{id}`` 的链接。列表页里这种链接的文字是空的（图片在链接里），
  所以**不能**从链接文字取标题；
* 标题在紧随其后的 ``class`` 含 ``video-title`` 的元素里；
* 作者是 URL 带 ``main_tag=2`` 的搜索链接；标签是 ``class`` 含 ``tag`` 的链接；
* 总数在 ``class`` 含 ``search-pagination-total`` 的元素里（形如「共255部」），
  分类页没有这个元素，此时总数为 ``None``；
* 搜索词非法（例如过短）时页面只有 ``<fieldset>`` 里的错误提示、没有卡片。

解析按元素逐个收集，不依赖整页正则，页面增删无关字段也不会整体失效。
需要作者、标签等完整字段时用移动端接口（:class:`jmcpy.clients.mobile.MobileClient`）。
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
_AUTHOR_HREF = re.compile(r"[?&]main_tag=2(?:&|$)")
_OG_URL_TAG = re.compile(r"<meta[^>]*og:url[^>]*>", re.I)
_TITLE_TAG = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_NUMBER = re.compile(r"\d[\d,]*")

#: ``class`` 含这些片段时整段取文本，并归到对应字段
_CAPTURE_MODES = (("video-title", "title"), ("search-pagination-total", "total"))

#: 错误提示：``<fieldset>`` 里出现这些字样就认为这一页没有结果
_ERROR_WORDS = ("错误", "錯誤", "error")

#: 页码：分页控件里处于 active 状态的那一项
_PAGINATION = (
    r'(?:pagination|pager)[\s\S]{0,600}?class=["\'][^"\']*active[^"\']*["\'][\s\S]{0,200}?(\d+)',
    r'class=["\'][^"\']*active[^"\']*["\'][\s\S]{0,200}?data-page=["\'](\d+)',
)

#: 总数兜底：任何文本里出现「共 255」都能识别
_TOTAL_TEXT = re.compile(r"共\s*(\d[\d,]*)")


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


@dataclass
class _Card:
    """一张结果卡片上我们关心的字段。"""

    book_id: int
    title: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)

    def to_brief(self) -> BookBrief:
        return BookBrief(book_id=self.book_id, title=self.title, author=self.author)


class _ListingParser(HTMLParser):
    """按元素收集卡片、总数与错误提示。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: dict[int, _Card] = {}
        self.order: list[int] = []
        self.total: int | None = None
        self.error: str | None = None

        self._last_card: int | None = None
        self._capture_tag: str | None = None
        self._capture_mode: str | None = None
        self._buffer: list[str] = []

    # ------------------------------------------------------------------ 元素
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag == "a":
            self._handle_anchor(attributes)
            return
        if tag == "fieldset":
            self._begin_capture(tag, "fieldset")
            return
        self._begin_capture(tag, _mode_for(attributes.get("class", "")))

    def handle_endtag(self, tag: str) -> None:
        if self._capture_tag is not None and tag == self._capture_tag:
            self._finish_capture()

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._capture_mode is not None:
            self._buffer.append(text)
            return
        if self.total is None:
            match = _TOTAL_TEXT.search(text)
            if match:
                self.total = as_int(match.group(1).replace(",", ""))

    # ------------------------------------------------------------------ 内部
    def _handle_anchor(self, attributes: Mapping[str, str]) -> None:
        href = attributes.get("href", "")
        album = _ALBUM_HREF.search(href)
        if album:
            book_id = int(album.group(1))
            if book_id not in self.cards:
                self.cards[book_id] = _Card(book_id=book_id)
                self.order.append(book_id)
            self._last_card = book_id
            # 少数版式把标题放在链接的 title 属性上，有就先用上
            title = attributes.get("title", "").strip()
            if title and not self.cards[book_id].title:
                self.cards[book_id].title = title
            return

        if _AUTHOR_HREF.search(href):
            self._begin_capture("a", "author")
            return

        if "tag" in attributes.get("class", "").split():
            self._begin_capture("a", "tag")

    def _begin_capture(self, tag: str, mode: str | None) -> None:
        if mode is None or self._capture_mode is not None:
            return
        self._capture_tag = tag
        self._capture_mode = mode
        self._buffer = []

    def _finish_capture(self) -> None:
        mode = self._capture_mode
        text = " ".join(self._buffer).strip()
        self._capture_tag = None
        self._capture_mode = None
        self._buffer = []

        card = self.cards.get(self._last_card) if self._last_card is not None else None
        if mode == "title" and card is not None and not card.title:
            card.title = text
        elif mode == "author" and card is not None and not card.author:
            card.author = text
        elif mode == "tag" and card is not None and text:
            card.tags.append(text)
        elif mode == "total" and self.total is None:
            match = _NUMBER.search(text)
            if match:
                self.total = as_int(match.group(0).replace(",", ""))
        elif mode == "fieldset" and text and any(word in text.lower() for word in _ERROR_WORDS):
            self.error = text

    def finish(self) -> list[int]:
        """收尾并返回真正属于结果列表的车号。

        列表页侧栏里也会出现指向本子的链接（例如推荐位），它们既没有标题也没有标签；
        结果卡片的判据就是「取到了标题或标签」。若整页都没取到（未知版式），
        则退回全部链接并用车号兜底，至少保证车号可用；识别出错误提示时除外。
        """
        cards = [card for card in self.cards.values() if card.title or card.tags]
        if not cards and self.error is None:
            cards = list(self.cards.values())
            for card in cards:
                card.title = f"JM{card.book_id}"
        return [card.book_id for card in cards]


def _mode_for(classes: str) -> str | None:
    for keyword, mode in _CAPTURE_MODES:
        if keyword in classes:
            return mode
    return None


def parse_listing_page(html: str, *, page: int = 1, page_size: int = 80) -> WebParseResult:
    """解析搜索页或分类页。"""
    parser = _ListingParser()
    parser.feed(html)
    parser.close()
    book_ids = parser.finish()

    items = tuple(parser.cards[book_id].to_brief() for book_id in book_ids)
    return WebParseResult(
        items=items,
        total=parser.total,
        page=parse_current_page(html) or page,
        error=parser.error,
        raw={"html_length": len(html)},
    )


def parse_album_page_identity(html: str) -> tuple[int, str] | None:
    """从本子详情页取出 ``(车号, 标题)``，用于搜索被重定向到单个本子的情况。"""
    candidate = html
    tag = _OG_URL_TAG.search(html)
    if tag:
        candidate = tag.group(0)
    match = _ALBUM_HREF.search(candidate) or _ALBUM_HREF.search(html)
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
    for pattern in _PAGINATION:
        match = re.search(pattern, html, re.I)
        if match:
            return as_int(match.group(1))
    return None


def require_usable_page(result: WebParseResult, url: str) -> WebParseResult:
    """把「页面看起来是错误页」的情况转成异常。"""
    if result.error:
        raise ParseFailed(f"网页端返回错误提示: {result.error}", url=url)
    return result
