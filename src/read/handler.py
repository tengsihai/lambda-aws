"""查询自检记录：支持按 id 查询与分页列表。

已软删除（``is_deleted = 1``）的记录对读接口不可见。
"""

from __future__ import annotations

import logging
from typing import Any

from common import db
from common.errors import NotFoundError
from common.http import (
    error_response,
    json_response,
    parse_int_param,
    parse_path_id,
    parse_request,
)
from common.logging_setup import configure_logging
from common.schema import TABLE_NAME, serialize_row

logger = logging.getLogger(__name__)

# 分页默认值与上限，防止一次拉取过多数据
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_OFFSET = 1_000_000


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda 入口。

    - ``GET /items/{id}`` 返回单条记录
    - ``GET /items`` 返回分页列表

    Returns:
        200 响应；单条为 ``{"data": {...}}``，列表为
        ``{"data": [...], "meta": {"total": ..., "limit": ..., "offset": ...}}``。
    """
    configure_logging()
    try:
        request = parse_request(event)
        raw_id = request.path_parameters.get("id")
        if raw_id:
            item = _get_item(raw_id)
            return json_response(200, {"data": serialize_row(item)})

        limit = parse_int_param(
            request.query_parameters,
            "limit",
            default=DEFAULT_PAGE_SIZE,
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
        offset = parse_int_param(
            request.query_parameters, "offset", default=0, minimum=0, maximum=MAX_OFFSET
        )
        items, total = _list_items(limit, offset)
        return json_response(
            200,
            {
                "data": [serialize_row(row) for row in items],
                "meta": {"total": total, "limit": limit, "offset": offset},
            },
        )
    except Exception as exc:  # noqa: BLE001 - 入口必须把所有异常收敛为 HTTP 响应
        return error_response(exc)


def _get_item(raw_id: Any) -> dict[str, Any]:
    """按主键查询单条未删除的记录。"""
    item_id = parse_path_id(raw_id)

    row = db.fetch_one(
        f"SELECT * FROM {TABLE_NAME} WHERE id = %s AND is_deleted = 0",
        (item_id,),
    )
    if row is None:
        raise NotFoundError(f"资源不存在: id={item_id}")
    return row


def _list_items(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    """分页查询未删除的记录，同时返回满足条件的总条数。"""
    rows = db.fetch_all(
        f"SELECT * FROM {TABLE_NAME} WHERE is_deleted = 0 ORDER BY id DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )
    count_row = db.fetch_one(f"SELECT COUNT(*) AS total FROM {TABLE_NAME} WHERE is_deleted = 0")
    total = int((count_row or {}).get("total", 0))

    logger.info("查询列表成功 返回=%s 总数=%s", len(rows), total)
    return rows, total
