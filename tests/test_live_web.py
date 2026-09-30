"""网页端真实网络冒烟（默认跳过）。

开启方式::

    JMCPY_LIVE=1 uv run pytest -m live

网页端各线路的可用性随网络环境而变：有的域名当前不可达，有的会被反爬验证页拦截。
SDK 会自动轮换并记住可用线路，因此这些用例只要**有一条线路能用**就应当通过；
全部线路都不可用时跳过而不是失败（这属于网络环境问题，不是代码回归）。
"""

from __future__ import annotations

import pytest

from jmcpy import ChallengeBlocked, Genre, RankingSpan, SortBy, SubGenre, TimeRange, WebClient
from jmcpy.errors import ConfigurationError, ParseFailed

pytestmark = pytest.mark.live

SEARCH_QUERY = "MANA"


def _open_client() -> WebClient:
    client = WebClient()
    client.refresh_endpoints()
    if not client.endpoints:
        client.close()
        pytest.skip("当前网络环境没有可用的网页端域名")
    return client


def _require_working_route(client: WebClient) -> None:
    try:
        client.search(SEARCH_QUERY)
    except (ChallengeBlocked, ConfigurationError) as exc:
        client.close()
        pytest.skip(f"所有网页端线路都不可用: {type(exc).__name__}")


def test_live_web_search_and_paging() -> None:
    client = _open_client()
    try:
        _require_working_route(client)

        first = client.search(SEARCH_QUERY, sort=SortBy.LATEST, time_range=TimeRange.ALL)
        assert first.items, "搜索应当返回结果"
        assert first.total and first.total > 0
        assert all(item.book_id > 0 and item.title for item in first.items)
        assert all(item.author for item in first.items), "网页端也应当解析出作者"

        second = client.search(SEARCH_QUERY, page=2)
        assert second.page == 2
        assert second.items and second.items[0].book_id != first.items[0].book_id
    finally:
        client.close()


def test_live_web_category_and_sub_category() -> None:
    """副分类是网页端独有的能力，也顺带验证分类路径。"""
    client = _open_client()
    try:
        _require_working_route(client)

        plain = client.browse(genre=Genre.HANMAN, sort=SortBy.VIEWS, time_range=TimeRange.MONTH)
        assert plain.items

        sub = client.browse(genre=Genre.DOUJIN, sub_genre=SubGenre.CG, sort=SortBy.VIEWS)
        assert sub.items
    finally:
        client.close()


def test_live_web_ranking() -> None:
    client = _open_client()
    try:
        _require_working_route(client)

        weekly = client.ranking(RankingSpan.WEEK)
        assert weekly.items
    finally:
        client.close()


def test_live_web_book_id_becomes_single_result() -> None:
    client = _open_client()
    try:
        _require_working_route(client)

        single = client.search("1472715")
        assert len(single) == 1
        assert single.items[0].book_id == 1472715
        assert single.items[0].title
    finally:
        client.close()


def test_live_web_invalid_query_is_reported() -> None:
    client = _open_client()
    try:
        _require_working_route(client)

        with pytest.raises(ParseFailed):
            client.search("a")  # 站点要求至少两个字
    finally:
        client.close()


async def test_live_web_async_client() -> None:
    from jmcpy import AsyncClient

    async with AsyncClient() as client:
        try:
            page = await client.search(SEARCH_QUERY, genre=Genre.DOUJIN, sub_genre=SubGenre.CG)
        except (ChallengeBlocked, ConfigurationError) as exc:
            pytest.skip(f"所有网页端线路都不可用: {type(exc).__name__}")
        assert page.items or page.total is not None
