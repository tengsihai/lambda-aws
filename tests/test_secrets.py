"""密钥读取与缓存测试。

所有 boto3 调用都被替换为假客户端，测试不会访问真实 AWS。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from common.secrets import DEFAULT_CACHE_TTL_SECONDS, SecretAccessError, get_secret, reset_cache

SECRET_ARN = "arn:aws:secretsmanager:ap-southeast-1:123456789012:secret:db-AbCdEf"


class FakeSecretsClient:
    """替身 Secrets Manager 客户端。"""

    def __init__(
        self, payload: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803 - 对齐 AWS 参数名
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"SecretString": json.dumps(self.payload)}

    @property
    def exceptions(self) -> Any:
        """提供与 boto3 客户端一致的异常命名空间。"""
        return _FakeExceptions


class _FakeExceptions:
    """模拟 boto3 的 client.exceptions 命名空间。"""

    class ResourceNotFoundException(ClientError):
        def __init__(self) -> None:
            super().__init__(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}}, "GetSecretValue"
            )

    class DecryptionFailure(ClientError):
        """对应服务模型中的 DecryptionFailure（不带 Exception 后缀）。"""

        def __init__(self) -> None:
            super().__init__(
                {"Error": {"Code": "DecryptionFailure", "Message": "x"}}, "GetSecretValue"
            )


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    """把 boto3.client 替换为假客户端。"""
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: client)


def test_secret_payload_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥应被解析成字典返回。"""
    _patch_client(monkeypatch, FakeSecretsClient({"username": "u", "password": "p"}))

    assert get_secret(SECRET_ARN) == {"username": "u", "password": "p"}


def test_secret_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存期内不应重复请求 Secrets Manager。"""
    client = FakeSecretsClient({"password": "p"})
    _patch_client(monkeypatch, client)

    get_secret(SECRET_ARN)
    get_secret(SECRET_ARN)
    get_secret(SECRET_ARN)

    assert client.calls == 1


def test_reset_cache_forces_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset_cache 应强制下一次调用重新拉取。"""
    client = FakeSecretsClient({"password": "p"})
    _patch_client(monkeypatch, client)

    get_secret(SECRET_ARN)
    reset_cache()
    get_secret(SECRET_ARN)

    assert client.calls == 2


def test_cache_is_keyed_by_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    """不同密钥不应互相命中缓存。"""
    client = FakeSecretsClient({"password": "p"})
    _patch_client(monkeypatch, client)

    get_secret(SECRET_ARN)
    get_secret(SECRET_ARN + "-other")

    assert client.calls == 2


def test_missing_secret_raises_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """资源不存在应转成 SecretAccessError 而不是原始 ClientError。"""
    client = FakeSecretsClient(error=_FakeExceptions.ResourceNotFoundException())
    _patch_client(monkeypatch, client)

    with pytest.raises(SecretAccessError, match="密钥不存在"):
        get_secret(SECRET_ARN)


def test_undecryptable_secret_raises_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """解密失败应转成 SecretAccessError，并提示检查 KMS 权限。"""
    client = FakeSecretsClient(error=_FakeExceptions.DecryptionFailure())
    _patch_client(monkeypatch, client)

    with pytest.raises(SecretAccessError, match="KMS"):
        get_secret(SECRET_ARN)


def test_other_client_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """其余 AWS 错误也应转成 SecretAccessError，避免泄漏原始错误结构。"""
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetSecretValue"
    )
    _patch_client(monkeypatch, FakeSecretsClient(error=error))

    with pytest.raises(SecretAccessError, match="读取密钥失败"):
        get_secret(SECRET_ARN)


def test_non_json_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 JSON 内容应被识别为配置错误。"""

    class BadClient(FakeSecretsClient):
        def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803
            self.calls += 1
            return {"SecretString": "not-json"}

    _patch_client(monkeypatch, BadClient())

    with pytest.raises(SecretAccessError, match="JSON"):
        get_secret(SECRET_ARN)


def test_array_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥内容必须是 JSON 对象。"""

    class ArrayClient(FakeSecretsClient):
        def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803
            self.calls += 1
            return {"SecretString": json.dumps(["a", "b"])}

    _patch_client(monkeypatch, ArrayClient())

    with pytest.raises(SecretAccessError, match="JSON 对象"):
        get_secret(SECRET_ARN)


def test_missing_secret_string_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 SecretString 说明密钥内容为空，应报错。"""

    class EmptyClient(FakeSecretsClient):
        def get_secret_value(self, SecretId: str) -> dict[str, Any]:  # noqa: N803
            self.calls += 1
            return {}

    _patch_client(monkeypatch, EmptyClient())

    with pytest.raises(SecretAccessError, match="字符串"):
        get_secret(SECRET_ARN)


def test_default_cache_ttl_is_five_minutes() -> None:
    """缓存时长应明显小于常见的密钥轮换窗口。"""
    assert DEFAULT_CACHE_TTL_SECONDS == 300
