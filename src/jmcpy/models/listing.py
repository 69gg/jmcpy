"""分页列表模型（搜索、分类、排行共用）。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from ..constants import DEFAULT_PAGE_SIZE

__all__ = ["Listing"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Listing(Generic[T]):
    """一页结果。

    ``total`` 是结果总数（服务端偶尔不返回），``page`` 从 1 开始。
    """

    items: tuple[T, ...] = ()
    total: int | None = None
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    @property
    def page_count(self) -> int | None:
        """总页数；没有总数时为 ``None``。"""
        if self.total is None:
            return None
        if self.total == 0:
            return 0
        return max(1, -(-self.total // self.page_size))

    @property
    def has_next(self) -> bool:
        if self.page_count is None:
            return bool(self.items)
        return self.page < self.page_count

    def __str__(self) -> str:
        total = "未知" if self.total is None else str(self.total)
        return f"<Listing page={self.page} items={len(self.items)} total={total}>"
