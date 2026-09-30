"""真实网络冒烟测试（默认跳过）。

开启方式::

    JMCPY_LIVE=1 uv run pytest -m live

只用移动端接口：网页端在有反爬验证的网络环境下可能整体不可用（见 docs/troubleshooting.md）。
用例只做少量轻量请求，不批量下载。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jmcpy.clients.mobile import AsyncMobileClient, MobileClient
from jmcpy.enums import Genre, RankingSpan, SearchTarget, SortBy, TimeRange

pytestmark = pytest.mark.live

#: 一个章节数很多的长篇本子，结构稳定，适合作冒烟对象
SAMPLE_BOOK_ID = "1114751"


def test_live_search_and_ranking() -> None:
    with MobileClient() as client:
        listing = client.search("MANA", target=SearchTarget.SITE, sort=SortBy.LATEST, time_range=TimeRange.ALL)
        assert listing.items, "搜索应当返回结果"
        assert listing.total and listing.total > 0
        assert all(item.book_id > 0 for item in listing.items)

        ranking = client.ranking(RankingSpan.WEEK, page=1, genre=Genre.ALL)
        assert ranking.items


def test_live_book_chapter_and_comments() -> None:
    with MobileClient() as client:
        book = client.get_book(SAMPLE_BOOK_ID)
        assert book.book_id == int(SAMPLE_BOOK_ID)
        assert book.chapters, "长篇本子应当有章节列表"

        chapter = client.get_chapter(book.chapters[0].chapter_id)
        assert chapter.pictures, "章节应当有图片"
        assert chapter.scramble_id, "应当拿到解扰参数"

        feed = client.get_comments(SAMPLE_BOOK_ID, page=1)
        assert feed.total is not None
        assert feed.page_count is not None


def test_live_endpoint_refresh() -> None:
    with MobileClient() as client:
        endpoints = client.refresh_endpoints(refresh=True)
        assert endpoints.mobile, "端点自动更新应当拿到线路表"


async def test_live_async_client() -> None:
    async with AsyncMobileClient() as client:
        listing = await client.search("MANA")
        assert listing.items


def test_live_download_pictures_and_pdf(tmp_path: Path) -> None:
    """只取前两张，验证「取图 → 解码 → 交付」整条链路。"""
    from dataclasses import replace

    from jmcpy.enums import ExportFormat
    from jmcpy.models.artifacts import ChapterDownload

    with MobileClient() as client:
        book = client.get_book(SAMPLE_BOOK_ID)
        chapter = client.get_chapter(book.chapters[0].chapter_id)
        sample = replace(chapter, pictures=chapter.pictures[:2])

        raw = client.fetch_picture(client.picture(sample, 1))
        assert len(raw) > 1024, "图片响应应当有实际内容"

        typed = client.download(sample, output=ExportFormat.BYTES, concurrency=2)
        assert isinstance(typed, ChapterDownload)
        assert len(typed) == 2
        assert all(item.data for item in typed.artifacts)

        as_pdf = client.download(sample, output=ExportFormat.PDF, dest=tmp_path, concurrency=2)
        assert as_pdf.pdf is not None and as_pdf.pdf.is_file()
        assert as_pdf.pdf.stat().st_size > 1024
