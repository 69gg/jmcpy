"""端点池：解析、缓存与降级。

端点源的解析用真实录制的响应作向量；发布页解析用与线上结构一致的合成 HTML。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jmcpy.endpoints import (
    AsyncEndpointPool,
    EndpointPool,
    EndpointSet,
    apply_to_settings,
    is_domain_like,
    parse_endpoint_feed,
    parse_publish_page,
    read_cache,
    write_cache,
)
from jmcpy.errors import NetworkIssue
from jmcpy.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"

PUBLISH_HTML = """
<html><body>
  <div class="international"><span>18comio.sbs</span>
  <span>18comio.sbs</span>
  </div>
  <div class="main"><p>APP 下載</p>
  <a href="https://jmcomic-zzz.org/stray/">jmcomic-zzz.org</a>
  <span>comic18j-ada.space</span>
  <span>不是域名</span>
  <span>3.14</span>
  </div>
</body></html>
"""


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(home=tmp_path, backoff_jitter=0.0, **overrides)  # type: ignore[arg-type]


def test_parse_endpoint_feed_uses_recorded_response() -> None:
    raw = (FIXTURES / "endpoint_feed.txt").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / "endpoint_feed_expected.json").read_text(encoding="utf-8"))

    hosts = parse_endpoint_feed(raw)

    assert hosts
    assert set(expected["Server"]) <= set(hosts)
    # jm3_Server 里的备用线路也应被收进来
    assert any(host not in expected["Server"] for host in hosts)
    assert len(set(hosts)) == len(hosts)


def test_parse_endpoint_feed_tolerates_unknown_shape() -> None:
    from jmcpy.constants import ENDPOINT_FEED_SALT
    from jmcpy.crypto import seal_payload

    payload = seal_payload(json.dumps({"Server": ["a.example"]}), "", ENDPOINT_FEED_SALT)

    assert parse_endpoint_feed(payload) == ("a.example",)


def test_parse_publish_page_keeps_domains_without_keyword() -> None:
    hosts = parse_publish_page(PUBLISH_HTML)

    assert hosts.index("18comio.sbs") < hosts.index("jmcomic-zzz.org") < hosts.index("comic18j-ada.space")
    assert len(hosts) == len(set(hosts))
    assert "不是域名" not in hosts


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("18comio.sbs", True),
        ("www.cdngwc.cc", True),
        ("https://cdn-msp.jmapiproxy2.cc/", True),
        ("3.14", False),
        ("localhost", False),
        ("has space.com", False),
        ("path/segment", False),
        ("", False),
    ],
)
def test_is_domain_like(text: str, expected: bool) -> None:
    assert is_domain_like(text) is expected


def test_cache_round_trip_and_ttl(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "endpoints.json"
    endpoints = EndpointSet(mobile=("a.example",), cdn=("c.example",), web=("w.example",), updated_at=time.time())

    write_cache(path, endpoints)

    assert read_cache(path, ttl=3600) == endpoints
    assert read_cache(path, ttl=0) == endpoints  # ttl<=0 表示永不过期


def test_cache_expires(tmp_path: Path) -> None:
    path = tmp_path / "endpoints.json"
    stale = EndpointSet(mobile=("a.example",), cdn=(), web=(), updated_at=time.time() - 7200)

    write_cache(path, stale)

    assert read_cache(path, ttl=3600) is None
    assert read_cache(path, ttl=0) == stale


def test_cache_ignores_missing_broken_and_empty(tmp_path: Path) -> None:
    path = tmp_path / "endpoints.json"
    assert read_cache(path, ttl=60) is None

    path.write_text("{ not json", encoding="utf-8")
    assert read_cache(path, ttl=60) is None

    path.write_text(json.dumps({"updated_at": time.time(), "mobile": [], "cdn": [], "web": []}), encoding="utf-8")
    assert read_cache(path, ttl=60) is None


def test_resolve_falls_back_to_builtin_when_disabled(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auto_update_endpoints=False)
    pool = EndpointPool(settings, fetch=lambda url: pytest.fail("关闭自动更新后不应发起请求"))

    resolved = pool.resolve()

    assert resolved.mobile == settings.mobile_endpoints
    assert resolved.web == ()


def test_resolve_falls_back_to_builtin_without_fetcher(tmp_path: Path) -> None:
    pool = EndpointPool(make_settings(tmp_path))

    assert pool.resolve().mobile == Settings().mobile_endpoints


def test_resolve_uses_fetched_endpoints_and_writes_cache(tmp_path: Path) -> None:
    feed = (FIXTURES / "endpoint_feed.txt").read_text(encoding="utf-8")
    requested: list[str] = []

    def fetch(url: str) -> str:
        requested.append(url)
        if url.endswith(".txt"):
            return feed
        return PUBLISH_HTML

    settings = make_settings(tmp_path)
    pool = EndpointPool(settings, fetch=fetch)

    resolved = pool.resolve()

    assert resolved.mobile
    assert resolved.mobile[0] == "www.cdnhjk.net"
    assert resolved.web == ("18comio.sbs", "jmcomic-zzz.org", "comic18j-ada.space")
    assert resolved.cdn == settings.cdn_endpoints
    assert pool.cache_path.is_file()
    assert len(requested) == 2


def test_resolve_serves_from_cache_without_refetch(tmp_path: Path) -> None:
    publish_only = EndpointSet(mobile=(), cdn=(), web=("cached.example",), updated_at=time.time())
    settings = make_settings(tmp_path)
    pool = EndpointPool(settings, fetch=lambda url: pytest.fail("命中缓存时不应再发请求"))
    write_cache(pool.cache_path, publish_only)

    resolved = pool.resolve()

    assert resolved.web == ("cached.example",)
    assert resolved.mobile == settings.mobile_endpoints


def test_resolve_refresh_bypasses_cache(tmp_path: Path) -> None:
    pool = None
    settings = make_settings(tmp_path)
    stale = EndpointSet(mobile=(), cdn=(), web=("cached.example",), updated_at=time.time())
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return "" if url.endswith(".txt") else PUBLISH_HTML

    pool = EndpointPool(settings, fetch=fetch)
    write_cache(pool.cache_path, stale)

    resolved = pool.resolve(refresh=True)

    assert calls
    assert resolved.web == ("18comio.sbs", "jmcomic-zzz.org", "comic18j-ada.space")


def test_resolve_degrades_when_all_sources_fail(tmp_path: Path) -> None:
    def fetch(url: str) -> str:
        raise NetworkIssue("网络不可达", url=url)

    settings = make_settings(tmp_path)
    pool = EndpointPool(settings, fetch=fetch)

    resolved = pool.resolve()

    assert resolved.mobile == settings.mobile_endpoints
    assert not pool.cache_path.exists()


def test_resolve_skips_broken_feed_and_tries_next(tmp_path: Path) -> None:
    attempts: list[str] = []

    def fetch(url: str) -> str:
        attempts.append(url)
        if url.endswith(".txt") and len(attempts) == 1:
            raise NetworkIssue("第一个源挂了", url=url)
        if url.endswith(".txt"):
            return (FIXTURES / "endpoint_feed.txt").read_text(encoding="utf-8")
        return PUBLISH_HTML

    pool = EndpointPool(make_settings(tmp_path), fetch=fetch)

    resolved = pool.resolve()

    assert len([url for url in attempts if url.endswith(".txt")]) == 2
    assert resolved.mobile


def test_apply_to_settings_keeps_explicit_overrides(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, mobile_endpoints=("mine.example",))

    updated = apply_to_settings(settings, EndpointSet(mobile=("found.example",), cdn=(), web=("w.example",)))

    assert updated.mobile_endpoints == ("found.example",)
    assert updated.web_endpoints == ("w.example",)
    assert updated.cdn_endpoints == settings.cdn_endpoints


async def test_async_pool_resolves_like_sync(tmp_path: Path) -> None:
    feed = (FIXTURES / "endpoint_feed.txt").read_text(encoding="utf-8")

    async def fetch(url: str) -> str:
        return feed if url.endswith(".txt") else PUBLISH_HTML

    settings = make_settings(tmp_path)
    pool = AsyncEndpointPool(settings, fetch=fetch)

    resolved = await pool.resolve()

    assert resolved.mobile[0] == "www.cdnhjk.net"
    assert resolved.web == ("18comio.sbs", "jmcomic-zzz.org", "comic18j-ada.space")
    assert pool.cache_path.is_file()


async def test_async_pool_degrades_on_failure(tmp_path: Path) -> None:
    async def fetch(url: str) -> str:
        raise NetworkIssue("网络不可达", url=url)

    settings = make_settings(tmp_path)
    pool = AsyncEndpointPool(settings, fetch=fetch)

    assert (await pool.resolve()).mobile == settings.mobile_endpoints
