"""模型自身的派生行为（不涉及接口解析）。"""

from __future__ import annotations

import pytest

from jmcpy.models import Book, BookBrief, Chapter, ChapterBrief, Comment, CommentFeed, Listing, Picture, Taxonomy


def make_chapter(**overrides: object) -> Chapter:
    defaults: dict[str, object] = {
        "chapter_id": 1472715,
        "book_id": 1472715,
        "title": "示例章节",
        "pictures": ("00001.webp", "00002.webp", "00003.gif"),
        "scramble_id": 220980,
    }
    defaults.update(overrides)
    return Chapter(**defaults)  # type: ignore[arg-type]


def test_book_author_joins_multiple_names() -> None:
    book = Book(book_id=1, title="t", authors=("A", "B"))

    assert book.author == "A, B"
    assert book.is_single_chapter is True


def test_book_single_chapter_flag() -> None:
    book = Book(book_id=1, title="t", chapters=(ChapterBrief(chapter_id=1, title="第1话"),))

    assert book.is_single_chapter is True

    multi = Book(
        book_id=1,
        title="t",
        chapters=(ChapterBrief(chapter_id=1, title="第1话"), ChapterBrief(chapter_id=2, title="第2话")),
    )
    assert multi.is_single_chapter is False


def test_taxonomy_prefers_title() -> None:
    assert str(Taxonomy(taxonomy_id="1", title="同人")) == "同人"
    assert str(Taxonomy(taxonomy_id="1")) == "1"
    assert str(Taxonomy()) == ""


def test_chapter_picture_urls_follow_endpoint() -> None:
    chapter = make_chapter()

    assert chapter.picture_urls("cdn.example") == (
        "https://cdn.example/media/photos/1472715/00001.webp",
        "https://cdn.example/media/photos/1472715/00002.webp",
        "https://cdn.example/media/photos/1472715/00003.gif",
    )
    assert chapter.picture_url("00001.webp", "cdn.example").endswith("/1472715/00001.webp")


def test_chapter_picture_index_is_one_based() -> None:
    chapter = make_chapter()

    first = chapter.picture(1, "cdn.example")

    assert first.index == 1
    assert first.filename == "00001.webp"
    assert first.url.endswith("/00001.webp")
    assert first.scramble_id == 220980


@pytest.mark.parametrize("index", [0, -1, 4])
def test_chapter_picture_index_out_of_range(index: int) -> None:
    with pytest.raises(IndexError, match="越界"):
        make_chapter().picture(index, "cdn.example")


def test_chapter_length_reflects_picture_count() -> None:
    assert len(make_chapter()) == 3


def test_picture_suffix_stem_and_flags() -> None:
    picture = Picture(chapter_id=1, index=1, filename="00001.WEBP", url="https://c/media/photos/1/00001.WEBP")

    assert picture.suffix == ".webp"
    assert picture.stem == "00001"
    assert picture.is_supported is True
    assert picture.is_animated is False


def test_picture_detects_animated_and_unknown_formats() -> None:
    gif = Picture(chapter_id=1, index=1, filename="00009.gif", url="https://c/x.gif?v=1")
    unknown = Picture(chapter_id=1, index=1, filename="00009.bin", url="https://c/x.bin")

    assert gif.is_animated is True
    assert gif.is_supported is True
    assert unknown.is_supported is False


def test_picture_with_endpoint_rebuilds_host() -> None:
    picture = Picture(
        chapter_id=9, index=2, filename="00002.webp", url="https://cdn1.example/media/photos/9/00002.webp"
    )

    moved = picture.with_endpoint("cdn2.example")

    assert moved.url == "https://cdn2.example/media/photos/9/00002.webp"
    assert moved.filename == picture.filename
    assert moved.chapter_id == 9
    assert picture.url == "https://cdn1.example/media/photos/9/00002.webp"  # 原对象不变


def test_picture_with_endpoint_falls_back_to_template() -> None:
    picture = Picture(chapter_id=9, index=2, filename="00002.webp", url="00002.webp")

    assert picture.with_endpoint("cdn2.example").url == "https://cdn2.example/media/photos/9/00002.webp"


def make_comment(comment_id: str, replies: tuple[Comment, ...] = (), **overrides: object) -> Comment:
    defaults: dict[str, object] = {"comment_id": comment_id, "content": "内容", "replies": replies}
    defaults.update(overrides)
    return Comment(**defaults)  # type: ignore[arg-type]


def test_comment_walk_and_reply_count() -> None:
    tree = make_comment(
        "1",
        replies=(
            make_comment("2", replies=(make_comment("3"),)),
            make_comment("4"),
        ),
    )

    assert [item.comment_id for item in tree.walk()] == ["1", "2", "3", "4"]
    assert tree.reply_count == 3
    assert tree.replies[0].reply_count == 1


def test_comment_display_name_and_spoiler_marker() -> None:
    comment = make_comment("1", nickname=None, username="u1", is_spoiler=True)

    assert comment.display_name == "u1"
    assert "（剧透）" in str(comment)

    anonymous = make_comment("2")
    assert anonymous.display_name == ""


def test_comment_feed_page_count_and_comment_count() -> None:
    feed = CommentFeed(
        items=(make_comment("1", replies=(make_comment("2"),)), make_comment("3")),
        total=21,
        page=1,
    )

    assert len(feed) == 2
    assert feed.page_count == 3
    assert feed.comment_count == 3
    assert feed.has_next is True
    assert feed[0].comment_id == "1"
    assert [item.comment_id for item in feed] == ["1", "3"]


def test_comment_feed_without_total() -> None:
    feed = CommentFeed(items=(make_comment("1"),), total=None)

    assert feed.page_count is None
    assert feed.has_next is True


def test_listing_pagination_math() -> None:
    listing: Listing[BookBrief] = Listing(
        items=(BookBrief(book_id=1, title="a"), BookBrief(book_id=2, title="b")),
        total=161,
        page=1,
        page_size=80,
    )

    assert len(listing) == 2
    assert listing.page_count == 3
    assert listing.has_next is True
    assert listing[0].book_id == 1


def test_listing_empty_and_unknown_total() -> None:
    assert Listing(total=0).page_count == 0
    assert Listing(total=None, page=3).page_count is None
    assert Listing(total=None).has_next is False
    assert Listing(total=1, page=1, page_size=80).has_next is False
    assert "total=未知" in str(Listing(total=None))
