"""评论模型：评论树与评论分页。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..constants import COMMENT_PAGE_SIZE

__all__ = ["Comment", "CommentFeed"]


@dataclass(frozen=True, slots=True)
class Comment:
    """一条评论；回评挂在 :attr:`replies` 上（可多层嵌套）。"""

    comment_id: str
    #: 评论正文（已把 HTML 还原成纯文本）
    content: str = ""
    #: 被回复评论的 id；顶层评论为 None
    parent_id: str | None = None
    book_id: str | None = None
    author_id: str | None = None
    username: str | None = None
    nickname: str | None = None
    #: 是否被标记为剧透
    is_spoiler: bool = False
    likes: int | None = None
    posted_at: str | None = None
    replies: tuple[Comment, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """优先昵称，其次用户名。"""
        return self.nickname or self.username or ""

    def walk(self) -> Iterator[Comment]:
        """深度优先遍历自身与全部回评。"""
        yield self
        for reply in self.replies:
            yield from reply.walk()

    @property
    def reply_count(self) -> int:
        """全部层级的回评数量（不含自身）。"""
        return sum(1 for _ in self.walk()) - 1

    def __str__(self) -> str:
        spoiler = "（剧透）" if self.is_spoiler else ""
        return f"[{self.comment_id}] {self.display_name}{spoiler}: {self.content}"


@dataclass(frozen=True, slots=True)
class CommentFeed:
    """评论分页。

    ``total`` 是全部主评论数（网页端可能拿不到），``comment_count`` 是当前页主评论
    加所有层级回评的数量。
    """

    items: tuple[Comment, ...] = ()
    total: int | None = None
    page: int = 1
    page_size: int = COMMENT_PAGE_SIZE
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[Comment]:
        return iter(self.items)

    def __getitem__(self, index: int) -> Comment:
        return self.items[index]

    @property
    def page_count(self) -> int | None:
        """总页数；没有总数时为 ``None``。"""
        if self.total is None:
            return None
        return max(1, -(-self.total // self.page_size))

    @property
    def comment_count(self) -> int:
        """当前页主评论 + 所有层级回评。"""
        return sum(1 for comment in self.items for _ in comment.walk())

    @property
    def has_next(self) -> bool:
        if self.page_count is None:
            return bool(self.items)
        return self.page < self.page_count
