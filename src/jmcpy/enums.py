"""对外暴露的枚举：搜索维度、排序、时间范围、分类、重试模式与导出格式。

所有取值都直接对应服务端参数，枚举成员名是给调用方看的语义名。
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

__all__ = [
    "Backend",
    "ExportFormat",
    "Genre",
    "RankingSpan",
    "RetryMode",
    "SearchTarget",
    "SortBy",
    "SubGenre",
    "TimeRange",
]


class SearchTarget(IntEnum):
    """搜索维度，对应 ``main_tag``。"""

    SITE = 0
    WORK = 1
    AUTHOR = 2
    TAG = 3
    ACTOR = 4


class SortBy(StrEnum):
    """排序方式，对应 ``o``。"""

    LATEST = "mr"
    VIEWS = "mv"
    PICTURES = "mp"
    LIKES = "tf"
    SCORE = "tr"
    COMMENTS = "md"


class TimeRange(StrEnum):
    """时间范围，对应 ``t``。"""

    ALL = "a"
    DAY = "t"
    WEEK = "w"
    MONTH = "m"


class Genre(StrEnum):
    """大分类，对应 ``c``。``ALL`` 即不限定分类。"""

    ALL = "0"
    DOUJIN = "doujin"
    SINGLE = "single"
    SHORT = "short"
    ANOTHER = "another"
    HANMAN = "hanman"
    MEIMAN = "meiman"
    DOUJIN_COSPLAY = "doujin_cosplay"
    THREE_D = "3D"
    ENGLISH_SITE = "english_site"


class SubGenre(StrEnum):
    """副分类。只有网页端接口支持，移动端接口会忽略。"""

    CHINESE = "chinese"
    JAPANESE = "japanese"
    CG = "CG"
    YOUTH = "youth"
    OTHER = "other"
    THREE_D = "3d"
    COSPLAY = "cosplay"


class RankingSpan(StrEnum):
    """排行榜的时间跨度。"""

    DAY = "t"
    WEEK = "w"
    MONTH = "m"


class RetryMode(StrEnum):
    """重试与换端点的优先级。"""

    #: 先在当前端点上退避重试，用尽次数后再换下一个端点
    RETRY_FIRST = "retry_first"
    #: 每轮把全部端点各试一次，整轮失败后退避再来一轮
    ROTATE_FIRST = "rotate_first"


class Backend(StrEnum):
    """HTTP 传输层实现。"""

    #: 浏览器指纹伪装（默认）
    CURL_CFFI = "curl_cffi"
    #: 纯 Python 回退实现，需要 ``jmcpy[httpx]``
    HTTPX = "httpx"


class ExportFormat(StrEnum):
    """图片下载的产出形式。"""

    #: list[bytes]，解码后的图片原始字节
    BYTES = "bytes"
    #: list[str]，解码后图片的 base64 文本
    BASE64 = "base64"
    #: list[Path]，落盘的文件路径
    PATH = "path"
    #: Path，整章合成一个 PDF
    PDF = "pdf"
