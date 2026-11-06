"""更新资源用例。"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.config import get_settings
from common.errors import AppError, NotFoundError, ValidationError
from common.http import error_response, json_response, parse_path_id, parse_request, require_object
from common.logging_setup import configure_logging

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 255


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口：``PUT /items/{id}``。

    请求体支持部分更新，只提交需要修改的字段。

    Returns:
        200 响应，body 为 ``{"data": <更新后的记录>}``。
    """
    configure_logging()
    try:
        request = parse_request(event)
        item_id = parse_path_id(request.path_parameters.get("id"))
        payload = require_object(request.body)
        item = _update_item(item_id, payload)
        logger.info("更新资源成功 id=%s", item_id)
        return json_response(200, {"data": item})
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _update_item(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """按白名单挑出可更新字段并执行更新，返回更新后的完整行。"""
    fields: dict[str, Any] = {}
    if "name" in payload:
        fields["name"] = _require_name(payload["name"])
    if "description" in payload:
        fields["description"] = _require_optional_description(payload["description"])

    if not fields:
        raise ValidationError("没有可更新的字段")

    # 列名只来自上面的白名单常量，不接受外部输入
    assignments = ", ".join(f"{column} = %s" for column in fields)
    table = get_settings().table_name  # 已在 config 中校验为合法标识符

    with db.transaction() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET {assignments} WHERE id = %s",
            (*fields.values(), item_id),
        )
        if cursor.rowcount == 0:
            # rowcount 为 0 既可能是不存在，也可能是值未变化，
            # 因此再查一次确认，避免把「无变化」误报成 404
            cursor.execute(f"SELECT id FROM {table} WHERE id = %s", (item_id,))
            if cursor.fetchone() is None:
                raise NotFoundError(f"资源不存在: id={item_id}")

        cursor.execute(f"SELECT * FROM {table} WHERE id = %s", (item_id,))
        row = cursor.fetchone()

    if row is None:
        raise AppError(500, "INTERNAL_ERROR", "更新后未查询到数据")
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
