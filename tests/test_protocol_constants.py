"""协议事实的防回归断言。

这些取值来自服务端契约（不是实现细节），改动会导致请求被拒或解析出错，
因此用测试把它们钉住，避免被顺手"重构"掉。
"""

from __future__ import annotations

import pytest

from jmcpy import constants as C
from jmcpy.enums import ExportFormat, Genre, RankingSpan, RetryMode, SearchTarget, SortBy, SubGenre, TimeRange


def test_salts_match_server_protocol() -> None:
    assert C.MOBILE_TOKEN_SALT == "185Hcomic3PAPP7R"
    assert C.CHAPTER_TOKEN_SALT == "18comicAPPContent"
    assert C.MOBILE_PAYLOAD_SALT == "185Hcomic3PAPP7R"
    assert C.ENDPOINT_FEED_SALT == "diosfjckwpqpdfjkvnqQjsik"


def test_mobile_paths() -> None:
    assert C.PATH_SETTING == "/setting"
    assert C.PATH_PROFILE == C.PATH_LOGIN == "/login"
    assert (C.PATH_SEARCH, C.PATH_CATEGORY) == ("/search", "/categories/filter")
    assert (C.PATH_BOOK, C.PATH_CHAPTER) == ("/album", "/chapter")
    assert C.PATH_CHAPTER_TOKEN == "/chapter_view_template"
    assert C.PATH_COMMENTS == "/forum"


def test_scramble_thresholds() -> None:
    assert (C.FALLBACK_SCRAMBLE_ID, C.SCRAMBLE_LEGACY_LIMIT, C.SCRAMBLE_MODERN_LIMIT) == (220980, 268850, 421926)


def test_media_url_templates() -> None:
    assert C.PICTURE_URL_TEMPLATE.format(endpoint="cdn", chapter_id=1, filename="00001.webp") == (
        "https://cdn/media/photos/1/00001.webp"
    )
    assert C.COVER_URL_TEMPLATE.format(endpoint="cdn", book_id=1, size="") == "https://cdn/media/albums/1.jpg"
    assert C.COVER_URL_TEMPLATE.format(endpoint="cdn", book_id=1, size=C.COVER_SIZE_LIST).endswith("1_3x4.jpg")


def test_builtin_endpoint_pools_are_non_empty_and_unique() -> None:
    for pool in (C.DEFAULT_MOBILE_ENDPOINTS, C.DEFAULT_CDN_ENDPOINTS, C.ENDPOINT_FEED_URLS):
        assert pool
        assert len(set(pool)) == len(pool)
    assert not C.DEFAULT_WEB_ENDPOINTS, "网页端端点需要运行时发现，内置池应为空"


def test_retry_status_codes_are_http_codes() -> None:
    assert all(400 <= code < 600 for code in C.DEFAULT_RETRY_STATUS)
    assert {403, 429, 500, 502, 503, 504} <= C.DEFAULT_RETRY_STATUS


@pytest.mark.parametrize(
    ("member", "value"),
    [
        (SearchTarget.SITE, 0),
        (SearchTarget.ACTOR, 4),
        (SortBy.LATEST, "mr"),
        (SortBy.COMMENTS, "md"),
        (TimeRange.ALL, "a"),
        (TimeRange.DAY, "t"),
        (Genre.ALL, "0"),
        (Genre.THREE_D, "3D"),
        (SubGenre.CG, "CG"),
        (SubGenre.THREE_D, "3d"),
        (RankingSpan.WEEK, "w"),
        (RetryMode.RETRY_FIRST, "retry_first"),
        (ExportFormat.PDF, "pdf"),
    ],
)
def test_enum_members_carry_wire_values(member: object, value: object) -> None:
    assert member == value


def test_search_target_is_int_like_for_query_params() -> None:
    # main_tag 必须是整数才能被正确编码
    assert int(SearchTarget.TAG) == 3
