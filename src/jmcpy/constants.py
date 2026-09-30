"""服务端协议常量与内置默认值。

本模块只存放「事实」：接口路径、协议盐值、内置端点池、默认阈值与默认请求头。
凡是可能随环境变化、需要用户覆盖的值，一律通过 :class:`jmcpy.settings.Settings` 提供，
这里给出的只是出厂默认。
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- 协议盐值
# token = md5(f"{ts}{MOBILE_TOKEN_SALT}")，tokenparam = f"{ts},{接口版本}"
MOBILE_TOKEN_SALT: Final[str] = "185Hcomic3PAPP7R"
# /chapter_view_template 用独立盐值，否则返回 403
CHAPTER_TOKEN_SALT: Final[str] = "18comicAPPContent"
# 响应体 data 的 AES-256 密钥 = md5(f"{ts}{MOBILE_PAYLOAD_SALT}")
MOBILE_PAYLOAD_SALT: Final[str] = "185Hcomic3PAPP7R"
# 端点源 JSON 的解密盐值（时间戳传空串）
ENDPOINT_FEED_SALT: Final[str] = "diosfjckwpqpdfjkvnqQjsik"

# --------------------------------------------------------------------------- 接口路径
PATH_SETTING: Final[str] = "/setting"
PATH_LOGIN: Final[str] = "/login"
# 携带有效 AVS 时，同一个 /login 会返回用户资料而不是重新登录
PATH_PROFILE: Final[str] = "/login"
PATH_SEARCH: Final[str] = "/search"
PATH_CATEGORY: Final[str] = "/categories/filter"
PATH_BOOK: Final[str] = "/album"
PATH_CHAPTER: Final[str] = "/chapter"
PATH_CHAPTER_TOKEN: Final[str] = "/chapter_view_template"
PATH_COMMENTS: Final[str] = "/forum"

# 网页端路径
PATH_WEB_SEARCH: Final[str] = "/search/photos"
PATH_WEB_CATEGORY: Final[str] = "/albums"

# --------------------------------------------------------------------------- 内置端点池
DEFAULT_MOBILE_ENDPOINTS: Final[tuple[str, ...]] = (
    "www.cdnhjk.net",
    "www.cdngwc.cc",
    "www.cdngwc.net",
    "www.cdngwc.club",
    "www.cdnutc.me",
)

DEFAULT_CDN_ENDPOINTS: Final[tuple[str, ...]] = (
    "cdn-msp.jmapiproxy1.cc",
    "cdn-msp.jmapiproxy2.cc",
    "cdn-msp2.jmapiproxy2.cc",
    "cdn-msp3.jmapiproxy2.cc",
    "cdn-msp.jmapinodeudzn.net",
    "cdn-msp3.jmapinodeudzn.net",
)

# 网页端域名需要运行时发现，内置为空
DEFAULT_WEB_ENDPOINTS: Final[tuple[str, ...]] = ()

# 端点源：按顺序尝试，任一成功即止
ENDPOINT_FEED_URLS: Final[tuple[str, ...]] = (
    "https://rup4a04-c01.tos-ap-southeast-1.bytepluses.com/newsvr-2025.txt",
    "https://rup4a04-c02.tos-cn-hongkong.bytepluses.com/newsvr-2025.txt",
    "https://rup4a04-c03.tos-cn-beijing.bytepluses.com.cn/newsvr-2025.txt",
)

# 网页端发布页，跟随重定向后从页面里抽取可用域名
PUBLISH_PAGE_URL: Final[str] = "https://jmcomicgo.org"

# --------------------------------------------------------------------------- 媒体地址
PICTURE_URL_TEMPLATE: Final[str] = "https://{endpoint}/media/photos/{chapter_id}/{filename}"
COVER_URL_TEMPLATE: Final[str] = "https://{endpoint}/media/albums/{book_id}{size}.jpg"
COVER_SIZE_LIST: Final[str] = "_3x4"

# --------------------------------------------------------------------------- 解扰阈值
FALLBACK_SCRAMBLE_ID: Final[int] = 220980
SCRAMBLE_LEGACY_LIMIT: Final[int] = 268850
SCRAMBLE_MODERN_LIMIT: Final[int] = 421926

PICTURE_SUFFIXES: Final[tuple[str, ...]] = (".jpg", ".webp", ".png", ".gif")
# 动图不做分块解扰
UNDECODED_SUFFIXES: Final[tuple[str, ...]] = (".gif",)

# --------------------------------------------------------------------------- 默认值
DEFAULT_MOBILE_VERSION: Final[str] = "2.1.9"
DEFAULT_TIMEOUT: Final[float] = 20.0
DEFAULT_IMAGE_TIMEOUT: Final[float] = 60.0
DEFAULT_RETRY_TIMES: Final[int] = 3
DEFAULT_BACKOFF_BASE: Final[float] = 0.5
DEFAULT_BACKOFF_MAX: Final[float] = 8.0
DEFAULT_BACKOFF_JITTER: Final[float] = 0.3
DEFAULT_CONCURRENCY: Final[int] = 8
DEFAULT_ENDPOINT_TTL: Final[float] = 12 * 3600.0
DEFAULT_PAGE_SIZE: Final[int] = 80
COMMENT_PAGE_SIZE: Final[int] = 10

# 这些状态码视为「服务端/链路临时异常」，值得重试
DEFAULT_RETRY_STATUS: Final[frozenset[int]] = frozenset({403, 408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})

# --------------------------------------------------------------------------- 请求头
DEFAULT_MOBILE_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Linux; Android 9; V1938CT Build/PQ3A.190705.11211812; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Safari/537.36"
)

DEFAULT_BROWSER_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MOBILE_HEADERS: Final[dict[str, str]] = {
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

PICTURE_HEADERS: Final[dict[str, str]] = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "X-Requested-With": "com.JMComic3.app",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

WEB_HEADERS: Final[dict[str, str]] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}
