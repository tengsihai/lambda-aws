"""HTTP 请求解析与响应封装。

同时兼容 API Gateway 的两种事件格式：
    - HTTP API（payload 格式 2.0），入口为 ``requestContext.http``
    - REST API / Lambda 代理集成（payload 格式 1.0），入口为 ``httpMethod``

业务代码只依赖统一的 :class:`Request`，切换入口类型无需改动处理器。
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .errors import AppError, ValidationError

logger = logging.getLogger(__name__)

# 请求体上限，超过则直接拒绝，避免把超大 payload 拉进内存
MAX_BODY_BYTES = 1_000_000

JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}


@dataclass(frozen=True)
class Request:
    """解析后的统一请求对象。

    Attributes:
        method: HTTP 方法，已转为大写。
        path: 请求路径。
        path_parameters: 路径参数，例如 ``{"id": "42"}``。
        query_parameters: 查询参数。
        body: 已解析的 JSON 请求体；没有请求体时为 ``None``。
    """

    method: str
    path: str
    path_parameters: dict[str, str] = field(default_factory=dict)
    query_parameters: dict[str, str] = field(default_factory=dict)
    body: Any = None


def json_response(
    status_code: int,
    body: Any,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造统一的 JSON 响应。

    204 No Content 按规范不携带响应体。
    """
    response_headers = dict(JSON_HEADERS)
    if headers:
        response_headers.update(headers)

    response: dict[str, Any] = {"statusCode": status_code, "headers": response_headers}
    if status_code != 204:
        # ensure_ascii=False 让中文按原样输出；default=str 兜底处理 datetime 等类型
        response["body"] = json.dumps(body, ensure_ascii=False, default=str)
    return response


def error_response(exc: Exception) -> dict[str, Any]:
    """把异常转换为对客户端友好的错误响应。

    必须在 ``except`` 块内调用，否则 ``exc_info`` 无法拿到堆栈。
    """
    if isinstance(exc, AppError):
        # 业务异常是预期路径，只记录一条 warn 便于排查
        logger.warning(
            "业务异常: code=%s status=%s message=%s", exc.code, exc.status_code, exc.message
        )
        return json_response(exc.status_code, {"code": exc.code, "message": exc.message})

    # 未预期错误记录完整堆栈用于排障，但只向客户端返回笼统提示
    logger.error("未处理的异常", exc_info=True)
    return json_response(500, {"code": "INTERNAL_ERROR", "message": "服务器内部错误"})


def parse_request(event: Any) -> Request:
    """从 Lambda 事件中解析出统一的请求对象。

    Raises:
        ValidationError: 事件结构不符合任一已知的 API Gateway 格式。
    """
    if not isinstance(event, dict):
        raise ValidationError("请求事件格式不正确")

    http_context = (event.get("requestContext") or {}).get("http")
    if isinstance(http_context, dict):
        # payload 格式 2.0
        method = str(http_context.get("method", "")).upper()
        path = str(http_context.get("path") or event.get("rawPath") or "/")
    else:
        # payload 格式 1.0
        method = str(event.get("httpMethod", "")).upper()
        path = str(event.get("path", "/"))

    if not method:
        raise ValidationError("请求事件缺少 HTTP 方法")

    path_parameters = _string_mapping(event.get("pathParameters"))
    query_parameters = _string_mapping(event.get("queryStringParameters"))
    body = _parse_body(event.get("body"), is_base64=bool(event.get("isBase64Encoded")))

    return Request(
        method=method,
        path=path,
        path_parameters=path_parameters,
        query_parameters=query_parameters,
        body=body,
    )


def require_object(body: Any) -> dict[str, Any]:
    """要求请求体是非空 JSON 对象。"""
    if not isinstance(body, dict):
        raise ValidationError("请求体必须是 JSON 对象")
    return body


def parse_int_param(
    params: dict[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """解析并校验一个整型查询参数。"""
    raw = params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw))
    except ValueError:
        raise ValidationError(f"查询参数 {name} 必须是整数") from None
    if not minimum <= value <= maximum:
        raise ValidationError(f"查询参数 {name} 取值范围为 {minimum}~{maximum}")
    return value


def parse_path_id(raw: Any) -> int:
    """把路径中的 id 解析为正整数。"""
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise ValidationError("id 必须是整数") from None
    if value <= 0:
        raise ValidationError("id 必须是正整数")
    return value


def _string_mapping(value: Any) -> dict[str, str]:
    """把 API Gateway 可能返回的 None/字典统一成 ``dict[str, str]``。"""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items()}


def _parse_body(raw: Any, *, is_base64: bool) -> Any:
    """解析请求体；没有请求体时返回 ``None``。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        raise ValidationError("请求体格式不正确")

    try:
        text = base64.b64decode(raw).decode("utf-8") if is_base64 else raw
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("请求体解码失败") from exc

    if len(text.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValidationError("请求体过大")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("请求体必须是合法 JSON") from exc
