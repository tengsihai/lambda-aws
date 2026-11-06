"""从 AWS Secrets Manager 读取密钥内容，并在进程内缓存。

缓存的必要性：Lambda 容器会被多次复用，每次调用都去请求 Secrets Manager
既增加延迟也会产生额外 API 费用。

缓存的代价：密钥轮换后，最长需要等待 ``ttl_seconds`` 才会拿到新值。
默认 5 分钟，可按轮换窗口自行调整；需要立即失效时调用 :func:`reset_cache`。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# 密钥缓存时长（秒）。应明显小于密钥轮换的生效窗口。
DEFAULT_CACHE_TTL_SECONDS = 300

# ARN -> (过期时间戳, 密钥内容)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


class SecretAccessError(RuntimeError):
    """密钥不存在、无法解密或内容格式不合法。"""


def get_secret(secret_arn: str, *, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> dict[str, Any]:
    """读取 JSON 格式的密钥内容，带进程内 TTL 缓存。

    Args:
        secret_arn: 密钥的 ARN 或名称。
        ttl_seconds: 缓存有效期。

    Returns:
        密钥内容解析后的字典。

    Raises:
        SecretAccessError: 密钥缺失或内容无法解析。
    """
    now = time.monotonic()
    with _lock:
        cached = _cache.get(secret_arn)
        if cached is not None:
            expires_at, payload = cached
            if now < expires_at:
                logger.debug("命中密钥缓存: %s", secret_arn)
                return payload

    payload = _fetch_secret(secret_arn)

    with _lock:
        _cache[secret_arn] = (now + ttl_seconds, payload)
    return payload


def reset_cache() -> None:
    """清空缓存，供测试或密钥轮换后强制刷新使用。"""
    with _lock:
        _cache.clear()


def _fetch_secret(secret_arn: str) -> dict[str, Any]:
    """请求 Secrets Manager 并解析密钥内容。"""
    # 使用自适应重试，避免在限流时直接把错误抛给调用方
    client = boto3.client(
        "secretsmanager",
        config=BotoConfig(retries={"max_attempts": 3, "mode": "adaptive"}),
    )

    try:
        response = client.get_secret_value(SecretId=secret_arn)
    except client.exceptions.ResourceNotFoundException as exc:
        # 密钥不存在属于配置问题，转成领域异常后由入口统一返回
        raise SecretAccessError(f"密钥不存在: {secret_arn}") from exc
    except client.exceptions.DecryptionFailure as exc:
        # 注意：该异常在服务模型中的名字是 DecryptionFailure，不带 Exception 后缀
        raise SecretAccessError(f"密钥无法解密（检查 KMS 权限）: {secret_arn}") from exc
    except ClientError as exc:
        # 权限不足、限流等其余错误统一收敛，避免向调用方暴露原始 AWS 错误结构
        raise SecretAccessError(f"读取密钥失败: {exc}") from exc

    secret_string = response.get("SecretString")
    if secret_string is None:
        raise SecretAccessError(f"密钥内容不是字符串: {secret_arn}")

    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise SecretAccessError(f"密钥内容不是合法 JSON: {secret_arn}") from exc

    if not isinstance(payload, dict):
        raise SecretAccessError(f"密钥内容必须是 JSON 对象: {secret_arn}")

    # 只记录 ARN，绝不记录密钥内容本身
    logger.info("已加载密钥: %s", secret_arn)
    return payload
