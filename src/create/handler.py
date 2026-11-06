"""新增资源用例。"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.config import get_settings
from common.errors import AppError, ValidationError
from common.http import error_response, json_response, parse_request, require_object
from common.logging_setup import configure_logging

logger = logging.getLogger(__name__)

# 允许写入的字段白名单：列名只能来自这里，避免把外部输入拼进 SQL
MAX_NAME_LENGTH = 255


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口：``POST /items``。

    Args:
        event: API Gateway 事件。
        context: Lambda 运行时上下文（本实现未使用）。

    Returns:
        201 响应，body 为 ``{"data": <新增的记录>}``。
    """
    configure_logging()
    try:
        request = parse_request(event)
        payload = require_object(request.body)
        item = _create_item(payload)
        logger.info("新增资源成功 id=%s", item.get("id"))
        return json_response(201, {"data": item})
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _create_item(payload: dict[str, Any]) -> dict[str, Any]:
    """校验入参并写入一条记录，返回写入后的完整行。"""
    name = _require_name(payload.get("name"))
    description = _require_optional_description(payload.get("description"))

    table = get_settings().table_name  # 已在 config 中校验为合法标识符
    with db.transaction() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {table} (name, description) VALUES (%s, %s)",
            (name, description),
        )
        created_id = cursor.lastrowid
        cursor.execute(
            f"SELECT * FROM {table} WHERE id = %s",
            (created_id,),
        )
        row = cursor.fetchone()

    if row is None:
        # 插入成功却查不到，说明表结构与代码假设不一致
        raise AppError(500, "INTERNAL_ERROR", "写入后未查询到数据")
    return row


def _require_name(value: Any) -> str:
    """校验必填的 name 字段。"""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("name 不能为空")
    name = value.strip()
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"name 长度不能超过 {MAX_NAME_LENGTH}")
    return name


def _require_optional_description(value: Any) -> str | None:
    """校验可选的 description 字段。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("description 必须是字符串")
    return value
