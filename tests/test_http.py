"""HTTP 解析与响应封装测试。"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from common.errors import NotFoundError, ValidationError
from common.http import (
    error_response,
    json_response,
    parse_int_param,
    parse_path_id,
    parse_request,
    require_object,
)
from helpers import api_event


def test_parse_payload_v2_event() -> None:
    """HTTP API payload 2.0 应被正确解析。"""
    event = api_event("POST", "/items", body={"name": "hello"}, query_parameters={"limit": "10"})
    request = parse_request(event)

    assert request.method == "POST"
    assert request.path == "/items"
    assert request.body == {"name": "hello"}
    assert request.query_parameters == {"limit": "10"}


def test_parse_payload_v1_event() -> None:
    """REST API payload 1.0 应被正确解析。"""
    event = api_event("GET", "/items/42", path_parameters={"id": "42"}, version="1.0")
    request = parse_request(event)

    assert request.method == "GET"
    # payload 1.0 的 path 是完整路径，包含 id
    assert request.path == "/items/42"
    assert request.path_parameters == {"id": "42"}


def test_missing_body_parses_to_none() -> None:
    """GET 请求没有 body，应解析为 None 而不是抛错。"""
    assert parse_request(api_event("GET", "/items")).body is None


def test_parse_base64_encoded_body() -> None:
    """带 isBase64Encoded 的请求体应先解码再解析。"""
    raw = base64.b64encode(json.dumps({"name": "中文"}).encode("utf-8")).decode("ascii")
    event = api_event("POST", "/items", body=raw)
    event["isBase64Encoded"] = True

    assert parse_request(event).body == {"name": "中文"}


def test_invalid_json_body_raises() -> None:
    """请求体不是合法 JSON 时应返回 400 类异常。"""
    with pytest.raises(ValidationError, match="JSON"):
        parse_request(api_event("POST", "/items", body="{not-json"))


def test_oversized_body_rejected() -> None:
    """超过上限的请求体应被拒绝。"""
    with pytest.raises(ValidationError, match="过大"):
        parse_request(api_event("POST", "/items", body="x" * 2_000_000))


def test_non_dict_event_rejected() -> None:
    """事件结构异常时应给出可读的错误。"""
    with pytest.raises(ValidationError, match="事件"):
        parse_request("not-a-dict")


def test_missing_http_method_rejected() -> None:
    """缺少 method 的事件应被拒绝。"""
    with pytest.raises(ValidationError, match="HTTP 方法"):
        parse_request({"version": "2.0", "requestContext": {"http": {}}})


def test_require_object_rejects_array() -> None:
    """请求体必须是对象，数组应被拒绝。"""
    with pytest.raises(ValidationError, match="JSON 对象"):
        require_object([1, 2, 3])


def test_missing_path_parameters_are_empty() -> None:
    """缺少 pathParameters 时应返回空字典而不是 None。"""
    assert parse_request(api_event("GET", "/items")).path_parameters == {}


def test_204_response_has_no_body() -> None:
    """204 按规范不得携带 body。"""
    response = json_response(204, None)
    assert response["statusCode"] == 204
    assert "body" not in response


def test_json_response_keeps_utf8_chinese() -> None:
    """中文应按原样输出，便于前端直接读取。"""
    response = json_response(200, {"name": "中文名"})
    assert response["headers"]["Content-Type"].startswith("application/json")
    assert "中文名" in response["body"]
    assert "\\u" not in response["body"]


def test_json_response_allows_custom_headers() -> None:
    """允许调用方追加自定义响应头。"""
    response = json_response(200, {}, headers={"Cache-Control": "no-store"})
    assert response["headers"]["Cache-Control"] == "no-store"


def test_app_error_maps_to_status_code() -> None:
    """业务异常应转成对应的状态码与错误码。"""
    response = error_response(NotFoundError("资源不存在: id=9"))
    assert response["statusCode"] == 404
    body: dict[str, Any] = json.loads(response["body"])
    assert body["code"] == "NOT_FOUND"
    assert "id=9" in body["message"]


def test_unexpected_error_hides_internals() -> None:
    """系统错误只能返回笼统提示，不能暴露堆栈或 SQL。"""
    response = error_response(RuntimeError("SELECT * FROM secret_table failed"))
    assert response["statusCode"] == 500
    body: dict[str, Any] = json.loads(response["body"])
    assert body["code"] == "INTERNAL_ERROR"
    assert "secret_table" not in body["message"]


def test_int_param_uses_default() -> None:
    """参数缺失时应使用默认值。"""
    assert parse_int_param({}, "limit", default=20, minimum=1, maximum=100) == 20
    assert parse_int_param({"limit": ""}, "limit", default=20, minimum=1, maximum=100) == 20


def test_int_param_parses_valid_value() -> None:
    """合法数值应被解析。"""
    assert parse_int_param({"limit": "10"}, "limit", default=20, minimum=1, maximum=100) == 10
    assert parse_int_param({"offset": "0"}, "offset", default=5, minimum=0, maximum=100) == 0


def test_int_param_rejects_non_numeric() -> None:
    """非数字应返回 400。"""
    with pytest.raises(ValidationError, match="整数"):
        parse_int_param({"limit": "abc"}, "limit", default=20, minimum=1, maximum=100)


def test_int_param_rejects_out_of_range() -> None:
    """超出范围应返回 400。"""
    with pytest.raises(ValidationError, match="取值范围"):
        parse_int_param({"limit": "9999"}, "limit", default=20, minimum=1, maximum=100)


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "1.5"])
def test_invalid_path_id_rejected(raw: str) -> None:
    """路径 id 必须是正整数。"""
    with pytest.raises(ValidationError, match="id"):
        parse_path_id(raw)


def test_valid_path_id_parsed() -> None:
    """合法 id 应被解析为 int。"""
    assert parse_path_id("42") == 42
