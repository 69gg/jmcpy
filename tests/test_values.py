"""容错取值与文本处理。"""

from __future__ import annotations

import pytest

from jmcpy.texts import host_of, html_to_text, normalize_book_id, sanitize_filename, suffix_of, without_query
from jmcpy.values import as_bool, as_int, as_mapping, as_optional_str, as_str, as_str_tuple, pick, text_or_none


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (177, 177),
        ("177", 177),
        (" 42 ", 42),
        ("12,345", 12345),
        ("3.9", 3),
        ("", None),
        (None, None),
        ("abc", None),
        (True, 1),
    ],
)
def test_as_int_is_tolerant(value: object, expected: int | None) -> None:
    assert as_int(value) == expected


def test_as_int_applies_default() -> None:
    assert as_int(None, 5) == 5
    assert as_int("abc", 5) == 5
    assert as_int("7", 5) == 7


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text", "text"),
        (7, "7"),
        (None, ""),
        (True, "true"),
        (False, "false"),
    ],
)
def test_as_str(value: object, expected: str) -> None:
    assert as_str(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("x", "x"), ("  x  ", "x"), ("", None), ("null", None), ("NULL", None), (None, None), (0, "0")],
)
def test_as_optional_str_treats_placeholders_as_missing(value: object, expected: str | None) -> None:
    assert as_optional_str(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        ("true", True),
        ("1", True),
        ("on", True),
        (1, True),
        (False, False),
        ("false", False),
        ("0", False),
        ("", False),
        (None, False),
        ("whatever", False),
    ],
)
def test_as_bool(value: object, expected: bool) -> None:
    assert as_bool(value) is expected


def test_as_bool_default_for_unknown_text() -> None:
    assert as_bool("maybe", True) is True


@pytest.mark.parametrize(
    "value",
    [None, "", [], (), "   "],
)
def test_as_str_tuple_empty_inputs(value: object) -> None:
    assert as_str_tuple(value) == ()


def test_as_str_tuple_handles_both_separators() -> None:
    assert as_str_tuple(["a", "b"]) == ("a", "b")
    assert as_str_tuple("全彩 中文 NTR") == ("全彩", "中文", "NTR")
    assert as_str_tuple("全彩,中文") == ("全彩", "中文")
    assert as_str_tuple([1, "b", None]) == ("1", "b")


def test_as_mapping_only_accepts_mappings() -> None:
    assert as_mapping({"a": 1}) == {"a": 1}
    assert as_mapping(None) == {}
    assert as_mapping(["a"]) == {}


def test_pick_skips_missing_and_blank() -> None:
    source = {"a": None, "b": "", "c": 0, "d": "值"}

    assert pick(source, "a", "b", "c", default="兜底") == 0
    assert pick(source, "a", "b", "d") == "值"
    assert pick(source, "x", "y", default="兜底") == "兜底"


def test_text_or_none_matches_optional_str() -> None:
    assert text_or_none("  ") is None
    assert text_or_none("x") == "x"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1472715, "1472715"),
        ("1472715", "1472715"),
        (" JM1472715 ", "1472715"),
        ("jm1472715", "1472715"),
        ("https://18comic.vip/album/1472715/xxx", "1472715"),
        ("https://18comic.vip/photo/1472715", "1472715"),
        ("https://x.example/?id=1472715", "1472715"),
    ],
)
def test_normalize_book_id_accepts_common_forms(value: object, expected: str) -> None:
    assert normalize_book_id(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["", "abc", "JM", 0, -5, True, "https://x.example/album/"])
def test_normalize_book_id_rejects_garbage(value: object) -> None:
    with pytest.raises(ValueError):
        normalize_book_id(value)  # type: ignore[arg-type]


def test_html_to_text_keeps_line_breaks_and_drops_markup() -> None:
    html = "<div style='flex-direction:row;'>第一行<br>第二行</div><div>第三行 &amp; 更多</div>"

    assert html_to_text(html) == "第一行\n第二行\n第三行 & 更多"


def test_html_to_text_handles_plain_and_empty() -> None:
    assert html_to_text("") == ""
    assert html_to_text("纯文本") == "纯文本"
    assert html_to_text("  空格  折叠  ") == "空格 折叠"


def test_html_to_text_collapses_blank_lines() -> None:
    assert html_to_text("<p>a</p>\n\n\n<p>b</p>") == "a\nb"


def test_without_query_and_suffix_of() -> None:
    assert without_query("https://c/x.webp?v=1") == "https://c/x.webp"
    assert without_query("https://c/x.webp") == "https://c/x.webp"
    assert suffix_of("00001.webp") == ".webp"
    assert suffix_of("https://c/media/photos/1/00001.JPG?v=2") == ".jpg"
    assert suffix_of("noextension") == ""
    assert suffix_of("trailing.") == ""


def test_host_of() -> None:
    assert host_of("https://cdn-msp.example/media/x.webp") == "cdn-msp.example"
    assert host_of("/media/x.webp") == ""


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("正常标题", "正常标题"),
        ('a/b\\c:d*e?f"g<h>i|j', "a_b_c_d_e_f_g_h_i_j"),
        ("  前后空白  ", "前后空白"),
        ("结尾点号...", "结尾点号"),
        ("多   空格", "多 空格"),
        ("", ""),
    ],
)
def test_sanitize_filename(name: str, expected: str) -> None:
    assert sanitize_filename(name) == expected


def test_sanitize_filename_truncates_and_keeps_tail_clean() -> None:
    assert len(sanitize_filename("x" * 500)) == 100
    assert sanitize_filename("a" * 99 + ".", limit=100) == "a" * 99
