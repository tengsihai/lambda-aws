"""表结构约定与字段校验。

表 ``t_migration_check`` 由 mysql-aws 工程创建与维护，本工程只读写、不建表。

表名直接写死在代码里，不走环境变量：改表名必须改代码并走一次完整发布，
避免某个环境的配置和真实表结构对不上。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import ValidationError

# 迁移链路自检表（非业务表，仅用于验证发布链路）
TABLE_NAME = "t_migration_check"

# 与表上的 CHECK 约束 chk_status 一致
ALLOWED_STATUS: tuple[int, ...] = (0, 1, 2)

# 各列长度上限，与表结构保持一致，避免等到数据库报错才暴露
MAX_BIZ_NO_LENGTH = 64
MAX_NAME_LENGTH = 128
MAX_REMARK_LENGTH = 255

# DECIMAL(18,2) 的取值上限
MAX_AMOUNT = Decimal("9999999999999999.99")


def validate_biz_no(value: Any) -> str:
    """校验业务编号（必填，全局唯一）。"""
    return _require_text(value, "biz_no", max_length=MAX_BIZ_NO_LENGTH)


def validate_name(value: Any) -> str:
    """校验名称（必填）。"""
    return _require_text(value, "name", max_length=MAX_NAME_LENGTH)


def validate_remark(value: Any) -> str | None:
    """校验备注（可为 NULL）。"""
    if value is None:
        return None
    return _require_text(value, "remark", max_length=MAX_REMARK_LENGTH)


def validate_status(value: Any) -> int:
    """校验状态，必须落在 CHECK 约束允许的取值内。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("status 必须是整数")
    if value not in ALLOWED_STATUS:
        allowed = ", ".join(str(item) for item in ALLOWED_STATUS)
        raise ValidationError(f"status 取值只能是 {allowed}")
    return value


def validate_amount(value: Any) -> str:
    """校验金额，返回交给驱动的字符串形式，避免二进制浮点误差。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValidationError("amount 必须是数字")

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError("amount 必须是数字") from None

    if not amount.is_finite():
        raise ValidationError("amount 必须是有限数值")
    if abs(amount) > MAX_AMOUNT:
        raise ValidationError("amount 超出 DECIMAL(18,2) 的取值范围")
    return str(amount)


def validate_payload(value: Any) -> str | None:
    """把 payload 编码成 MySQL JSON 列接受的字符串。"""
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ValidationError("payload 必须是可序列化的 JSON 值") from None


def decode_payload(value: Any) -> Any:
    """把 MySQL 返回的 JSON 文本还原成对象，便于原样输出给调用方。"""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # 内容不是合法 JSON 时原样返回，不要因为解码失败让整个查询报错
        return value


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """把查询结果转换成可直接序列化为 JSON 的字典。"""
    data = dict(row)
    if "payload" in data:
        data["payload"] = decode_payload(data["payload"])
    return data


def _require_text(value: Any, field: str, *, max_length: int) -> str:
    """校验非空字符串字段并去除首尾空白。"""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} 不能为空")
    text = value.strip()
    if len(text) > max_length:
        raise ValidationError(f"{field} 长度不能超过 {max_length}")
    return text


# 建表时可选的字段及其校验器。请求里没带的字段不写入，交给列默认值。
CREATE_OPTIONAL_FIELDS: dict[str, Callable[[Any], Any]] = {
    "status": validate_status,
    "amount": validate_amount,
    "payload": validate_payload,
    "remark": validate_remark,
}

# 更新时允许修改的字段。
# 不含 biz_no：业务编号是标识，创建后不再变更。
UPDATABLE_FIELDS: dict[str, Callable[[Any], Any]] = {
    "name": validate_name,
    **CREATE_OPTIONAL_FIELDS,
}
