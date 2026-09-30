"""pytest 全局配置。

标注 ``live`` 的用例需要真实网络访问，默认跳过；设置环境变量 ``JMCPY_LIVE=1`` 后才会执行。
"""

from __future__ import annotations

import os

import pytest

LIVE_ENV_VAR = "JMCPY_LIVE"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """未显式开启时，跳过所有 live 用例。"""
    if os.environ.get(LIVE_ENV_VAR) == "1":
        return

    skip = pytest.mark.skip(reason=f"需要真实网络访问，设置 {LIVE_ENV_VAR}=1 后启用")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
