# -*- coding: utf-8 -*-
"""
Dumplings 中央 HTTP 客户端（基于 httpx）
========================================

httpx 同时提供同步 ``Client`` 和 ``AsyncClient``，是 ``requests`` 的现代替代：

- 内置 timeout（无需手动读 socket）
- 同步 / 异步 API 形态一致
- 统一 ``httpx.HTTPError`` 体系
- 连接池复用，keep-alive

我们在此之上做：
- **指数退避重试**：429 / 5xx / 网络错误按 ``0.5 * 2^attempt + jitter`` 退避
- **错误分类**：非 2xx → ``errors.APIError`` 子类
- **超时与重试可单次覆盖**：``timeout=`` / ``max_retries=``

调用层
------
- ``BaseAgent`` 的 ``Connectivity`` / ``conversation_with_tool``（同步）
- ``AnthropicAgent`` 的 ``_connectivity`` / ``_call_blocking`` / ``_call_stream``（同步）
- ``Agent.aconversation_with_tool``（异步）
- ``mcp_bridge`` 的 RPC 通道
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional

import httpx

from .errors import (
    APIError,
    classify,
)
from .errors import (
    ConnectionError as DumplingsConnectionError,
)
from .errors import (
    TimeoutError as DumplingsTimeoutError,
)
from .logging_config import logger

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT: float = 60.0
DEFAULT_MAX_RETRIES: int = 2
DEFAULT_BACKOFF_BASE: float = 0.5
DEFAULT_BACKOFF_CAP: float = 8.0
DEFAULT_JITTER: float = 0.25

# 默认可重试的状态码（除 status code 外，网络层错误总是重试）
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------

class HTTPClient:
    """
    同步 HTTP 客户端（基于 ``httpx.Client``）。

    线程安全：``httpx.Client`` 在多线程下应该各自 new 一份；
    Agent 内部使用模块级单例的场景里，建议一个进程一个 client。
    """

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_timeout: float = DEFAULT_TIMEOUT,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_cap: float = DEFAULT_BACKOFF_CAP,
        jitter: float = DEFAULT_JITTER,
    ):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=default_timeout,
            follow_redirects=True,
        )
        self.max_retries = max(0, int(max_retries))
        self.default_timeout = float(default_timeout)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.jitter = float(jitter)

    def __del__(self) -> None:
        if self._owns_client:
            try:
                self.client.close()
            except Exception:
                pass

    # ----- POST -----

    def post(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        json: Any = None,
        content: Any = None,
        params: Any = None,
        stream: bool = False,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> httpx.Response:
        """POST，失败按策略重试，最终失败抛 ``APIError`` / ``TimeoutError`` / ``ConnectionError``。"""
        attempts = self.max_retries if max_retries is None else max(0, int(max_retries))
        eff_timeout = self.default_timeout if timeout is None else float(timeout)

        last_exc: Optional[BaseException] = None
        for attempt in range(attempts + 1):
            try:
                rsp = self.client.post(
                    url,
                    headers=headers or {},
                    json=json,
                    content=content,
                    params=params,
                    timeout=eff_timeout,
                )
            except httpx.TimeoutException as e:
                last_exc = DumplingsTimeoutError(
                    f"HTTP timeout after {eff_timeout}s: {e}",
                    status_code=None,
                )
                logger.warning(
                    f"http_utils: timeout (attempt {attempt+1}/{attempts+1}) url={url}"
                )
            except httpx.ConnectError as e:
                last_exc = DumplingsConnectionError(
                    f"HTTP connection error: {e}",
                    status_code=None,
                )
                logger.warning(
                    f"http_utils: connect error (attempt {attempt+1}/{attempts+1}) url={url}"
                )
            except httpx.HTTPError as e:
                # 其他 httpx 异常（如 InvalidURL）不重试
                raise APIError(f"HTTP request failed: {e}") from e
            else:
                if 200 <= rsp.status_code < 300:
                    return rsp
                if rsp.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    body_preview = self._safe_body_preview(rsp)
                    logger.warning(
                        f"http_utils: status {rsp.status_code} "
                        f"(attempt {attempt+1}/{attempts+1}) url={url} body={body_preview}"
                    )
                    last_exc = classify(rsp.status_code, body_preview, dict(rsp.headers))
                else:
                    body_preview = self._safe_body_preview(rsp)
                    raise classify(
                        rsp.status_code,
                        body_preview,
                        dict(rsp.headers),
                    )

            if attempt < attempts:
                self._sleep_backoff(attempt)

        assert last_exc is not None
        raise last_exc

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """便捷 GET：复用同一套 retry + 错误分类逻辑。"""
        attempts = kwargs.pop("max_retries", self.max_retries)
        eff_timeout = kwargs.pop("timeout", self.default_timeout)
        last_exc: Optional[BaseException] = None
        for attempt in range(int(attempts) + 1):
            try:
                rsp = self.client.get(
                    url,
                    headers=kwargs.get("headers") or {},
                    params=kwargs.get("params"),
                    timeout=eff_timeout,
                )
            except httpx.TimeoutException as e:
                last_exc = DumplingsTimeoutError(
                    f"HTTP timeout after {eff_timeout}s: {e}", status_code=None,
                )
                logger.warning(f"http_utils: GET timeout (attempt {attempt+1})")
            except httpx.ConnectError as e:
                last_exc = DumplingsConnectionError(
                    f"HTTP connection error: {e}", status_code=None,
                )
                logger.warning(f"http_utils: GET connect error (attempt {attempt+1})")
            except httpx.HTTPError as e:
                raise APIError(f"HTTP request failed: {e}") from e
            else:
                if 200 <= rsp.status_code < 300:
                    return rsp
                if rsp.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    body_preview = self._safe_body_preview(rsp)
                    last_exc = classify(rsp.status_code, body_preview, dict(rsp.headers))
                else:
                    raise classify(rsp.status_code, self._safe_body_preview(rsp), dict(rsp.headers))
            if attempt < attempts:
                self._sleep_backoff(attempt)
        assert last_exc is not None
        raise last_exc

    # ----- 助手 -----

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self.backoff_base * (2 ** attempt)
        delay = min(delay, self.backoff_cap)
        delay += random.uniform(0, self.jitter)
        time.sleep(delay)

    @staticmethod
    def _safe_body_preview(rsp: httpx.Response, limit: int = 300) -> str:
        try:
            content = rsp.text or ""
            return content[:limit]
        except Exception:
            return "<unrepresentable body>"


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class AsyncHTTPClient:
    """
    异步 HTTP 客户端（基于 ``httpx.AsyncClient``）。

    用于 Agent 的 ``aconversation_with_tool`` 路径，
    内部对 ``async for`` 流式响应做同样 retry + 错误分类。
    """

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_timeout: float = DEFAULT_TIMEOUT,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_cap: float = DEFAULT_BACKOFF_CAP,
        jitter: float = DEFAULT_JITTER,
    ):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=default_timeout,
            follow_redirects=True,
        )
        self.max_retries = max(0, int(max_retries))
        self.default_timeout = float(default_timeout)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.jitter = float(jitter)

    async def __aenter__(self) -> "AsyncHTTPClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def apost(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        json: Any = None,
        content: Any = None,
        params: Any = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> httpx.Response:
        """异步 POST，失败按策略重试。"""
        attempts = self.max_retries if max_retries is None else max(0, int(max_retries))
        eff_timeout = self.default_timeout if timeout is None else float(timeout)

        last_exc: Optional[BaseException] = None
        for attempt in range(attempts + 1):
            try:
                rsp = await self.client.post(
                    url,
                    headers=headers or {},
                    json=json,
                    content=content,
                    params=params,
                    timeout=eff_timeout,
                )
            except httpx.TimeoutException as e:
                last_exc = DumplingsTimeoutError(
                    f"HTTP timeout after {eff_timeout}s: {e}",
                    status_code=None,
                )
                logger.warning(
                    f"http_utils[async]: timeout (attempt {attempt+1}/{attempts+1}) url={url}"
                )
            except httpx.ConnectError as e:
                last_exc = DumplingsConnectionError(
                    f"HTTP connection error: {e}",
                    status_code=None,
                )
                logger.warning(
                    f"http_utils[async]: connect error (attempt {attempt+1}/{attempts+1}) url={url}"
                )
            except httpx.HTTPError as e:
                raise APIError(f"HTTP request failed: {e}") from e
            else:
                if 200 <= rsp.status_code < 300:
                    return rsp
                if rsp.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                    body_preview = _safe_body_preview(rsp)
                    last_exc = classify(rsp.status_code, body_preview, dict(rsp.headers))
                    logger.warning(
                        f"http_utils[async]: status {rsp.status_code} "
                        f"(attempt {attempt+1}/{attempts+1}) url={url} body={body_preview}"
                    )
                else:
                    body_preview = _safe_body_preview(rsp)
                    raise classify(
                        rsp.status_code,
                        body_preview,
                        dict(rsp.headers),
                    )

            if attempt < attempts:
                await self._asleep_backoff(attempt)

        assert last_exc is not None
        raise last_exc

    async def _asleep_backoff(self, attempt: int) -> None:
        delay = self.backoff_base * (2 ** attempt)
        delay = min(delay, self.backoff_cap)
        delay += random.uniform(0, self.jitter)
        await asyncio.sleep(delay)


def _safe_body_preview(rsp: httpx.Response, limit: int = 300) -> str:
    try:
        content = rsp.text or ""
        return content[:limit]
    except Exception:
        return "<unrepresentable body>"


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_default_client: Optional[HTTPClient] = None


def get_default_client() -> HTTPClient:
    """获取默认同步 client（懒加载单例）。"""
    global _default_client
    if _default_client is None:
        _default_client = HTTPClient()
    return _default_client
