"""运行时配置。

所有配置一律来自环境变量，代码中不允许出现任何口令、AccessKey 等敏感值。
非敏感的基础设施参数（主机、端口、库名）由 CloudFormation 参数注入环境变量；
敏感的数据库口令一律由 Secrets Manager 提供，见 :mod:`common.secrets`。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

# SQL 标识符（表名）允许的字符集，用于防止把不可信内容拼进 SQL
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(RuntimeError):
    """必需配置缺失或取值非法。"""


def _require(name: str) -> str:
    """读取必需的环境变量，缺失或为空时抛出 :class:`ConfigError`。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"缺少必需的环境变量: {name}")
    return value


def _optional(name: str, default: str) -> str:
    """读取可选的环境变量，缺失时返回默认值。"""
    return os.environ.get(name, "").strip() or default


def _table_name() -> str:
    """读取并校验表名。

    表名无法通过 SQL 占位符传入，只能拼接，因此必须先确认它是合法标识符。
    """
    value = _optional("TABLE_NAME", "items")
    if not _IDENTIFIER_PATTERN.match(value):
        raise ConfigError(f"TABLE_NAME 不是合法的 SQL 标识符: {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    """一次读取到的配置快照。

    Attributes:
        db_host: 数据库主机名或 IP。
        db_port: 数据库端口。
        db_name: 数据库名。
        db_user: 数据库用户名；留空时回退到密钥中的 ``username`` 字段。
        db_secret_arn: Secrets Manager 中数据库凭据的 ARN。
        table_name: 业务表名，已通过标识符合法性校验。
        log_level: 日志级别。
    """

    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_secret_arn: str
    table_name: str
    log_level: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取配置并缓存。

    Lambda 容器会被多次复用，缓存配置可以避免每次调用都读环境变量并重复校验。
    测试中修改环境变量后需要调用 ``get_settings.cache_clear()``。
    """
    port_raw = _optional("DB_PORT", "3306")
    try:
        db_port = int(port_raw)
    except ValueError as exc:
        raise ConfigError(f"DB_PORT 必须是整数: {port_raw!r}") from exc
    if not 0 < db_port < 65536:
        raise ConfigError(f"DB_PORT 超出取值范围: {db_port}")

    return Settings(
        db_host=_require("DB_HOST"),
        db_port=db_port,
        db_name=_require("DB_NAME"),
        db_user=_optional("DB_USER", ""),
        db_secret_arn=_require("DB_SECRET_ARN"),
        table_name=_table_name(),
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
    )
