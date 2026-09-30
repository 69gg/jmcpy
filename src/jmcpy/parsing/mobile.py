"""移动端接口响应的解析。

服务端的字段名保持不变（那是协议），这里负责把它们映射成 :mod:`jmcpy.models`。
所有函数都是纯函数，方便离线测试，也让同步/异步客户端共用同一套逻辑。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..constants import COVER_URL_TEMPLATE, DEFAULT_PAGE_SIZE, FALLBACK_SCRAMBLE_ID
from ..errors import ParseFailed
from ..models import (
    Account,
    Book,
    BookBrief,
    Chapter,
    ChapterBrief,
    Comment,
    CommentFeed,
    Listing,
    RelatedBook,
    Taxonomy,
)
from ..texts import html_to_text
from ..values import as_bool, as_int, as_items, as_mapping, as_optional_str, as_str, as_str_tuple, pick

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

_SCRAMBLE_PATTERN = re.compile(r"var scramble_id = (\d+)")


def _cover_url(raw_image: Any, book_id: int, cdn_endpoint: str | None) -> str | None:
    """封面地址：优先用响应里的路径，缺失时按约定模板补一个。"""
    text = as_optional_str(raw_image)
    if text:
        if text.startswith(("http://", "https://")):
            return text
        if cdn_endpoint:
            return f"https://{cdn_endpoint}/{text.lstrip('/')}"
        return text
    if not cdn_endpoint:
        return None
    return COVER_URL_TEMPLATE.format(endpoint=cdn_endpoint, book_id=book_id, size="")


def parse_taxonomy(value: Any) -> Taxonomy | None:
    """解析 ``category`` / ``category_sub``；两者都空时返回 ``None``。"""
    source = as_mapping(value)
    taxonomy_id = as_optional_str(pick(source, "id"))
    title = as_optional_str(pick(source, "title", "name"))
    if taxonomy_id is None and title is None:
        return None
    return Taxonomy(taxonomy_id=taxonomy_id, title=title)


def parse_book_brief(item: Mapping[str, Any], *, cdn_endpoint: str | None = None) -> BookBrief:
    """解析列表里的本子条目。"""
    book_id = as_int(pick(item, "id", "aid")) or 0
    return BookBrief(
        book_id=book_id,
        title=as_str(pick(item, "name", "title")).strip(),
        author=as_str(pick(item, "author")).strip(),
        description=as_optional_str(pick(item, "description")),
        cover_url=_cover_url(pick(item, "image", "cover"), book_id, cdn_endpoint),
        category=parse_taxonomy(pick(item, "category")),
        sub_category=parse_taxonomy(pick(item, "category_sub", "categorySub")),
        liked=as_bool(pick(item, "liked")),
        is_favorite=as_bool(pick(item, "is_favorite", "isFavorite")),
        updated_at=as_int(pick(item, "update_at", "updateAt")),
        raw=dict(item),
    )


def parse_book_listing(
    payload: Mapping[str, Any],
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    cdn_endpoint: str | None = None,
) -> Listing[BookBrief]:
    """解析搜索/分类/排行的一页结果。"""
    content = payload.get("content") or payload.get("list") or ()
    items = tuple(
        parse_book_brief(as_mapping(item), cdn_endpoint=cdn_endpoint) for item in content if isinstance(item, Mapping)
    )
    return Listing(
        items=items,
        total=as_int(pick(payload, "total")),
        page=page,
        page_size=page_size,
        raw=dict(payload),
    )


def parse_redirect_book_id(payload: Mapping[str, Any]) -> int | None:
    """搜索直接命中车号时，服务端会返回 ``redirect_aid`` 而不是结果列表。"""
    return as_int(pick(payload, "redirect_aid", "redirectAid"))


def _parse_chapters(series: Any, book_id: int, single: bool) -> tuple[ChapterBrief, ...]:
    if single:
        return (ChapterBrief(chapter_id=book_id, title="第1话", order=1),)

    chapters: list[ChapterBrief] = []
    for index, entry in enumerate(series if isinstance(series, Sequence) else (), start=1):
        source = as_mapping(entry)
        chapter_id = as_int(pick(source, "id"))
        if chapter_id is None:
            continue
        order = as_int(pick(source, "sort")) or index
        title = as_str(pick(source, "name")).strip() or f"第{order}话"
        chapters.append(ChapterBrief(chapter_id=chapter_id, title=title, order=order, raw=dict(source)))

    if not chapters:
        return (ChapterBrief(chapter_id=book_id, title="第1话", order=1),)
    return tuple(chapters)


def parse_book_detail(payload: Mapping[str, Any], *, cdn_endpoint: str | None = None) -> Book:
    """解析本子详情。"""
    book_id = as_int(pick(payload, "id", "aid")) or 0
    series_id = as_int(pick(payload, "series_id", "seriesId")) or 0
    single = series_id == 0
    chapters = _parse_chapters(payload.get("series"), book_id, single)

    related: list[RelatedBook] = []
    for entry in payload.get("related_list") or payload.get("relatedList") or ():
        source = as_mapping(entry)
        related_id = as_int(pick(source, "id"))
        if related_id is None:
            continue
        related.append(
            RelatedBook(
                book_id=related_id,
                title=as_str(pick(source, "name", "title")).strip(),
                author=as_str(pick(source, "author")).strip(),
                cover_url=_cover_url(pick(source, "image", "cover"), related_id, cdn_endpoint),
                raw=dict(source),
            )
        )

    return Book(
        book_id=book_id,
        title=as_str(pick(payload, "name", "title")).strip(),
        authors=as_items(payload.get("author")),
        description=as_optional_str(pick(payload, "description")),
        tags=as_str_tuple(payload.get("tags")),
        works=as_items(payload.get("works")),
        cast=as_items(payload.get("actors")),
        likes=as_int(pick(payload, "likes")),
        views=as_int(pick(payload, "total_views", "totalViews")),
        comment_count=as_int(pick(payload, "comment_total", "commentTotal")),
        chapter_count=as_int(pick(payload, "total_photos", "totalPhotos")) or len(chapters),
        chapters=chapters,
        related=tuple(related),
        liked=as_bool(pick(payload, "liked")),
        is_favorite=as_bool(pick(payload, "is_favorite", "isFavorite")),
        added_at=as_int(pick(payload, "addtime", "addTime")),
        raw=dict(payload),
    )


def parse_chapter_detail(
    payload: Mapping[str, Any],
    *,
    scramble_id: int | None = None,
) -> Chapter:
    """解析章节详情。"""
    chapter_id = as_int(pick(payload, "id", "aid")) or 0
    series_id = as_int(pick(payload, "series_id", "seriesId")) or 0

    order = 1
    if series_id != 0:
        for entry in payload.get("series") or ():
            source = as_mapping(entry)
            if as_int(pick(source, "id")) == chapter_id:
                order = as_int(pick(source, "sort")) or 1
                break

    pictures = as_items(payload.get("images"))
    return Chapter(
        chapter_id=chapter_id,
        book_id=chapter_id if series_id == 0 else series_id,
        title=as_str(pick(payload, "name", "title")).strip(),
        order=order,
        tags=as_str_tuple(payload.get("tags")),
        pictures=pictures,
        scramble_id=scramble_id,
        liked=as_bool(pick(payload, "liked")),
        is_favorite=as_bool(pick(payload, "is_favorite", "isFavorite")),
        added_at=as_int(pick(payload, "addtime", "addTime")),
        raw=dict(payload),
    )


def parse_scramble_id(text: str) -> int:
    """从 ``/chapter_view_template`` 的 HTML 里取出 ``scramble_id``。"""
    match = _SCRAMBLE_PATTERN.search(text)
    return int(match.group(1)) if match else FALLBACK_SCRAMBLE_ID


def parse_comment(item: Mapping[str, Any], *, book_id: str | None = None) -> Comment:
    """解析一条评论（含递归的回评）。

    回评不带 ``AID`` 字段，这里让它继承父评论的本子号，避免调用方拿到 ``None``。
    """
    own_book_id = as_optional_str(pick(item, "AID", "aid")) or book_id
    replies = tuple(
        parse_comment(as_mapping(reply), book_id=own_book_id)
        for reply in (item.get("replys") or item.get("replies") or ())
        if isinstance(reply, Mapping)
    )
    parent_id = as_optional_str(pick(item, "parent_CID", "parentId"))
    if parent_id == "0":
        parent_id = None

    likes = as_int(pick(item, "likes"))
    return Comment(
        comment_id=as_str(pick(item, "CID", "comment_id", "id")),
        content=html_to_text(as_str(pick(item, "content"))),
        parent_id=parent_id,
        book_id=own_book_id,
        author_id=as_optional_str(pick(item, "UID", "uid")),
        username=as_optional_str(pick(item, "username")),
        nickname=as_optional_str(pick(item, "nickname")),
        is_spoiler=_is_spoiler(pick(item, "is_spoiler", "spoiler")),
        likes=likes,
        posted_at=as_optional_str(pick(item, "addtime", "created_at")),
        replies=replies,
        raw=dict(item),
    )


def _is_spoiler(value: Any) -> bool:
    """服务端用 ``spoiler == "2"`` 表示剧透。"""
    if isinstance(value, bool):
        return value
    return as_str(value).strip() == "2"


def parse_comment_feed(payload: Mapping[str, Any], *, page: int = 1) -> CommentFeed:
    """解析评论分页。"""
    items = tuple(parse_comment(as_mapping(item)) for item in (payload.get("list") or ()) if isinstance(item, Mapping))
    return CommentFeed(items=items, total=as_int(pick(payload, "total")), page=page, raw=dict(payload))


def parse_account(payload: Mapping[str, Any], *, cdn_endpoint: str | None = None) -> Account:
    """解析用户资料。"""
    photo = as_optional_str(pick(payload, "photo", "avatar"))
    avatar_url: str | None = None
    if photo:
        if photo.startswith(("http://", "https://")):
            avatar_url = photo
        elif cdn_endpoint:
            avatar_url = f"https://{cdn_endpoint}/media/users/{photo.lstrip('/')}"
        else:
            avatar_url = photo

    return Account(
        uid=as_str(pick(payload, "uid")),
        username=as_str(pick(payload, "username")),
        email=as_optional_str(pick(payload, "email")),
        email_verified=as_str(pick(payload, "emailverified", "email_verified")).lower() in {"yes", "true", "1"},
        avatar_url=avatar_url,
        gender=as_optional_str(pick(payload, "gender")),
        message=as_optional_str(pick(payload, "message")),
        coins=as_int(pick(payload, "coin", "coins")),
        favorites=as_int(pick(payload, "album_favorites", "albumFavorites")),
        favorites_limit=as_int(pick(payload, "album_favorites_max", "albumFavoritesMax")),
        level=as_int(pick(payload, "level")),
        level_name=as_optional_str(pick(payload, "level_name", "levelName")),
        experience=as_int(pick(payload, "exp")),
        experience_percent=_as_float(pick(payload, "expPercent", "exp_percent")),
        next_level_experience=as_int(pick(payload, "nextLevelExp", "next_level_exp")),
        ad_free=as_bool(pick(payload, "ad_free", "adFree")),
        raw=dict(payload),
    )


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(as_str(value).strip())
    except ValueError:
        return None


def require_payload_json(text: str) -> Mapping[str, Any]:
    """把解密后的文本解析成 JSON 对象。"""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseFailed("解密后的内容不是合法 JSON", snippet=text) from exc
    if not isinstance(document, Mapping):
        raise ParseFailed("解密后的内容不是 JSON 对象", snippet=text)
    return document
