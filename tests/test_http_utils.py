# -*- coding: utf-8 -*-
"""
http_utils 单测

覆盖：retry 行为 / 错误分类 / connect error / 输入参数
"""
import httpx
import pytest
from dumplingsAI.errors import (
    APIError,
    InternalServerError,
    RateLimitError,
    classify,
)
from dumplingsAI.errors import (
    ConnectionError as DumplingsConnectionError,
)
from dumplingsAI.errors import (
    TimeoutError as DumplingsTimeoutError,
)
from dumplingsAI.http_utils import RETRYABLE_STATUS_CODES, HTTPClient


class _MockTransport(httpx.BaseTransport):
    """返回预设的 responses / 抛预设的异常序列"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self._idx = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"method": request.method, "url": str(request.url)})
        if self._idx >= len(self.responses):
            raise IndexError("MockTransport 耗尽")
        item = self.responses[self._idx]
        self._idx += 1
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, httpx.Response):
            return item
        status, body = item
        return httpx.Response(status_code=status, content=body.encode("utf-8"))


def _client_with(responses, *, max_retries=2) -> HTTPClient:
    """构造一个 HTTPClient，client 内部走 MockTransport"""
    mock = _MockTransport(responses)
    inner = httpx.Client(transport=mock, timeout=5.0)
    return HTTPClient(client=inner, max_retries=max_retries, default_timeout=5.0)


def test_classify_status_code_mapping():
    assert classify(400, "bad").__class__.__name__ == "BadRequestError"
    assert classify(401, "x").__class__.__name__ == "AuthenticationError"
    assert classify(403).__class__.__name__ == "PermissionDeniedError"
    assert classify(404).__class__.__name__ == "NotFoundError"
    assert classify(408).__class__.__name__ == "TimeoutError"
    assert classify(409).__class__.__name__ == "ConflictError"
    assert classify(422).__class__.__name__ == "UnprocessableEntityError"
    assert classify(429).__class__.__name__ == "RateLimitError"
    assert classify(500).__class__.__name__ == "InternalServerError"
    assert classify(503).__class__.__name__ == "InternalServerError"
    assert classify(504).__class__.__name__ == "InternalServerError"
    # 未知
    assert classify(418).__class__.__name__ == "APIError"


def test_2xx_returns_response():
    client = _client_with([(200, '{"ok": true}')], max_retries=0)
    rsp = client.post("https://x/y", json={"a": 1})
    assert rsp.status_code == 200
    assert rsp.json() == {"ok": True}


def test_4xx_raises_without_retry():
    """非可重试 4xx：立刻抛错，不重试"""
    client = _client_with([(400, '{"err": "bad"}')], max_retries=3)
    with pytest.raises(APIError) as exc:
        client.post("https://x/y", json={})
    assert exc.value.status_code == 400
    assert len(client.client._transport.calls) == 1  # type: ignore[attr-defined]


def test_5xx_retries_then_raises():
    """5xx：max_retries=2 时会试 3 次（初次 + 2 retry）"""
    client = _client_with(
        [(500, "err1"), (500, "err2"), (500, "err3")],
        max_retries=2,
    )
    with pytest.raises(InternalServerError):
        client.post("https://x/y", json={})
    assert len(client.client._transport.calls) == 3  # type: ignore[attr-defined]


def test_5xx_recovers_on_retry():
    """第 2 次 5xx 后第 3 次 200：返回成功"""
    client = _client_with(
        [(500, "err"), (200, "ok")],
        max_retries=2,
    )
    rsp = client.post("https://x/y", json={})
    assert rsp.status_code == 200
    assert len(client.client._transport.calls) == 2  # type: ignore[attr-defined]


def test_429_retries_with_rate_limit_classification():
    client = _client_with(
        [(429, "slow down"), (200, "ok")],
        max_retries=2,
    )
    rsp = client.post("https://x/y", json={})
    assert rsp.status_code == 200
    assert len(client.client._transport.calls) == 2  # type: ignore[attr-defined]


def test_429_max_retries_raises():
    client = _client_with(
        [(429, "x"), (429, "x"), (429, "x")],
        max_retries=2,
    )
    with pytest.raises(RateLimitError):
        client.post("https://x/y", json={})
    assert len(client.client._transport.calls) == 3  # type: ignore[attr-defined]


def test_connect_error_retries_then_raises():
    client = _client_with(
        [httpx.ConnectError("net down"), httpx.ConnectError("net down"),
         httpx.ConnectError("net down")],
        max_retries=2,
    )
    with pytest.raises(DumplingsConnectionError):
        client.post("https://x/y", json={})
    assert len(client.client._transport.calls) == 3  # type: ignore[attr-defined]


def test_timeout_retries_then_raises():
    client = _client_with(
        [httpx.ConnectTimeout("t1"), httpx.ConnectTimeout("t2"),
         httpx.ReadTimeout("t3")],
        max_retries=2,
    )
    with pytest.raises((DumplingsTimeoutError, DumplingsConnectionError)):
        client.post("https://x/y", json={})


def test_max_retries_zero_means_no_retry():
    client = _client_with([(500, "x")], max_retries=0)
    with pytest.raises(InternalServerError):
        client.post("https://x/y", json={})
    assert len(client.client._transport.calls) == 1  # type: ignore[attr-defined]


def test_retryable_status_codes_constant():
    """确认可重试状态码集合（与官方 SDK 对齐）"""
    assert 408 in RETRYABLE_STATUS_CODES
    assert 409 in RETRYABLE_STATUS_CODES
    assert 429 in RETRYABLE_STATUS_CODES
    assert 500 in RETRYABLE_STATUS_CODES
    assert 502 in RETRYABLE_STATUS_CODES
    assert 503 in RETRYABLE_STATUS_CODES
    assert 504 in RETRYABLE_STATUS_CODES
    # 不在重试集合
    assert 400 not in RETRYABLE_STATUS_CODES
    assert 401 not in RETRYABLE_STATUS_CODES
    assert 404 not in RETRYABLE_STATUS_CODES
