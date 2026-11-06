"""四个 Lambda 入口的集成级测试。

数据库层被替换为替身，因此这里验证的是：参数校验、路由分发、
SQL 组装、状态码映射——而不是真实 SQL 执行。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from common import db
from helpers import FakeConnection, FakeCursor, api_event, make_transaction


def _body(response: dict[str, Any]) -> Any:
    """解析响应体；204 等无响应体的情况返回 None。"""
    raw = response.get("body")
    return json.loads(raw) if isinstance(raw, str) and raw else None


def _patch_transaction(monkeypatch: pytest.MonkeyPatch, cursor: FakeCursor) -> FakeConnection:
    """把数据库事务替换为返回指定游标的替身，并返回可断言的连接。"""
    connection = FakeConnection(cursor)
    monkeypatch.setattr(db, "transaction", make_transaction(connection))
    return connection


# ---------------------------------------------------------------------------
# 新增
# ---------------------------------------------------------------------------
def test_create_returns_201(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法请求应写入数据并返回 201。"""
    from create import handler

    row = {"id": 1, "name": "示例", "description": None}
    cursor = FakeCursor(results=[row], lastrowid=1)
    connection = _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(api_event("POST", "/items", body={"name": "示例"}), None)

    assert response["statusCode"] == 201
    assert _body(response)["data"] == row
    assert connection.commits == 1
    assert "INSERT INTO items" in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("示例", None)


def test_create_trims_name_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """写入前应规范化用户输入。"""
    from create import handler

    cursor = FakeCursor(results=[{"id": 1, "name": "示例"}], lastrowid=1)
    _patch_transaction(monkeypatch, cursor)

    handler.lambda_handler(api_event("POST", "/items", body={"name": "  示例  "}), None)

    assert cursor.executed[0][1][0] == "示例"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": 123},
        {"name": None},
    ],
)
def test_create_rejects_missing_name(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """name 必填且必须是非空字符串。"""
    from create import handler

    response = handler.lambda_handler(api_event("POST", "/items", body=payload), None)

    assert response["statusCode"] == 400
    assert _body(response)["code"] == "VALIDATION_ERROR"


def test_create_rejects_oversized_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """超出列长度的姓名应被提前拒绝，而不是等数据库报错。"""
    from create import handler

    response = handler.lambda_handler(api_event("POST", "/items", body={"name": "x" * 256}), None)

    assert response["statusCode"] == 400


def test_create_rejects_non_string_description(monkeypatch: pytest.MonkeyPatch) -> None:
    """description 必须是字符串。"""
    from create import handler

    response = handler.lambda_handler(
        api_event("POST", "/items", body={"name": "ok", "description": 123}), None
    )

    assert response["statusCode"] == 400


def test_create_rejects_missing_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有请求体时应返回 400 而不是 500。"""
    from create import handler

    response = handler.lambda_handler(api_event("POST", "/items"), None)

    assert response["statusCode"] == 400


def test_create_maps_unexpected_error_to_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库故障应转成 500，且不泄漏内部细节。"""
    from create import handler

    def _boom() -> Any:
        raise RuntimeError("connection refused: secret_table")

    monkeypatch.setattr(db, "transaction", _boom)

    response = handler.lambda_handler(api_event("POST", "/items", body={"name": "x"}), None)

    assert response["statusCode"] == 500
    assert "secret_table" not in response["body"]


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def test_get_by_id_returns_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """带路径参数时返回单条记录。"""
    from read import handler

    row = {"id": 42, "name": "示例"}
    monkeypatch.setattr(db, "fetch_one", lambda sql, params=(): row)

    response = handler.lambda_handler(
        api_event("GET", "/items/42", path_parameters={"id": "42"}), None
    )

    assert response["statusCode"] == 200
    assert _body(response)["data"] == row


def test_get_missing_item_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """资源不存在时返回 404。"""
    from read import handler

    monkeypatch.setattr(db, "fetch_one", lambda sql, params=(): None)

    response = handler.lambda_handler(
        api_event("GET", "/items/404", path_parameters={"id": "404"}), None
    )

    assert response["statusCode"] == 404
    assert _body(response)["code"] == "NOT_FOUND"


def test_list_returns_pagination_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """列表接口应同时返回数据与总数。"""
    from read import handler

    rows = [{"id": 3, "name": "c"}, {"id": 2, "name": "b"}]

    def _fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert "LIMIT %s OFFSET %s" in sql
        assert params == (20, 0)
        return rows

    def _fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        assert "COUNT(*)" in sql
        return {"total": 42}

    monkeypatch.setattr(db, "fetch_all", _fetch_all)
    monkeypatch.setattr(db, "fetch_one", _fetch_one)

    response = handler.lambda_handler(api_event("GET", "/items"), None)

    assert response["statusCode"] == 200
    payload = _body(response)
    assert payload["data"] == rows
    assert payload["meta"] == {"total": 42, "limit": 20, "offset": 0}


def test_list_passes_limit_and_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """limit/offset 应透传到 SQL。"""
    from read import handler

    captured: dict[str, Any] = {}

    def _fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        captured["params"] = params
        return []

    def _fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return {"total": 0}

    monkeypatch.setattr(db, "fetch_all", _fetch_all)
    monkeypatch.setattr(db, "fetch_one", _fetch_one)

    handler.lambda_handler(
        api_event("GET", "/items", query_parameters={"limit": "5", "offset": "10"}), None
    )

    assert captured["params"] == (5, 10)


def test_list_rejects_invalid_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法分页参数应被拒绝。"""
    from read import handler

    response = handler.lambda_handler(
        api_event("GET", "/items", query_parameters={"limit": "-1"}), None
    )

    assert response["statusCode"] == 400


def test_get_rejects_invalid_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """路径 id 必须是正整数。"""
    from read import handler

    response = handler.lambda_handler(
        api_event("GET", "/items/abc", path_parameters={"id": "abc"}), None
    )

    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# 更新
# ---------------------------------------------------------------------------
def test_update_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法更新应返回更新后的完整记录。"""
    from update import handler

    row = {"id": 1, "name": "新名字", "description": "新描述"}
    cursor = FakeCursor(results=[row], rowcount=1)
    connection = _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event(
            "PUT",
            "/items/1",
            path_parameters={"id": "1"},
            body={"name": "新名字", "description": "新描述"},
        ),
        None,
    )

    assert response["statusCode"] == 200
    assert _body(response)["data"] == row
    assert connection.commits == 1

    sql, params = cursor.executed[0]
    assert "UPDATE items SET" in sql
    assert "name = %s" in sql
    assert "description = %s" in sql
    assert params == ("新名字", "新描述", 1)


def test_update_supports_partial_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """只提交部分字段时不应把缺失字段写成 null。"""
    from update import handler

    cursor = FakeCursor(results=[{"id": 1, "name": "只改名"}], rowcount=1)
    _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event("PUT", "/items/1", path_parameters={"id": "1"}, body={"name": "只改名"}), None
    )

    assert response["statusCode"] == 200
    sql, params = cursor.executed[0]
    assert "description" not in sql
    assert params == ("只改名", 1)


def test_update_missing_item_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标不存在时返回 404。"""
    from update import handler

    # rowcount=0 且复查也查不到，说明资源确实不存在
    cursor = FakeCursor(results=[], rowcount=0)
    connection = _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event("PUT", "/items/999", path_parameters={"id": "999"}, body={"name": "x"}), None
    )

    assert response["statusCode"] == 404
    assert connection.rollbacks == 1


def test_update_noop_does_not_return_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """MySQL 的 rowcount 为 0 也可能是值未变化，必须二次确认。"""
    from update import handler

    # 第一次 UPDATE 影响 0 行，但复查能查到记录 -> 不是 404
    cursor = FakeCursor(results=[{"id": 1}, {"id": 1, "name": "不变"}], rowcount=0)
    _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event("PUT", "/items/1", path_parameters={"id": "1"}, body={"name": "不变"}), None
    )

    assert response["statusCode"] == 200


def test_update_rejects_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """空请求体应被拒绝。"""
    from update import handler

    response = handler.lambda_handler(
        api_event("PUT", "/items/1", path_parameters={"id": "1"}, body={}), None
    )

    assert response["statusCode"] == 400


def test_update_rejects_missing_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT 必须携带请求体。"""
    from update import handler

    response = handler.lambda_handler(
        api_event("PUT", "/items/1", path_parameters={"id": "1"}), None
    )

    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------
def test_delete_returns_204(monkeypatch: pytest.MonkeyPatch) -> None:
    """删除成功按规范返回 204 且无响应体。"""
    from delete import handler

    cursor = FakeCursor(rowcount=1)
    connection = _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event("DELETE", "/items/1", path_parameters={"id": "1"}), None
    )

    assert response["statusCode"] == 204
    assert "body" not in response
    assert connection.commits == 1

    sql, params = cursor.executed[0]
    assert "DELETE FROM items WHERE id = %s" in sql
    assert params == (1,)


def test_delete_missing_item_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """目标不存在时返回 404。"""
    from delete import handler

    cursor = FakeCursor(rowcount=0)
    connection = _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event("DELETE", "/items/999", path_parameters={"id": "999"}), None
    )

    assert response["statusCode"] == 404
    # DELETE 未命中任何行，事务本身没有产生变更，正常提交即可（无需回滚）
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_delete_rejects_invalid_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """路径 id 必须是正整数。"""
    from delete import handler

    response = handler.lambda_handler(
        api_event("DELETE", "/items/xyz", path_parameters={"id": "xyz"}), None
    )

    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# 路由与事件格式
# ---------------------------------------------------------------------------
def test_handlers_accept_payload_v1_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """切换到 REST API 入口时业务逻辑无需改动。"""
    from create import handler

    cursor = FakeCursor(results=[{"id": 1, "name": "x"}], lastrowid=1)
    _patch_transaction(monkeypatch, cursor)

    response = handler.lambda_handler(
        api_event("POST", "/items", body={"name": "x"}, version="1.0"), None
    )

    assert response["statusCode"] == 201


def test_error_shape_is_uniform(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有函数的错误响应结构必须一致，便于前端统一处理。"""
    from delete import handler

    response = handler.lambda_handler(
        api_event("DELETE", "/items/abc", path_parameters={"id": "abc"}), None
    )

    payload = _body(response)
    assert set(payload) == {"code", "message"}


def test_list_route_does_not_query_single_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少路径参数时查询函数应走列表分支，而不是直接报错。"""
    from read import handler

    monkeypatch.setattr(db, "fetch_all", lambda sql, params=(): [])
    monkeypatch.setattr(db, "fetch_one", lambda sql, params=(): {"total": 0})

    response = handler.lambda_handler(api_event("GET", "/items"), None)

    assert response["statusCode"] == 200
