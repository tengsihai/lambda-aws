"""删除自检记录。

表上有 ``is_deleted`` 软删除标记，因此这里做的是软删除：
把标记置 1，记录仍留在库里，只是对读接口不可见。
"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.errors import NotFoundError
from common.http import error_response, json_response, parse_path_id, parse_request
from common.logging_setup import configure_logging
from common.schema import TABLE_NAME

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口：``DELETE /items/{id}``。

    Returns:
        删除成功返回 204（无响应体），资源不存在或已删除返回 404。
    """
    configure_logging()
    try:
        request = parse_request(event)
        item_id = parse_path_id(request.path_parameters.get("id"))
        _delete_item(item_id)
        logger.info("软删除自检记录成功 id=%s", item_id)
        return json_response(204, None)
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _delete_item(item_id: int) -> None:
    """把记录标记为已删除；不存在或已删除时抛出 :class:`NotFoundError`。"""
    with db.transaction() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE {TABLE_NAME} SET is_deleted = 1 WHERE id = %s AND is_deleted = 0",
            (item_id,),
        )
        if cursor.rowcount == 0:
            raise NotFoundError(f"资源不存在: id={item_id}")
