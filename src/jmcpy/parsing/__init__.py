"""解析层：把服务端响应翻译成数据模型。

这里的函数都是纯函数（不做 I/O），同步与异步客户端共用同一套解析逻辑。
"""

from __future__ import annotations

from .mobile import (
    parse_account,
    parse_book_brief,
    parse_book_detail,
    parse_book_listing,
    parse_chapter_detail,
    parse_comment,
    parse_comment_feed,
    parse_redirect_book_id,
    parse_scramble_id,
    parse_taxonomy,
    require_payload_json,
)

__all__ = [
    "parse_account",
    "parse_book_brief",
    "parse_book_detail",
    "parse_book_listing",
    "parse_chapter_detail",
    "parse_comment",
    "parse_comment_feed",
    "parse_redirect_book_id",
    "parse_scramble_id",
    "parse_taxonomy",
    "require_payload_json",
]
