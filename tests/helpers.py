"""测试替身与事件构造工具。

这里的类只用于把数据库与 API Gateway 事件替换成可控的内存实现，
不涉及任何真实网络调用或凭据。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# 一组不含任何真实值的测试用配置
TEST_ENV: dict[str, str] = {
    "DB_HOST": "db.example.internal",
    "DB_PORT": "3306",
    "DB_NAME": "appdb",
    "DB_USER": "app_user",
    "DB_SECRET_ARN": "arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:db-AbCdEf",
    "LOG_LEVEL": "DEBUG",
}


class FakeCursor:
    """替身游标：记录执行过的 SQL，并按顺序返回预设结果。"""

    def __init__(
        self,
        *,
        results: list[Any] | None = None,
        lastrowid: int = 1,
        rowcount: int = 1,
    ) -> None:
        self.results = list(results or [])
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._index = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """记录 SQL 与参数，返回受影响行数。"""
        self.executed.append((sql, params))
        return self.rowcount

    def fetchone(self) -> Any:
        """返回下一行，没有更多行时返回 None。"""
        if self._index >= len(self.results):
            return None
        value = self.results[self._index]
        self._index += 1
        return value

    def fetchall(self) -> list[Any]:
        """返回剩余的全部行。"""
        remaining = self.results[self._index :]
        self._index = len(self.results)
        return remaining

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class FakeConnection:
    """替身连接：持有游标并记录提交与回滚次数。"""

    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None

    def ping(self, reconnect: bool = False) -> None:
        """模拟健康检查，测试中恒定认为连接可用。"""
        del reconnect


def make_transaction(connection: FakeConnection) -> Any:
    """构造一个可替换 ``common.db.transaction`` 的上下文管理器。

    行为与 :func:`common.db.transaction` 保持一致：正常结束提交、异常回滚，
    这样测试才能断言到真实的提交/回滚次数。传入连接而不是游标，
    是为了让断言用的 ``FakeConnection`` 与被真正执行的那个是同一个对象。
    """

    @contextmanager
    def _transaction() -> Iterator[FakeConnection]:
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        connection.commit()

    return _transaction


def api_event(
    method: str,
    path: str,
    *,
    path_parameters: dict[str, str] | None = None,
    query_parameters: dict[str, str] | None = None,
    body: Any = None,
    version: str = "2.0",
) -> dict[str, Any]:
    """构造 API Gateway 事件，默认使用 payload 格式 2.0。

    Args:
        method: HTTP 方法。
        path: 请求路径。
        path_parameters: 路径参数。
        query_parameters: 查询参数。
        body: 请求体；传字典会被序列化成 JSON 字符串。
        version: 事件格式版本，"2.0" 为 HTTP API，"1.0" 为 REST API。
    """
    payload: dict[str, Any] = {
        "version": version,
        "rawPath": path,
        "headers": {"content-type": "application/json"},
    }

    if body is not None:
        payload["body"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)

    if version == "2.0":
        payload["requestContext"] = {"http": {"method": method, "path": path}}
    else:
        payload["httpMethod"] = method
        payload["path"] = path

    if path_parameters:
        payload["pathParameters"] = path_parameters
    if query_parameters:
        payload["queryStringParameters"] = query_parameters

    return payload
