"""业务异常。

把「可预期的业务错误」和「未预期的系统错误」分开：
前者映射为明确的 HTTP 状态码与错误码，后者统一映射为 500，
避免把堆栈、SQL、内部实现细节泄漏给客户端。
"""

from __future__ import annotations


class AppError(Exception):
    """可预期的业务异常基类。

    Attributes:
        status_code: 返回给客户端的 HTTP 状态码。
        code: 机器可读的错误码，便于前端分支处理。
        message: 面向人类的错误描述。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ValidationError(AppError):
    """请求参数不合法。"""

    def __init__(self, message: str) -> None:
        super().__init__(400, "VALIDATION_ERROR", message)


class NotFoundError(AppError):
    """目标资源不存在。"""

    def __init__(self, message: str = "资源不存在") -> None:
        super().__init__(404, "NOT_FOUND", message)


class ConflictError(AppError):
    """与现有数据冲突。"""

    def __init__(self, message: str) -> None:
        super().__init__(409, "CONFLICT", message)


class ServiceUnavailableError(AppError):
    """依赖的下游服务暂时不可用。"""

    def __init__(self, message: str = "服务暂时不可用，请稍后重试") -> None:
        super().__init__(503, "SERVICE_UNAVAILABLE", message)
