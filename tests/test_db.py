"""数据库连接与事务测试。

连接建立过程被替换为假实现，测试不会连向任何真实数据库。
"""

from __future__ import annotations

from typing import Any

import pytest

from common import db
from common.config import get_settings
from common.db import DatabaseError, close_connection, get_connection, transaction
from common.secrets import reset_cache


class FakeDriverConnection:
    """替身驱动连接，模拟 pymysql 的最小接口。"""

    def __init__(self, *, fail_ping: bool = False) -> None:
        self.fail_ping = fail_ping
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def ping(self, reconnect: bool = False) -> None:
        del reconnect
        if self.fail_ping:
            raise db.pymysql.MySQLError("server has gone away")
        if self.closed:
            raise db.pymysql.MySQLError("closed")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _patch_connect(monkeypatch: pytest.MonkeyPatch, connection: Any) -> list[dict[str, Any]]:
    """替换 pymysql.connect，并返回用于断言的调用记录。"""
    calls: list[dict[str, Any]] = []

    def _connect(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return connection

    monkeypatch.setattr(db.pymysql, "connect", _connect)
    return calls


def _patch_secret(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    """替换密钥读取，避免访问 Secrets Manager。"""
    monkeypatch.setattr(db, "get_secret", lambda arn: payload)


def test_connection_password_comes_from_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """口令必须来自密钥，而不是任何常量。"""
    connection = FakeDriverConnection()
    calls = _patch_connect(monkeypatch, connection)
    _patch_secret(monkeypatch, {"username": "u", "password": "从密钥来"})

    get_connection()

    assert calls[0]["password"] == "从密钥来"
    assert calls[0]["host"] == get_settings().db_host
    assert calls[0]["autocommit"] is False


def test_connection_uses_dict_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """结果需要直接是字典，便于序列化为 JSON。"""
    calls = _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    get_connection()

    assert calls[0]["cursorclass"] is db.pymysql.cursors.DictCursor


def test_connection_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一容器内不应重复建立连接。"""
    calls = _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    get_connection()
    get_connection()

    assert len(calls) == 1


def test_stale_connection_is_rebuilt(monkeypatch: pytest.MonkeyPatch) -> None:
    """连接被服务端断开后应重建，而不是把错误抛给调用方。"""
    stale = FakeDriverConnection(fail_ping=True)
    fresh = FakeDriverConnection()
    calls = _patch_connect(monkeypatch, stale)
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    get_connection()
    # 让下一次 ping 失败，触发重建
    calls.clear()
    rebuilt = _patch_connect(monkeypatch, fresh)
    get_connection()

    assert len(rebuilt) == 1


def test_missing_password_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥内容不符合约定时必须失败，不能用空口令连库。"""
    _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"username": "u"})

    with pytest.raises(DatabaseError, match="password"):
        get_connection()


def test_username_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB_USER 已配置时应优先使用环境变量。"""
    calls = _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"password": "p"})

    get_connection()

    assert calls[0]["user"] == get_settings().db_user


def test_no_username_available_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """无法确定用户名时必须失败。"""
    # 环境变量与密钥里都没有用户名才是这个场景
    monkeypatch.delenv("DB_USER", raising=False)
    get_settings.cache_clear()

    _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"password": "p"})

    with pytest.raises(DatabaseError, match="用户名"):
        get_connection()


def test_driver_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """驱动层错误不应直接泄漏给上层。"""

    def _connect(**kwargs: Any) -> Any:
        del kwargs
        raise db.pymysql.MySQLError('(2003, "Can\'t connect")')

    monkeypatch.setattr(db.pymysql, "connect", _connect)
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    with pytest.raises(DatabaseError, match="连接数据库失败"):
        get_connection()


def test_failed_connect_leaves_no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """建立失败后不应留下半初始化状态。"""
    monkeypatch.setattr(
        db.pymysql,
        "connect",
        lambda **kwargs: (_ for _ in ()).throw(db.pymysql.MySQLError("boom")),
    )
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    with pytest.raises(DatabaseError):
        get_connection()

    assert db._connection is None  # noqa: SLF001 - 直接断言内部状态


def test_transaction_commits_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """with 正常退出应提交。"""
    connection = FakeDriverConnection()
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    with transaction():
        pass

    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_transaction_rolls_back_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """with 内抛异常应回滚并把异常继续抛出。"""
    connection = FakeDriverConnection()
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="业务失败"), transaction():
        raise RuntimeError("业务失败")

    assert connection.rollbacks == 1
    assert connection.commits == 0


def test_close_connection_forces_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """close_connection 应清空缓存的连接。"""
    calls = _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    get_connection()
    close_connection()
    get_connection()

    assert len(calls) == 2


def test_close_connection_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """重复调用 close_connection 不应报错。"""
    close_connection()
    close_connection()


def test_connection_cache_is_cleared_between_tests() -> None:
    """conftest 的夹具应保证缓存干净。"""
    assert db._connection is None  # noqa: SLF001 - 直接断言内部状态


def test_secret_cache_is_reset_between_tests() -> None:
    """夹具应重置所有进程级缓存，避免测试互相污染。"""
    from common.secrets import _cache

    assert _cache == {}


def test_no_real_aws_credentials_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试环境不应依赖任何真实 AWS 凭证。"""
    _patch_connect(monkeypatch, FakeDriverConnection())
    _patch_secret(monkeypatch, {"username": "u", "password": "p"})

    get_connection()

    # 只要走到这里就说明没有去请求 Secrets Manager
    assert db._connection is not None  # noqa: SLF001 - 直接断言内部状态


def test_reset_cache_clears_state() -> None:
    """reset_cache 应清空所有已缓存的密钥。"""
    reset_cache()

    from common.secrets import _cache

    assert _cache == {}
