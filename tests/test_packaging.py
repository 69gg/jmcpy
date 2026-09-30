"""打包契约：版本号单一来源与公共 API 完整性。"""

from __future__ import annotations

import tomllib
from pathlib import Path

import jmcpy

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"


def load_pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_version_is_declared_in_pyproject() -> None:
    """版本号写在 pyproject.toml 里，不再走 dynamic + 单独文件。"""
    document = load_pyproject()
    project = document["project"]
    assert isinstance(project, dict)

    assert project["version"] == "0.1.2"
    assert "version" not in set(project.get("dynamic", []))


def test_runtime_version_matches_pyproject() -> None:
    """运行时读的是安装后的元数据，必须与 pyproject 一致（防止改了一处忘了另一处）。"""
    document = load_pyproject()
    project = document["project"]
    assert isinstance(project, dict)

    assert jmcpy.__version__ == project["version"]


def test_version_comes_from_installed_metadata() -> None:
    from importlib.metadata import version

    assert jmcpy.__version__ == version("jmcpy")
    assert jmcpy.__version__ != "0.0.0+unknown", "测试环境里本包应当是已安装状态"


def test_public_api_is_complete_and_importable() -> None:
    missing = [name for name in jmcpy.__all__ if not hasattr(jmcpy, name)]

    assert missing == []
    assert len(jmcpy.__all__) == len(set(jmcpy.__all__)), "__all__ 不应有重复项"


def test_public_api_covers_documented_entry_points() -> None:
    """文档里出现的入口必须真的能 import。"""
    expected = {
        "AsyncClient",
        "AsyncMobileClient",
        "AsyncWebClient",
        "Client",
        "ChallengeBlocked",
        "ExportFormat",
        "Genre",
        "MobileClient",
        "RankingSpan",
        "SearchTarget",
        "Settings",
        "SortBy",
        "SubGenre",
        "TimeRange",
        "WebClient",
        "download_chapter",
        "download_chapter_async",
    }

    assert expected <= set(jmcpy.__all__)


def test_no_stale_version_module() -> None:
    """版本只有一处定义：不应再留着单独的版本文件。"""
    assert not (PROJECT_ROOT / "src" / "jmcpy" / "_version.py").exists()


def test_pyproject_extras_are_the_documented_ones() -> None:
    document = load_pyproject()
    project = document["project"]
    assert isinstance(project, dict)
    extras = project["optional-dependencies"]
    assert isinstance(extras, dict)

    assert set(extras) == {"httpx", "keyring", "all"}


def test_dev_tools_live_in_a_dependency_group() -> None:
    """开发工具放在依赖组里，uv run 才会默认装上并保留（放 optional-extra 会被清掉）。"""
    document = load_pyproject()
    groups = document.get("dependency-groups")
    assert isinstance(groups, dict)

    dev = " ".join(groups.get("dev", []))
    for tool in ("pytest", "mypy", "ruff", "httpx", "keyring"):
        assert tool in dev
