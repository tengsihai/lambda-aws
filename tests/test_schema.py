"""表结构与字段校验测试。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from common.errors import ValidationError
from common.schema import (
    ALLOWED_STATUS,
    CREATE_OPTIONAL_FIELDS,
    MAX_AMOUNT,
    TABLE_NAME,
    UPDATABLE_FIELDS,
    decode_payload,
    serialize_row,
    validate_amount,
    validate_biz_no,
    validate_name,
    validate_payload,
    validate_remark,
    validate_status,
)


def test_table_name_is_hardcoded() -> None:
    """表名写死在代码里，不走环境变量。"""
    assert TABLE_NAME == "t_migration_check"


def test_status_allowed_values_match_check_constraint() -> None:
    """允许值必须与表上的 chk_status 约束一致。"""
    assert ALLOWED_STATUS == (0, 1, 2)


@pytest.mark.parametrize("value", [0, 1, 2])
def test_valid_status_accepted(value: int) -> None:
    """0/1/2 都应被接受。"""
    assert validate_status(value) == value


@pytest.mark.parametrize("value", [3, -1, "1", 1.0, True, None])
def test_invalid_status_rejected(value: Any) -> None:
    """非 0/1/2 或非整数一律拒绝，避免撞上 CHECK 约束。"""
    with pytest.raises(ValidationError, match="status"):
        validate_status(value)


def test_biz_no_trims_whitespace() -> None:
    """业务编号应去除首尾空白。"""
    assert validate_biz_no("  BIZ-1  ") == "BIZ-1"


@pytest.mark.parametrize("value", [None, 123, 4.5, ["BIZ-1"]])
def test_biz_no_rejects_non_string(value: Any) -> None:
    """业务编号必须是字符串。"""
    with pytest.raises(ValidationError, match="biz_no"):
        validate_biz_no(value)


def test_text_fields_reject_empty() -> None:
    """空串与纯空白都算没填。"""
    for validator in (validate_biz_no, validate_name):
        for bad in ("", "   ", None, 123):
            with pytest.raises(ValidationError):
                validator(bad)


def test_text_fields_reject_oversized() -> None:
    """超过列长度上限应被拒绝。"""
    with pytest.raises(ValidationError, match="64"):
        validate_biz_no("x" * 65)
    with pytest.raises(ValidationError, match="128"):
        validate_name("x" * 129)
    with pytest.raises(ValidationError, match="255"):
        validate_remark("x" * 256)


def test_remark_allows_null() -> None:
    """备注可为 NULL。"""
    assert validate_remark(None) is None
    assert validate_remark("  备注  ") == "备注"


@pytest.mark.parametrize("value", [0, 1, 2, "0.00", "12.5", 12.5, Decimal("3.14")])
def test_valid_amount_accepted(value: Any) -> None:
    """合法金额应被转成字符串交给驱动，避免浮点误差。"""
    result = validate_amount(value)
    assert isinstance(result, str)
    assert Decimal(result) == Decimal(str(value))


@pytest.mark.parametrize("value", ["abc", "", None, True, [1], {"a": 1}, "NaN", "Infinity"])
def test_invalid_amount_rejected(value: Any) -> None:
    """非数字、布尔与非有限值一律拒绝。"""
    with pytest.raises(ValidationError, match="amount"):
        validate_amount(value)


def test_amount_rejects_out_of_range() -> None:
    """超出 DECIMAL(18,2) 上限应被拒绝。"""
    with pytest.raises(ValidationError, match="DECIMAL"):
        validate_amount(MAX_AMOUNT + 1)
    # 正好等于上限则允许
    assert validate_amount(MAX_AMOUNT) == str(MAX_AMOUNT)


def test_payload_encoded_to_json_string() -> None:
    """payload 必须编码成 JSON 字符串才能写进 JSON 列。"""
    assert validate_payload({"k": "中文"}) == '{"k": "中文"}'
    assert validate_payload([1, 2]) == "[1, 2]"
    assert validate_payload(None) is None


def test_payload_rejects_unserializable() -> None:
    """无法序列化的值应在写库前被拒绝。"""
    with pytest.raises(ValidationError, match="payload"):
        validate_payload(object())


def test_payload_decode_roundtrip() -> None:
    """写进去什么，读出来就该是什么。"""
    original = {"k": "中文", "n": 1}
    encoded = validate_payload(original)
    assert decode_payload(encoded) == original


def test_decode_payload_tolerates_invalid_json() -> None:
    """内容不是合法 JSON 时原样返回，不让整个查询失败。"""
    assert decode_payload("not-json") == "not-json"
    assert decode_payload(None) is None
    assert decode_payload(42) == 42


def test_serialize_row_decodes_payload() -> None:
    """序列化行时应还原 payload，其余字段原样保留。"""
    row = {"id": 1, "name": "x", "payload": '{"a": 1}', "created_at": "2026-01-01"}
    result = serialize_row(row)

    assert result["payload"] == {"a": 1}
    assert result["name"] == "x"
    # 不修改入参
    assert row["payload"] == '{"a": 1}'


def test_create_optional_fields_exclude_required_ones() -> None:
    """建表可选字段不含 biz_no / name 这两个必填列。"""
    assert set(CREATE_OPTIONAL_FIELDS) == {"status", "amount", "payload", "remark"}


def test_updatable_fields_exclude_biz_no() -> None:
    """biz_no 是标识，创建后不接受修改。"""
    assert "biz_no" not in UPDATABLE_FIELDS
    assert "name" in UPDATABLE_FIELDS


def test_updatable_fields_all_have_validators() -> None:
    """每个可更新字段都必须配校验器。"""
    assert all(callable(validator) for validator in UPDATABLE_FIELDS.values())
