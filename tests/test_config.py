"""配置模块测试。"""

from __future__ import annotations

import dataclasses

import pytest

from common.config import ConfigError, get_settings
from helpers import TEST_ENV


def test_settings_parsed_from_env() -> None:
    """环境变量齐全时应解析出预期的配置项。"""
    settings = get_settings()
    assert settings.db_host == TEST_ENV["DB_HOST"]
    assert settings.db_port == 3306
    assert settings.db_name == TEST_ENV["DB_NAME"]
    assert settings.db_user == TEST_ENV["DB_USER"]
    assert settings.db_secret_arn == TEST_ENV["DB_SECRET_ARN"]
    assert settings.log_level == "DEBUG"


def test_settings_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """第二次读取应命中缓存，不受环境变量后续变化影响。"""
    first = get_settings()
    monkeypatch.setenv("DB_HOST", "another.host")
    second = get_settings()
    assert first is second

    # 显式清缓存后才能读到新值
    get_settings.cache_clear()
    assert get_settings().db_host == "another.host"


def test_settings_are_immutable() -> None:
    """配置快照应为不可变对象，避免运行期被意外修改。"""
    settings = get_settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.db_host = "other"  # type: ignore[misc]


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_NAME", "DB_SECRET_ARN"])
def test_missing_required_setting_raises(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """必需配置缺失必须失败，而不是静默使用空值连库。"""
    monkeypatch.delenv(missing, raising=False)
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match=missing):
        get_settings()


def test_non_numeric_port_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """端口必须是整数。"""
    monkeypatch.setenv("DB_PORT", "not-a-port")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="DB_PORT"):
        get_settings()


@pytest.mark.parametrize("port", ["0", "70000"])
def test_out_of_range_port_rejected(monkeypatch: pytest.MonkeyPatch, port: str) -> None:
    """端口超出 1~65535 范围必须失败。"""
    monkeypatch.setenv("DB_PORT", port)
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="DB_PORT"):
        get_settings()


def test_port_defaults_to_3306(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB_PORT 可选，默认 3306。"""
    monkeypatch.delenv("DB_PORT", raising=False)
    get_settings.cache_clear()
    assert get_settings().db_port == 3306


def test_db_user_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB_USER 可选，留空表示从密钥的 username 字段读取。"""
    monkeypatch.delenv("DB_USER", raising=False)
    get_settings.cache_clear()
    assert get_settings().db_user == ""


def test_settings_field_names_are_stable() -> None:
    """确保 dataclass 字段集合稳定，防止误删配置项。"""
    names = {field.name for field in dataclasses.fields(get_settings())}
    assert names == {
        "db_host",
        "db_port",
        "db_name",
        "db_user",
        "db_secret_arn",
        "log_level",
    }


def test_settings_types_are_correct() -> None:
    """配置项类型应符合声明。"""
    settings = get_settings()
    assert isinstance(settings.db_port, int)
    assert isinstance(settings.db_secret_arn, str)
