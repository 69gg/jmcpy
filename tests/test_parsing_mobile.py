"""移动端响应的解析。

夹具是从线上抓取后**只替换文本字段**的真实结构，因此字段类型、嵌套层次、
可选字段都与线上一致；涉及剧透标记等少数分支使用合成数据并在用例里注明。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jmcpy.constants import FALLBACK_SCRAMBLE_ID
from jmcpy.parsing import (
    parse_account,
    parse_book_detail,
    parse_book_listing,
    parse_chapter_detail,
    parse_comment_feed,
    parse_redirect_book_id,
    parse_scramble_id,
    require_payload_json,
)

CDN = "cdn-msp2.example"


def test_search_payload_becomes_listing(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_search.json")

    listing = parse_book_listing(payload, page=1, cdn_endpoint=CDN)

    assert listing.total == payload["total"]
    assert len(listing.items) == len(payload["content"])
    first = listing.items[0]
    assert first.book_id == int(payload["content"][0]["id"])
    assert first.title
    # 响应里 image 为空，封面按约定模板补全
    assert first.cover_url == f"https://{CDN}/media/albums/{first.book_id}.jpg"
    assert first.category is not None and first.category.title
    assert first.raw["id"] == payload["content"][0]["id"]


def test_book_detail_payload_becomes_book(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_album.json")

    book = parse_book_detail(payload, cdn_endpoint=CDN)

    assert book.book_id == int(payload["id"])
    assert book.chapter_count == payload["total_photos"]
    assert len(book.chapters) == len(payload["series"])
    assert [chapter.chapter_id for chapter in book.chapters] == [int(item["id"]) for item in payload["series"]]
    assert [chapter.order for chapter in book.chapters] == [int(item["sort"]) for item in payload["series"]]
    assert book.tags and book.authors
    assert book.views == int(payload["total_views"])
    assert book.likes == int(payload["likes"])
    assert book.comment_count == int(payload["comment_total"])
    assert len(book.related) == len(payload["related_list"])
    assert book.is_single_chapter is False


def test_single_chapter_book_gets_synthetic_chapter(load_fixture: Callable[[str], Any]) -> None:
    payload = dict(load_fixture("mobile_album.json"), series=[], series_id="0", total_photos=16)

    book = parse_book_detail(payload)

    assert book.is_single_chapter is True
    assert [chapter.chapter_id for chapter in book.chapters] == [book.book_id]
    assert book.chapters[0].title == "第1话"
    assert book.chapter_count == 16


def test_chapter_payload_becomes_chapter(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_chapter.json")

    chapter = parse_chapter_detail(payload, scramble_id=220980)

    assert chapter.chapter_id == int(payload["id"])
    assert chapter.book_id == int(payload["series_id"])
    assert chapter.pictures == tuple(payload["images"])
    assert chapter.scramble_id == 220980
    assert chapter.order == 1  # 是该本子的第一章
    assert chapter.tags  # 空格分隔的标签串被拆成元组


def test_chapter_order_follows_series_position(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_chapter.json")
    target = payload["series"][2]

    chapter = parse_chapter_detail(dict(payload, id=target["id"]))

    assert chapter.order == int(target["sort"])


def test_comment_feed_keeps_nested_replies(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_comments.json")

    feed = parse_comment_feed(payload, page=1)

    assert feed.total == int(payload["total"])
    assert len(feed.items) == len(payload["list"])
    nested = [comment for comment in feed.items if comment.replies]
    assert nested, "夹具里应当保留带回评的评论"
    for comment in nested:
        raw = comment.raw
        assert len(comment.replies) == len(raw["replys"])
        for reply in comment.replies:
            assert reply.parent_id == comment.comment_id
            assert reply.book_id == comment.book_id


def test_comment_content_is_plain_text(load_fixture: Callable[[str], Any]) -> None:
    payload = load_fixture("mobile_comments.json")
    payload = dict(payload, list=[dict(payload["list"][0], content="<div>第一行<br>第二行</div>")])

    feed = parse_comment_feed(payload)

    assert feed.items[0].content == "第一行\n第二行"


def test_comment_spoiler_flag_uses_value_two() -> None:
    """服务端用 ``spoiler == "2"`` 表示剧透（线上抽样 51 条中 50 条为 "1"）。"""
    payload = {
        "total": 3,
        "list": [
            {"CID": "1", "content": "a", "spoiler": "1"},
            {"CID": "2", "content": "b", "spoiler": "2"},
            {"CID": "3", "content": "c", "spoiler": "0"},
        ],
    }

    feed = parse_comment_feed(payload)

    assert [comment.is_spoiler for comment in feed.items] == [False, True, False]


def test_comment_likes_and_missing_fields_are_tolerated() -> None:
    payload = {"total": None, "list": [{"CID": 123, "likes": "7", "content": None}]}

    feed = parse_comment_feed(payload)

    assert feed.total is None
    assert feed.items[0].comment_id == "123"
    assert feed.items[0].likes == 7
    assert feed.items[0].content == ""
    assert feed.items[0].parent_id is None
    assert feed.page_count is None


def test_redirect_payload_reports_target_book() -> None:
    assert parse_redirect_book_id({"redirect_aid": "310311", "content": [], "total": 1}) == 310311
    assert parse_redirect_book_id({"total": 1, "content": []}) is None


def test_parse_scramble_id_from_html(load_text: Callable[[str], str]) -> None:
    html = "<html><script>var scramble_id = 220980;</script></html>"

    assert parse_scramble_id(html) == 220980


def test_parse_scramble_id_falls_back() -> None:
    assert parse_scramble_id("<html>没有该变量</html>") == FALLBACK_SCRAMBLE_ID
    assert parse_scramble_id("var scramble_id = ;") == FALLBACK_SCRAMBLE_ID


def test_parse_account_maps_documented_fields() -> None:
    """资料字段按接口文档给出的结构合成（真实响应需要登录才能取到）。"""
    payload = {
        "uid": "123",
        "username": "someone",
        "email": "a@b.c",
        "emailverified": "yes",
        "photo": "123.gif",
        "gender": "Male",
        "coin": "1500",
        "album_favorites": 12,
        "album_favorites_max": 1000,
        "level": 1,
        "level_name": "初來乍到",
        "exp": "120",
        "expPercent": 12.5,
        "nextLevelExp": 1000,
        "ad_free": False,
        "s": "AVS-TOKEN",
    }

    account = parse_account(payload, cdn_endpoint=CDN)

    assert account.uid == "123"
    assert account.username == "someone"
    assert account.email_verified is True
    assert account.avatar_url == f"https://{CDN}/media/users/123.gif"
    assert account.coins == 1500
    assert account.favorites == 12
    assert account.favorites_limit == 1000
    assert account.experience == 120
    assert account.experience_percent == 12.5
    assert account.next_level_experience == 1000
    assert account.level_name == "初來乍到"


def test_parse_account_tolerates_missing_and_absolute_avatar() -> None:
    account = parse_account({"username": "u", "photo": "https://x.example/a.gif"})

    assert account.avatar_url == "https://x.example/a.gif"
    assert account.uid == ""
    assert account.coins is None
    assert account.experience_percent is None


def test_require_payload_json() -> None:
    assert require_payload_json('{"a": 1}') == {"a": 1}
