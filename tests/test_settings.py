"""配置读取与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from jmcpy.constants import DEFAULT_MOBILE_ENDPOINTS, DEFAULT_TIMEOUT
from jmcpy.enums import Backend, RetryMode
from jmcpy.errors import ConfigurationError
from jmcpy.settings import Settings, cache_dir, config_dir


def test_defaults_are_usable_out_of_the_box() -> None:
    settings = Settings()

    assert settings.backend is Backend.CURL_CFFI
    assert settings.timeout == DEFAULT_TIMEOUT
    assert settings.mobile_endpoints == DEFAULT_MOBILE_ENDPOINTS
    assert settings.retry_mode is RetryMode.RETRY_FIRST
    assert settings.auto_route is True
    assert settings.cookies == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"timeout": 0},
        {"timeout": -1},
        {"image_timeout": 0},
        {"retry_times": -1},
        {"concurrency": 0},
        {"backoff_jitter": 1.0},
        {"mobile_endpoints": ()},
        {"cdn_endpoints": ()},
    ],
)
def test_invalid_values_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        Settings(**overrides)  # type: ignore[arg-type]


def test_from_mapping_overrides_fields() -> None:
    settings = Settings.from_mapping({"timeout": 5, "retry_mode": RetryMode.ROTATE_FIRST})

    assert settings.timeout == 5
    assert settings.retry_mode is RetryMode.ROTATE_FIRST


def test_from_mapping_rejects_unknown_field() -> None:
    with pytest.raises(ConfigurationError, match="未知配置项: timout"):
        Settings.from_mapping({"timout": 5})


def test_from_env_reads_documented_variables(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "JMCPY_TIMEOUT": "7.5",
            "JMCPY_IMAGE_TIMEOUT": "90",
            "JMCPY_RETRY_TIMES": "5",
            "JMCPY_RETRY_MODE": "rotate_first",
            "JMCPY_BACKEND": "httpx",
            "JMCPY_CONCURRENCY": "3",
            "JMCPY_MOBILE_ENDPOINTS": "a.example, b.example",
            "JMCPY_CDN_ENDPOINTS": "c.example",
            "JMCPY_COOKIES": "AVS=token; other=1",
            "JMCPY_PROXY": "http://127.0.0.1:7890",
            "JMCPY_HOME": str(tmp_path),
            "JMCPY_USE_KEYRING": "false",
        }
    )

    assert settings.timeout == 7.5
    assert settings.image_timeout == 90
    assert settings.retry_times == 5
    assert settings.retry_mode is RetryMode.ROTATE_FIRST
    assert settings.backend is Backend.HTTPX
    assert settings.concurrency == 3
    assert settings.mobile_endpoints == ("a.example", "b.example")
    assert settings.cdn_endpoints == ("c.example",)
    assert settings.cookies == {"AVS": "token", "other": "1"}
    assert settings.proxy == "http://127.0.0.1:7890"
    assert settings.resolved_home == tmp_path
    assert settings.use_keyring is False


def test_from_env_ignores_empty_values() -> None:
    settings = Settings.from_env({"JMCPY_TIMEOUT": "", "JMCPY_PROXY": ""})

    assert settings.timeout == DEFAULT_TIMEOUT
    assert settings.proxy is None


def test_from_env_reports_bad_value() -> None:
    with pytest.raises(ConfigurationError, match="JMCPY_TIMEOUT"):
        Settings.from_env({"JMCPY_TIMEOUT": "abc"})


def test_from_file_supports_both_table_styles(tmp_path: Path) -> None:
    top_level = tmp_path / "top.toml"
    top_level.write_text("timeout = 11\nretry_times = 2\n", encoding="utf-8")
    nested = tmp_path / "nested.toml"
    nested.write_text('[jmcpy]\ntimeout = 12\nmobile_endpoints = ["x.example"]\n', encoding="utf-8")

    assert Settings.from_file(top_level).timeout == 11
    assert Settings.from_file(top_level).retry_times == 2
    nested_settings = Settings.from_file(nested)
    assert nested_settings.timeout == 12
    assert nested_settings.mobile_endpoints == ("x.example",)


def test_from_file_converts_types(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'backend = "httpx"\nretry_mode = "rotate_first"\nretry_status = [500, 502]\nhome = "/tmp/jmcpy-home"\n',
        encoding="utf-8",
    )

    settings = Settings.from_file(path)

    assert settings.backend is Backend.HTTPX
    assert settings.retry_mode is RetryMode.ROTATE_FIRST
    assert settings.retry_status == frozenset({500, 502})
    assert settings.home == Path("/tmp/jmcpy-home")


def test_from_file_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("nope = 1\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="未知配置项: nope"):
        Settings.from_file(path)


def test_from_file_rejects_missing_and_broken_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="配置文件不存在"):
        Settings.from_file(tmp_path / "absent.toml")

    broken = tmp_path / "broken.toml"
    broken.write_text("timeout = [", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="解析失败"):
        Settings.from_file(broken)


def test_load_precedence_file_then_env_then_overrides(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("timeout = 10\nretry_times = 1\nconcurrency = 2\n", encoding="utf-8")

    settings = Settings.load(
        path,
        env={"JMCPY_TIMEOUT": "20", "JMCPY_CONCURRENCY": "4"},
        overrides={"timeout": 30},
    )

    assert settings.timeout == 30  # 显式参数最高
    assert settings.concurrency == 4  # 环境变量覆盖文件
    assert settings.retry_times == 1  # 只有文件提供


def test_load_uses_default_config_path_from_home(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("timeout = 42\n", encoding="utf-8")

    settings = Settings.load(env={"JMCPY_HOME": str(tmp_path)})

    assert settings.timeout == 42


def test_load_without_config_file_falls_back_to_defaults(tmp_path: Path) -> None:
    settings = Settings.load(env={"JMCPY_HOME": str(tmp_path)})

    assert settings.timeout == DEFAULT_TIMEOUT


def test_resolved_paths_follow_home(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path)

    assert settings.resolved_home == tmp_path
    assert settings.resolved_session_path == tmp_path / "session.json"
    assert settings.resolved_config_path == tmp_path / "config.toml"
    assert settings.resolved_cache_dir() == tmp_path / "cache"


def test_resolved_paths_use_platform_directories_by_default() -> None:
    settings = Settings()

    assert settings.resolved_session_path.name == "session.json"
    assert "jmcpy" in str(settings.resolved_home)
    assert "jmcpy" in str(settings.resolved_cache_dir())


def test_session_path_can_be_overridden_directly(tmp_path: Path) -> None:
    custom = tmp_path / "custom-session.json"

    settings = Settings(session_path=custom)

    assert settings.resolved_session_path == custom


def test_config_and_cache_dir_helpers_honour_home(tmp_path: Path) -> None:
    assert config_dir(tmp_path) == tmp_path
    assert cache_dir(tmp_path) == tmp_path / "cache"
    assert "jmcpy" in str(config_dir())
