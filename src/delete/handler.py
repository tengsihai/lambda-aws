"""删除资源用例。"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.config import get_settings
from common.errors import NotFoundError
from common.http import error_response, json_response, parse_path_id, parse_request
from common.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口：``DELETE /items/{id}``。

    Returns:
        删除成功返回 204（无响应体），资源不存在返回 404。
    """
    configure_logging()
    try:
        request = parse_request(event)
        item_id = parse_path_id(request.path_parameters.get("id"))
        _delete_item(item_id)
        logger.info("删除资源成功 id=%s", item_id)
        return json_response(204, None)
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _delete_item(item_id: int) -> None:
    """按主键删除记录，不存在时抛出 :class:`NotFoundError`。"""
    table = get_settings().table_name  # 已在 config 中校验为合法标识符
    affected = db.execute(f"DELETE FROM {table} WHERE id = %s", (item_id,))
    if affected == 0:
        raise NotFoundError(f"资源不存在: id={item_id}")
