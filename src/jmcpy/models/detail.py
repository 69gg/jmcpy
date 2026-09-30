"""详情模型：本子、章节与单张图片。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ..constants import PICTURE_SUFFIXES, PICTURE_URL_TEMPLATE, UNDECODED_SUFFIXES
from ..texts import host_of, suffix_of, without_query
from .catalog import ChapterBrief, RelatedBook, Taxonomy

__all__ = ["Book", "Chapter", "Picture"]


@dataclass(frozen=True, slots=True)
class Book:
    """本子详情。"""

    book_id: int
    title: str
    authors: tuple[str, ...] = ()
    description: str | None = None
    tags: tuple[str, ...] = ()
    works: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()
    likes: int | None = None
    views: int | None = None
    comment_count: int | None = None
    #: 章节总数（单章本子为 1）
    chapter_count: int | None = None
    chapters: tuple[ChapterBrief, ...] = ()
    related: tuple[RelatedBook, ...] = ()
    category: Taxonomy | None = None
    sub_category: Taxonomy | None = None
    liked: bool = False
    is_favorite: bool = False
    #: 上架时间（秒级时间戳）
    added_at: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def author(self) -> str:
        """作者串（多个作者用逗号连接），便于直接做文件名。"""
        return ", ".join(self.authors)

    @property
    def is_single_chapter(self) -> bool:
        """是否是单章本子（此时章节号就是本子号）。"""
        return len(self.chapters) <= 1

    def __str__(self) -> str:
        return f"[{self.book_id}] {self.title} ({len(self.chapters)} 章)"


@dataclass(frozen=True, slots=True)
class Chapter:
    """章节详情。

    ``pictures`` 是图片文件名列表（形如 ``00001.webp``），配合 :meth:`picture` /
    :meth:`picture_urls` 生成完整地址；同一章节的所有图片共用一个 ``scramble_id``。
    """

    chapter_id: int
    book_id: int
    title: str = ""
    #: 在本子中的序号，从 1 开始
    order: int = 1
    tags: tuple[str, ...] = ()
    pictures: tuple[str, ...] = ()
    scramble_id: int | None = None
    liked: bool = False
    is_favorite: bool = False
    added_at: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.pictures)

    def picture_url(self, filename: str, endpoint: str) -> str:
        """按指定 CDN 端点拼出图片地址。"""
        return PICTURE_URL_TEMPLATE.format(endpoint=endpoint, chapter_id=self.chapter_id, filename=filename)

    def picture(self, index: int, endpoint: str) -> Picture:
        """取第 ``index`` 张图片（从 1 开始）。"""
        if not 1 <= index <= len(self.pictures):
            raise IndexError(f"图片序号越界: {index} (共 {len(self.pictures)} 张)")
        filename = self.pictures[index - 1]
        return Picture(
            chapter_id=self.chapter_id,
            index=index,
            filename=filename,
            url=self.picture_url(filename, endpoint),
            scramble_id=self.scramble_id,
        )

    def picture_urls(self, endpoint: str) -> tuple[str, ...]:
        """按顺序生成该章节全部图片地址。"""
        return tuple(self.picture_url(name, endpoint) for name in self.pictures)

    def __str__(self) -> str:
        return f"[{self.chapter_id}] {self.title} ({len(self.pictures)} 张)"


@dataclass(frozen=True, slots=True)
class Picture:
    """一张图片的定位信息。"""

    chapter_id: int
    #: 从 1 开始
    index: int
    filename: str
    url: str
    scramble_id: int | None = None

    @property
    def suffix(self) -> str:
        """扩展名（含点，小写）。"""
        return suffix_of(self.filename) or suffix_of(self.url)

    @property
    def stem(self) -> str:
        """不含扩展名的文件名。"""
        name = without_query(self.filename).rsplit("/", 1)[-1]
        dot = name.rfind(".")
        return name if dot <= 0 else name[:dot]

    @property
    def is_animated(self) -> bool:
        """动图不做分块解扰。"""
        return self.suffix in UNDECODED_SUFFIXES

    @property
    def is_supported(self) -> bool:
        """是否是已知的图片格式。"""
        return self.suffix in PICTURE_SUFFIXES

    def with_endpoint(self, endpoint: str) -> Picture:
        """换一个 CDN 端点，返回新的图片定位（用于端点切换重试）。"""
        host = host_of(self.url)
        if not host:
            return replace(
                self,
                url=PICTURE_URL_TEMPLATE.format(endpoint=endpoint, chapter_id=self.chapter_id, filename=self.filename),
            )
        return replace(self, url=self.url.replace(host, endpoint, 1))

    def __str__(self) -> str:
        return f"{self.chapter_id}/{self.filename} #{self.index}"
