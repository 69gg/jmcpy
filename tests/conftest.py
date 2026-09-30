"""pytest 全局配置与共享夹具。

标注 ``live`` 的用例需要真实网络访问，默认跳过；设置环境变量 ``JMCPY_LIVE=1`` 后才会执行。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

LIVE_ENV_VAR = "JMCPY_LIVE"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """未显式开启时，跳过所有 live 用例。"""
    if os.environ.get(LIVE_ENV_VAR) == "1":
        return

    skip = pytest.mark.skip(reason=f"需要真实网络访问，设置 {LIVE_ENV_VAR}=1 后启用")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(name="load_fixture")
def load_fixture_fixture() -> Callable[[str], Any]:
    """读取 ``tests/fixtures`` 下的 JSON 夹具。"""

    def load(name: str) -> Any:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture(name="load_text")
def load_text_fixture() -> Callable[[str], str]:
    """读取 ``tests/fixtures`` 下的文本夹具。"""

    def load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    return load
