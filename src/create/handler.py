"""新增自检记录。"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.errors import AppError
from common.http import error_response, json_response, parse_request, require_object
from common.logging_setup import configure_logging
from common.schema import (
    CREATE_OPTIONAL_FIELDS,
    TABLE_NAME,
    serialize_row,
    validate_biz_no,
    validate_name,
)

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口：``POST /items``。

    ``biz_no`` 与 ``name`` 必填，其余字段缺省时使用列默认值。

    Returns:
        201 响应，body 为 ``{"data": <新增的记录>}``。
    """
    configure_logging()
    try:
        request = parse_request(event)
        payload = require_object(request.body)
        item = _create_item(payload)
        logger.info("新增自检记录成功 id=%s biz_no=%s", item.get("id"), item.get("biz_no"))
        return json_response(201, {"data": serialize_row(item)})
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _create_item(payload: dict[str, Any]) -> dict[str, Any]:
    """校验入参并写入一条记录，返回写入后的完整行。"""
    columns = ["biz_no", "name"]
    values: list[Any] = [validate_biz_no(payload.get("biz_no")), validate_name(payload.get("name"))]

    # 请求里没带的可选字段不写入，交给列默认值（status=0、amount=0.00 等）
    for field, validator in CREATE_OPTIONAL_FIELDS.items():
        if field in payload:
            columns.append(field)
            values.append(validator(payload[field]))

    # 列名全部来自上面固定的白名单，不接受外部输入
    column_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    with db.transaction() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {TABLE_NAME} ({column_sql}) VALUES ({placeholders})",
            tuple(values),
        )
        created_id = cursor.lastrowid
        cursor.execute(f"SELECT * FROM {TABLE_NAME} WHERE id = %s", (created_id,))
        row = cursor.fetchone()

    if row is None:
        # 插入成功却查不到，说明表结构与代码假设不一致
        raise AppError(500, "INTERNAL_ERROR", "写入后未查询到数据")
    return row
