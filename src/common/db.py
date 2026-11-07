"""MySQL 连接与事务管理。

关键约束：
    - 数据库口令只从 Secrets Manager 获取，任何配置项、代码、配置文件中
      都不允许出现明文口令（见 :mod:`common.secrets`）。
    - 连接在进程内复用：Lambda 容器会被多次调用，省去每次握手的开销。
    - 连接不是线程安全的，因此事务期间持锁串行执行。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from .config import get_settings
from .secrets import get_secret

logger = logging.getLogger(__name__)

# 连接复用状态。Lambda 单个容器同一时刻通常只处理一个请求，
# 这里的锁用于覆盖扩展场景下的多线程访问，保证连接不会被并发使用。
# 类型参数 DictCursor 与连接建立时的 cursorclass 保持一致，
# 这样 fetchone()/fetchall() 才会被推断为 dict 而不是 tuple。
_connection: Connection[DictCursor] | None = None
# 必须用 RLock：transaction() 先持锁，再调用 get_connection()，后者会再次
# 获取同一把锁。用普通 Lock 时首次请求就会自锁，表现为 Lambda 一直超时。
_connection_lock = threading.RLock()


class DatabaseError(RuntimeError):
    """数据库访问失败。"""


def get_connection() -> Connection[DictCursor]:
    """获取可用的数据库连接，必要时建立或重建连接。"""
    global _connection

    with _connection_lock:
        if _connection is None:
            _connection = _create_connection()
        else:
            try:
                # 连接可能已被服务端或空闲超时断开，ping 会按需自动重连
                _connection.ping(reconnect=True)
            except pymysql.MySQLError:
                logger.warning("数据库连接已失效，正在重建")
                _close_quietly(_connection)
                _connection = _create_connection()
        return _connection


@contextmanager
def transaction() -> Iterator[Connection[DictCursor]]:
    """在事务中执行一段数据库操作，正常结束提交、异常回滚。

    用法::

        with transaction() as conn, conn.cursor() as cursor:
            cursor.execute(sql, params)

    Raises:
        DatabaseError: 连接建立失败。
    """
    with _connection_lock:
        conn = get_connection()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def close_connection() -> None:
    """关闭进程内的数据库连接（测试或容器回收时使用）。"""
    global _connection
    with _connection_lock:
        if _connection is not None:
            _close_quietly(_connection)
            _connection = None


def _create_connection() -> Connection[DictCursor]:
    """读取配置与密钥并建立新连接。"""
    settings = get_settings()
    credentials = get_secret(settings.db_secret_arn)

    # 口令必须来自密钥，缺失说明密钥内容不符合约定，直接失败而不是降级
    password = credentials.get("password")
    if not isinstance(password, str) or not password:
        raise DatabaseError("密钥中缺少 password 字段")

    # 用户名优先取环境变量，其次取密钥中的 username 字段
    username = settings.db_user or credentials.get("username")
    if not isinstance(username, str) or not username:
        raise DatabaseError("无法确定数据库用户名：请配置 DB_USER 或在密钥中提供 username")

    try:
        connection = pymysql.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=username,
            password=password,
            database=settings.db_name,
            charset="utf8mb4",
            # DictCursor 让查询结果直接是字典，便于序列化成 JSON
            cursorclass=DictCursor,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
            # 每个 handler 只做一次原子操作，交由 transaction() 显式提交
            autocommit=False,
        )
    except pymysql.MySQLError as exc:
        # 不把连接串、口令等敏感信息写进日志
        logger.error(
            "连接数据库失败 host=%s port=%s db=%s",
            settings.db_host,
            settings.db_port,
            settings.db_name,
        )
        raise DatabaseError(f"连接数据库失败: {exc}") from exc

    logger.info(
        "已建立数据库连接 host=%s port=%s db=%s",
        settings.db_host,
        settings.db_port,
        settings.db_name,
    )
    return connection


def _close_quietly(connection: Connection[DictCursor]) -> None:
    """关闭连接并吞掉关闭阶段的异常——关闭失败不应影响主流程。"""
    try:
        connection.close()
    except pymysql.MySQLError:
        logger.debug("关闭数据库连接时出现异常", exc_info=True)


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """执行查询并返回单行，无结果时返回 ``None``。

    表名等标识符由调用方拼接（必须已通过校验），值一律走占位符。
    """
    with transaction() as conn, conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """执行查询并返回全部结果行。"""
    with transaction() as conn, conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    """执行写操作，返回受影响的行数。"""
    with transaction() as conn, conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount
