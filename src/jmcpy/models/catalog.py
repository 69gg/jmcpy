"""列表项模型：搜索 / 分类 / 排行 / 相关推荐里出现的本子与章节条目。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["BookBrief", "ChapterBrief", "RelatedBook", "Taxonomy"]


@dataclass(frozen=True, slots=True)
class Taxonomy:
    """接口返回的分类标签（``category`` / ``category_sub``）。"""

    #: 服务端给的是字符串编号，也可能是 ``null``
    taxonomy_id: str | None = None
    title: str | None = None

    def __str__(self) -> str:
        return self.title or self.taxonomy_id or ""


@dataclass(frozen=True, slots=True)
class BookBrief:
    """列表里的本子条目。"""

    book_id: int
    title: str
    author: str = ""
    description: str | None = None
    cover_url: str | None = None
    category: Taxonomy | None = None
    sub_category: Taxonomy | None = None
    liked: bool = False
    is_favorite: bool = False
    #: 服务端给的是秒级时间戳
    updated_at: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.book_id}] {self.title}"


@dataclass(frozen=True, slots=True)
class ChapterBrief:
    """本子详情里的章节条目。"""

    chapter_id: int
    title: str
    #: 在本子中的序号，从 1 开始
    order: int = 1
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.chapter_id}] {self.title}"


@dataclass(frozen=True, slots=True)
class RelatedBook:
    """相关推荐里的本子（字段比列表项少，单独建模以免误用）。"""

    book_id: int
    title: str
    author: str = ""
    cover_url: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.book_id}] {self.title}"
