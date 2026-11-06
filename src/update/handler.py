"""更新自检记录。"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.errors import AppError, NotFoundError, ValidationError
from common.http import error_response, json_response, parse_path_id, parse_request, require_object
from common.logging_setup import configure_logging
from common.schema import TABLE_NAME, UPDATABLE_FIELDS, serialize_row

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口：``PUT /items/{id}``。

    请求体支持部分更新，只提交需要修改的字段。
    ``biz_no`` 是标识，创建后不接受修改。

    Returns:
        200 响应，body 为 ``{"data": <更新后的记录>}``。
    """
    configure_logging()
    try:
        request = parse_request(event)
        item_id = parse_path_id(request.path_parameters.get("id"))
        payload = require_object(request.body)
        item = _update_item(item_id, payload)
        logger.info("更新自检记录成功 id=%s", item_id)
        return json_response(200, {"data": serialize_row(item)})
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _update_item(item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """按白名单挑出可更新字段并执行更新，返回更新后的完整行。"""
    assignments: list[str] = []
    values: list[Any] = []
    for field, validator in UPDATABLE_FIELDS.items():
        if field in payload:
            assignments.append(f"{field} = %s")
            values.append(validator(payload[field]))

    if not assignments:
        raise ValidationError("没有可更新的字段")

    # 列名只来自 UPDATABLE_FIELDS 的键，不接受外部输入
    assignment_sql = ", ".join(assignments)

    with db.transaction() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE {TABLE_NAME} SET {assignment_sql} WHERE id = %s AND is_deleted = 0",
            (*values, item_id),
        )
        if cursor.rowcount == 0:
            # rowcount 为 0 既可能是不存在、已软删除，也可能是值未变化，
            # 因此再查一次确认，避免把「无变化」误报成 404
            cursor.execute(
                f"SELECT id FROM {TABLE_NAME} WHERE id = %s AND is_deleted = 0",
                (item_id,),
            )
            if cursor.fetchone() is None:
                raise NotFoundError(f"资源不存在: id={item_id}")

        cursor.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE id = %s AND is_deleted = 0",
            (item_id,),
        )
        row = cursor.fetchone()

    if row is None:
        raise AppError(500, "INTERNAL_ERROR", "更新后未查询到数据")
    return row
