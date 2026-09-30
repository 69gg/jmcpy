"""文本处理：HTML 转纯文本、车号归一化、文件名净化。"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

__all__ = [
    "host_of",
    "html_to_text",
    "normalize_book_id",
    "sanitize_filename",
    "suffix_of",
    "without_query",
]

#: 会被文件名净化掉的字符（Windows 与 POSIX 的并集，外加控制字符）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: 车号出现在这些形态里：/album/123、/photo/123、?id=123
_ID_IN_PATH = (
    re.compile(r"/(?:album|albums|photo|photos)/(\d+)"),
    re.compile(r"[?&]id=(\d+)"),
    re.compile(r"^(\d+)$"),
)

_DEFAULT_FILENAME_LIMIT = 100


class _TextExtractor(HTMLParser):
    """把 HTML 片段还原成纯文本（评论正文用）。

    ``<br>``、块级标签结尾会转成换行，其余标签只去掉；实体由 ``convert_charrefs`` 还原。
    """

    _BLOCK_TAGS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """去掉标签并压缩空白，块级标签之间保留换行。"""
    if not html:
        return ""
    if "<" not in html:
        # 没有标签时也做同样的空白压缩，保证两条路径产物一致
        return " ".join(html.split())

    parser = _TextExtractor()
    parser.feed(html)
    parser.close()

    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_book_id(value: int | str) -> str:
    """把车号归一化成纯数字字符串。

    接受 ``1472715``、``"1472715"``、``"JM1472715"``、``"jm1472715"``
    以及 ``"https://…/album/1472715"``、``"…/photo/1472715"``、``"…?id=1472715"``。
    """
    if isinstance(value, bool):
        raise ValueError(f"无法解析车号: {value!r}")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"车号必须为正整数: {value}")
        return str(value)

    text = value.strip()
    if not text:
        raise ValueError("车号为空")

    upper = text.upper()
    if upper.startswith("JM"):
        digits = text[2:].strip()
        if digits.isdigit():
            return digits

    for pattern in _ID_IN_PATH:
        match = pattern.search(text)
        if match:
            return match.group(1)

    raise ValueError(f"无法从 {value!r} 解析出车号")


def host_of(url: str) -> str:
    """取出 URL 的主机名；没有则返回空串。"""
    try:
        return urlsplit(url).netloc
    except ValueError:  # pragma: no cover - 极端畸形 URL
        return ""


def without_query(url: str) -> str:
    """去掉查询串。"""
    index = url.find("?")
    return url if index == -1 else url[:index]


def suffix_of(name_or_url: str) -> str:
    """取出小写扩展名（含点）；没有则返回空串。"""
    path = urlsplit(without_query(name_or_url)).path or name_or_url
    dot = path.rfind(".")
    slash = path.rfind("/")
    if dot <= slash or dot == len(path) - 1:
        return ""
    return path[dot:].lower()


def sanitize_filename(name: str, *, limit: int = _DEFAULT_FILENAME_LIMIT) -> str:
    """把标题净化成安全的文件名。

    非法字符替换为下划线，去掉首尾空白与点号（Windows 不允许尾随点号），
    空白折叠为单个空格，超长按字符截断。
    """
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", name)
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(" .")
    if limit > 0 and len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip(" .")
    return cleaned
