# -*- coding: utf-8 -*-
"""
Dumplings 异常类型体系
======================

对齐官方 openai-python / anthropic-sdk-python 的错误体系：

- ``DumplingsError``        — 框架层基类
- ``APIError``              — HTTP API 通用错误
- ``BadRequestError``       — HTTP 400
- ``AuthenticationError``   — HTTP 401
- ``PermissionDeniedError`` — HTTP 403
- ``NotFoundError``         — HTTP 404
- ``ConflictError``         — HTTP 409
- ``TimeoutError``          — HTTP 408 或 read/connect 超时
- ``RateLimitError``        — HTTP 429
- ``UnprocessableEntityError`` — HTTP 422
- ``InternalServerError``   — HTTP 5xx
- ``ConnectionError``       — 网络层错误

``http_utils.HTTPClient`` 拿到非 2xx 响应时按状态码选对应异常抛出；
网络层异常（``httpx.ConnectError`` / ``httpx.TimeoutException``）也转成
对应 ``APIError`` 子类。
"""
from __future__ import annotations

from typing import Any, Optional


class DumplingsError(Exception):
    """所有 Dumplings 异常的基类。"""


class APIError(DumplingsError):
    """HTTP API 错误基类，承载 status_code / body / headers。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Any = None,
        headers: Optional[dict] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.headers = headers or {}

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"status_code={self.status_code}, "
            f"message={str(self)[:200]!r})"
        )


class BadRequestError(APIError):
    """HTTP 400"""


class AuthenticationError(APIError):
    """HTTP 401"""


class PermissionDeniedError(APIError):
    """HTTP 403"""


class NotFoundError(APIError):
    """HTTP 404"""


class ConflictError(APIError):
    """HTTP 409"""


class UnprocessableEntityError(APIError):
    """HTTP 422"""


class TimeoutError(APIError):
    """HTTP 408 或 read/connect 超时"""


class RateLimitError(APIError):
    """HTTP 429"""


class InternalServerError(APIError):
    """HTTP 5xx"""


class ConnectionError(APIError):
    """网络层异常（DNS / TCP / TLS 等）"""


_STATUS_TO_EXC: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    408: TimeoutError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
    500: InternalServerError,
    501: InternalServerError,
    502: InternalServerError,
    503: InternalServerError,
    504: InternalServerError,
}


def classify(status_code: int, body: Any = None, headers: Optional[dict] = None) -> APIError:
    """根据 HTTP 状态码选对应异常类并构造一个实例。"""
    cls = _STATUS_TO_EXC.get(status_code, APIError)
    snippet = ""
    if body is not None:
        try:
            snippet = str(body)[:300]
        except Exception:
            snippet = "<unrepresentable body>"
    msg = f"{cls.__name__} (status {status_code}): {snippet}"
    return cls(msg, status_code=status_code, body=body, headers=headers or {})
