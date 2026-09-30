#!/usr/bin/env python
"""把网页端页面录成脱敏夹具，用于离线回归测试。

为什么需要它：网页端解析依赖页面结构，而结构只有真实页面能校准。
直接把线上 HTML 存进仓库既臃肿又夹带内容，所以这里保留**结构与数字**、
把可读文本替换成占位符。

用法::

    uv run python scripts/record_web_fixture.py --base-url https://example.domain \
        --query MANA --cards 3

产出：

* ``tests/fixtures/web_search.html``  搜索页（截取到前若干张卡片）
* ``tests/fixtures/web_error.html``   错误提示页（搜索词过短）
* ``tests/fixtures/web_album.html``   本子详情页骨架（用于「搜索被重定向」分支）
"""

from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path

from curl_cffi import requests as creq

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
PLACEHOLDER = "示例"
NUMERIC = re.compile(r"^[\s\d,.:%·\-]*$")
#: 连同内容一起丢掉的元素
DROP_TAGS = {"script", "style", "noscript", "head"}
#: 空元素：没有结束标签，不能参与层级计数
VOID_TAGS = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col"}


class Scrubber(HTMLParser):
    """按事件重新输出 HTML：标签原样保留，文本替换成占位符。

    保留前 ``cards`` 张卡片，之后跳到总数元素再收尾——夹具要小，但必须带上
    解析器真正依赖的那几个锚点。
    """

    def __init__(self, *, cards: int) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.cards = cards
        self.done = False
        self._drop_depth = 0
        self._capture_total = 0

    @staticmethod
    def _render(tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> str:
        rendered = "".join(f' {name}="{value or ""}"' for name, value in attrs)
        return f"<{tag}{rendered}{'/' if self_closing else ''}>"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            if not self._drop_depth and not self.done:
                self.parts.append(self._render(tag, attrs, self_closing=False))
            return
        if tag in DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or self.done:
            return

        classes = dict(attrs).get("class") or ""
        if "search-pagination-total" in classes:
            self._capture_total += 1
        self.parts.append(self._render(tag, attrs, self_closing=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._drop_depth or self.done:
            return
        self.parts.append(self._render(tag, attrs, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if tag in DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth or self.done:
            return
        if self._capture_total and tag == "span":
            self.parts.append(f"</{tag}>")
            self._capture_total -= 1
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._drop_depth or self.done:
            return
        text = data.strip()
        if not text:
            return
        if self._capture_total:  # 总数形如「共255部」，数字要保留
            self.parts.append(text)
            return
        self.parts.append(PLACEHOLDER if not NUMERIC.match(text) else text)

    def render(self, trailer: str = "") -> str:
        return "".join(self.parts) + trailer


PROVENANCE = (
    "<!-- 由 scripts/record_web_fixture.py 从线上页面录制：标签结构与数字保持原样，可读文本替换为「示例」 -->\n"
)
CARD_MARKER = 'class="title-truncate tags'
ALBUM_LINK = re.compile(r'href="/album/\d+')
TOTAL_TEXT = re.compile(r'class="search-pagination-total"[^>]*>([\s\S]{0,120}?)</span>')
PAGINATION = (
    '<ul class="pagination"><li class="active"><span>1</span></li>'
    '<li><a href="?page=2">2</a></li></ul></div></body></html>'
)


def scrub_search(html: str, *, cards: int) -> str:
    """截取结果区里的前 ``cards`` 张卡片，并把总数元素原样带在末尾。

    页面其余部分（导航、侧栏、弹窗）与解析无关，一并丢弃，夹具因此只有几 KB。
    """
    starts = [match.start() for match in re.finditer(re.escape(CARD_MARKER), html)]
    if len(starts) > cards:
        # 每张卡片以「封面链接」开头、以「标签容器」结尾；要切在第 cards+1 张的封面链接之前
        limit = starts[cards]
        links = [match.start() for match in ALBUM_LINK.finditer(html) if match.start() < limit]
        cut = links[-1] if links else limit
    else:
        cut = len(html)

    total = TOTAL_TEXT.search(html)
    total_span = f'<span class="search-pagination-total">{total.group(1).strip() if total else ""}</span>'
    begin = html.rfind('class="well well-sm"', 0, cut)
    begin = begin if begin != -1 else 0

    scrubber = Scrubber(cards=cards)
    scrubber.feed(html[begin:cut])
    scrubber.close()
    return PROVENANCE + "<html><body>" + scrubber.render(total_span + PAGINATION) + "</body></html>"


def scrub_error(html: str) -> str:
    """错误页只需要 fieldset 提示这一小段，连同它的原始文案一起保留。"""
    match = re.search(r"<fieldset[\s\S]{0,600}?</fieldset>", html)
    intro = '<html><body><div class="well well-sm">'
    card = '<a href="/album/1472715/slug"><img data-original="https://cdn.example/1472715_3x4.jpg"></a>'
    return f"{intro}{match.group(0) if match else ''}{card}</div></body></html>"


def scrub_album(html: str) -> str:
    """详情页骨架：og:url 与 <title> 足以覆盖「搜索被重定向」这条分支。"""
    og = re.search(r"<meta[^>]*og:url[^>]*>", html, re.I)
    return (
        "<html><head>"
        "<title>示例标题 | 站点</title>"
        f"{og.group(0) if og else ''}"
        '</head><body><div class="well well-sm"></div></body></html>'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="可用的网页端地址，例如 https://example.domain")
    parser.add_argument("--query", default="MANA", help="搜索词（只影响页面形状，不进夹具文本）")
    parser.add_argument("--cards", type=int, default=3, help="保留的卡片数")
    args = parser.parse_args()

    session = creq.Session(impersonate="chrome")
    base = args.base_url.rstrip("/")

    search = session.get(
        f"{base}/search/photos",
        params={"main_tag": 0, "search_query": args.query, "page": 1, "o": "mr", "t": "a"},
        timeout=40,
    )
    search.raise_for_status()
    (FIXTURES / "web_search.html").write_text(scrub_search(search.text, cards=args.cards), encoding="utf-8")

    error = session.get(
        f"{base}/search/photos",
        params={"main_tag": 0, "search_query": "a", "page": 1, "o": "mr", "t": "a"},
        timeout=40,
    )
    (FIXTURES / "web_error.html").write_text(scrub_error(error.text), encoding="utf-8")

    album = session.get(f"{base}/album/1472715", timeout=40, allow_redirects=True)
    (FIXTURES / "web_album.html").write_text(scrub_album(album.text), encoding="utf-8")

    for name in ("web_search.html", "web_error.html", "web_album.html"):
        path = FIXTURES / name
        print(f"wrote {path.name}: {path.stat().st_size} 字节")


if __name__ == "__main__":
    main()
