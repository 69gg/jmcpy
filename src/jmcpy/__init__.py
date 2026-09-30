"""禁漫天堂 Python SDK。

只提供库，不带 GUI 与命令行。核心能力：

* 搜索（站内 / 作品 / 作者 / 标签 / 登场角色，含排序、时间范围、大分类与副分类）
* 评论（含多层回评与剧透标记）、分类浏览与日/周/月排行榜
* 本子详情、章节详情与图片地址
* 图片下载、分块还原，交付为 bytes / base64 / 文件路径 / PDF
* 可选登录，凭据加密保存在用户配置目录，未登录同样可用
* 自动重试与多端点切换、端点自动发现、超时与并发可配

最简单的用法：

.. code-block:: python

    from jmcpy import Client, ExportFormat

    with Client() as client:
        page = client.search("关键词")
        book = client.get_book(page.items[0].book_id)
        chapter = client.get_chapter(book.chapters[0].chapter_id)
        result = client.download(chapter, output=ExportFormat.PDF, dest="./downloads")

库内日志走标准 :mod:`logging`，包级 logger 已挂 ``NullHandler``：默认静默，
需要排查问题时由调用方配置 ``logging``。
"""

from __future__ import annotations

import logging

from ._version import __version__
from .clients.facade import AsyncClient, Client
from .clients.mobile import AsyncMobileClient, MobileClient
from .clients.web import AsyncWebClient, WebClient
from .credentials import CredentialStore, LoginSession
from .endpoints import EndpointSet
from .enums import (
    Backend,
    ExportFormat,
    Genre,
    RankingSpan,
    RetryMode,
    SearchTarget,
    SortBy,
    SubGenre,
    TimeRange,
)
from .errors import (
    ApiRejected,
    AttemptFailure,
    AuthRejected,
    AuthRequired,
    BadStatus,
    ChallengeBlocked,
    ConfigurationError,
    CredentialError,
    CryptoError,
    InvalidArgument,
    JmcpyError,
    NetworkIssue,
    NotFound,
    ParseFailed,
    RegionBlocked,
    RequestFailed,
    ResponseInvalid,
)
from .exporting import download_chapter, download_chapter_async
from .models import (
    Account,
    Book,
    BookBrief,
    Chapter,
    ChapterBrief,
    ChapterDownload,
    Comment,
    CommentFeed,
    DownloadFailure,
    Listing,
    Picture,
    PictureArtifact,
    RelatedBook,
    Taxonomy,
)
from .settings import Settings

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "Account",
    "ApiRejected",
    "AsyncClient",
    "AsyncMobileClient",
    "AsyncWebClient",
    "AttemptFailure",
    "AuthRejected",
    "AuthRequired",
    "Backend",
    "BadStatus",
    "Book",
    "BookBrief",
    "ChallengeBlocked",
    "Chapter",
    "ChapterBrief",
    "ChapterDownload",
    "Client",
    "Comment",
    "CommentFeed",
    "ConfigurationError",
    "CredentialError",
    "CredentialStore",
    "CryptoError",
    "DownloadFailure",
    "EndpointSet",
    "ExportFormat",
    "Genre",
    "InvalidArgument",
    "JmcpyError",
    "Listing",
    "LoginSession",
    "MobileClient",
    "NetworkIssue",
    "NotFound",
    "ParseFailed",
    "Picture",
    "PictureArtifact",
    "RankingSpan",
    "RegionBlocked",
    "RelatedBook",
    "RequestFailed",
    "ResponseInvalid",
    "RetryMode",
    "SearchTarget",
    "Settings",
    "SortBy",
    "SubGenre",
    "Taxonomy",
    "TimeRange",
    "WebClient",
    "__version__",
    "download_chapter",
    "download_chapter_async",
]
