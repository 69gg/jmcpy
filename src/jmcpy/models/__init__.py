"""数据模型。

一律使用不可变 dataclass，并保留原始响应片段（``raw``）以备调用方取用未建模的字段。
字段解析的容错规则集中在 :mod:`jmcpy.values` 与 :mod:`jmcpy.parsing`。
"""

from __future__ import annotations

from .account import Account
from .artifacts import ChapterDownload, DownloadFailure, PictureArtifact
from .catalog import BookBrief, ChapterBrief, RelatedBook, Taxonomy
from .comment import Comment, CommentFeed
from .detail import Book, Chapter, Picture
from .listing import Listing

__all__ = [
    "Account",
    "Book",
    "BookBrief",
    "Chapter",
    "ChapterBrief",
    "ChapterDownload",
    "Comment",
    "CommentFeed",
    "DownloadFailure",
    "Listing",
    "Picture",
    "PictureArtifact",
    "RelatedBook",
    "Taxonomy",
]
