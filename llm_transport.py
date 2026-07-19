# -*- coding: utf-8 -*-
"""
LLM Transport 抽象层
====================

为什么需要这一层
----------------
原 ``BaseAgent.conversation_with_tool`` / ``AnthropicAgent.conversation_with_tool``
把 provider 协议相关的东西（payload 拼装、HTTP header、SSE 解析、tool_call 抽取、
token 统计）都写在 Agent 类里。一旦底层 API 换协议（OpenAI → Anthropic → 自家网关 →
OpenRouter…），Agent 的代码要跟着大改。

LLM Transport 把这些 provider 细节**封装到独立的 transport 类**里，Agent 只
看到一份中性的 ``LLMResponse`` / ``LLMEvent``。换底层只换一个 transport 实现，
Agent 本身不动。

层次
----
```
BaseAgent
   │
   ▼  transport.chat(...) / transport.chat_stream(...)
   │
LLMTransport  (抽象：chat / achat / chat_stream / achat_stream)
   │
   ├── HttpxOpenAITransport   (现状：OpenAI-compatible 协议)
   ├── HttpxAnthropicTransport (现状：Anthropic Messages 协议)
   └── …                      (未来：OpenAI SDK / aiohttp / 自家 SDK)
```

Httpx 仍然藏在 ``http_utils.HTTPClient`` 里；transport 用 ``HTTPClient``
做实际的 HTTP 调用。任何"换底层 HTTP 库"的工作量仅限于
``http_utils.py`` + transport 实现本身。
"""
from __future__ import annotations

import json
import uuid as _uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

import httpx

from .http_utils import HTTPClient

# ============================================================================
# Provider-neutral 数据类型
# ============================================================================

@dataclass
class ToolCall:
    """Provider 无关的 tool_call"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class UsageInfo:
    """Token 用量（可能 provider 没给）"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: Optional[Dict[str, Any]] = None


@dataclass
class LLMResponse:
    """单次模型返回的 provider-neutral 视图"""
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: Optional[str] = None     # "end_turn" / "stop" / "tool_use" / "max_tokens" …
    usage: Optional[UsageInfo] = None
    raw: Any = None                       # 原始 provider 响应（供高级用法）


@dataclass
class LLMEvent:
    """流式响应里的一帧"""
    type: str                            # "text" | "tool_call" | "usage" | "done" | "error"
    text: str = ""
    tool_call: Optional[ToolCall] = None
    usage: Optional[UsageInfo] = None
    raw: Any = None


@dataclass
class ChatRequest:
    """Agent 传给 transport 的请求（中性的）"""
    model: str
    system: str
    messages: List[Dict[str, Any]]        # [{"role": "user|assistant|tool", "content": ...}]
    tools: List[Dict[str, Any]] = field(default_factory=list)   # OpenAI-style tool schema
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # provider 特定透传
    extra: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Transport 抽象
# ============================================================================

class LLMTransport(ABC):
    """
    LLM Transport 抽象接口。

    实现需要提供：
    - ``chat(req) -> LLMResponse``         同步非流
    - ``achat(req) -> LLMResponse``        异步非流
    - ``chat_stream(req) -> Iterator[LLMEvent]``       同步流
    - ``achat_stream(req) -> AsyncIterator[LLMEvent]``  异步流
    """

    @abstractmethod
    def chat(self, req: ChatRequest) -> LLMResponse: ...

    @abstractmethod
    async def achat(self, req: ChatRequest) -> LLMResponse: ...

    @abstractmethod
    def chat_stream(self, req: ChatRequest) -> Iterator[LLMEvent]: ...

    @abstractmethod
    async def achat_stream(self, req: ChatRequest) -> AsyncIterator[LLMEvent]: ...


# ============================================================================
# HttpxOpenAITransport — OpenAI-compatible Chat Completions
# ============================================================================

class HttpxOpenAITransport(LLMTransport):
    """
    任何兼容 OpenAI Chat Completions 的 endpoint（OpenAI、Azure、Qwen、vLLM、Ollama…）
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        client: Optional[HTTPClient] = None,
        default_timeout: float = 60.0,
    ):
        self.endpoint = endpoint
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = client or HTTPClient(default_timeout=default_timeout)

    def _build_payload(self, req: ChatRequest) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": req.model,
            "messages": [{"role": "system", "content": req.system}, *req.messages],
            "stream": req.stream,
        }
        if req.tools:
            payload["tools"] = req.tools
            payload["tool_choice"] = "auto"
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        payload.update(req.extra)
        return payload

    def _parse_usage(self, raw: Optional[Dict[str, Any]]) -> Optional[UsageInfo]:
        if not isinstance(raw, dict):
            return None
        u = raw.get("usage") or {}
        if not isinstance(u, dict):
            return None
        return UsageInfo(
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
            total_tokens=int(u.get("total_tokens", 0)),
            raw=u,
        )

    def _response_to_llm(self, data: Dict[str, Any]) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        tool_calls: List[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            tool_calls.append(ToolCall(
                id=tc.get("id", str(_uuid.uuid4())),
                name=fn.get("name", ""),
                arguments=args if isinstance(args, dict) else {},
            ))
        return LLMResponse(
            text=text if isinstance(text, str) else "",
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason"),
            usage=self._parse_usage(data),
            raw=data,
        )

    def chat(self, req: ChatRequest) -> LLMResponse:
        payload = self._build_payload(req)
        rsp = self._client.post(
            self.endpoint,
            headers=self.headers,
            json=payload,
            stream=False,
        )
        return self._response_to_llm(rsp.json())

    async def achat(self, req: ChatRequest) -> LLMResponse:
        from .http_utils import AsyncHTTPClient
        async with AsyncHTTPClient(default_timeout=60.0) as c:
            payload = self._build_payload(req)
            rsp = await c.apost(self.endpoint, headers=self.headers, json=payload)
            return self._response_to_llm(rsp.json())

    def chat_stream(self, req: ChatRequest) -> Iterator[LLMEvent]:
        payload = self._build_payload(req)
        # 走 stream=True：HTTPClient 返回的 Response 需要 iter_lines
        rsp = self._client.post(
            self.endpoint,
            headers={**self.headers, "Accept": "text/event-stream"},
            json=payload,
            stream=True,
        )
        return self._iter_openai_sse(rsp)

    async def achat_stream(self, req: ChatRequest) -> AsyncIterator[LLMEvent]:
        from .http_utils import AsyncHTTPClient
        async with AsyncHTTPClient(default_timeout=60.0) as c:
            payload = self._build_payload(req)
            rsp = await c.apost(
                self.endpoint,
                headers={**self.headers, "Accept": "text/event-stream"},
                json=payload,
            )
            async for evt in self._aiter_openai_sse(rsp):
                yield evt

    def _iter_openai_sse(self, rsp: httpx.Response) -> Iterator[LLMEvent]:
        """OpenAI-style SSE: 每行 ``data: {json}``，最后 ``data: [DONE]``"""
        current_calls: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        usage: Optional[UsageInfo] = None
        for line in rsp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason") or finish_reason
                content = delta.get("content")
                if content:
                    yield LLMEvent(type="text", text=content)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = current_calls.setdefault(idx, {
                        "id": tc.get("id", ""),
                        "name": (tc.get("function") or {}).get("name", ""),
                        "arguments": "",
                    })
                    if "id" in tc and tc["id"]:
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
            u = chunk.get("usage")
            if u:
                usage = self._parse_usage(chunk)
        # flush 累积的 tool_calls
        for slot in current_calls.values():
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            if not isinstance(args, dict):
                args = {}
            yield LLMEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id=slot["id"] or str(_uuid.uuid4()),
                    name=slot["name"],
                    arguments=args,
                ),
            )
        yield LLMEvent(
            type="done",
            stop_reason=finish_reason,
            usage=usage,
            raw=None,
        )

    async def _aiter_openai_sse(self, rsp: httpx.Response) -> AsyncIterator[LLMEvent]:
        # httpx 的 aiter_lines() 是异步的
        current_calls: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        usage: Optional[UsageInfo] = None
        async for line in rsp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason") or finish_reason
                content = delta.get("content")
                if content:
                    yield LLMEvent(type="text", text=content)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = current_calls.setdefault(idx, {
                        "id": tc.get("id", ""),
                        "name": (tc.get("function") or {}).get("name", ""),
                        "arguments": "",
                    })
                    if "id" in tc and tc["id"]:
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
            u = chunk.get("usage")
            if u:
                usage = self._parse_usage(chunk)
        for slot in current_calls.values():
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            if not isinstance(args, dict):
                args = {}
            yield LLMEvent(
                type="tool_call",
                tool_call=ToolCall(
                    id=slot["id"] or str(_uuid.uuid4()),
                    name=slot["name"],
                    arguments=args,
                ),
            )
        yield LLMEvent(type="done", stop_reason=finish_reason, usage=usage, raw=None)


# ============================================================================
# HttpxAnthropicTransport — Anthropic Messages API
# ============================================================================

class HttpxAnthropicTransport(LLMTransport):
    """
    Anthropic Messages API (claude-3-5-sonnet 等)
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        anthropic_version: str = "2023-06-01",
        max_tokens: int = 4096,
        client: Optional[HTTPClient] = None,
    ):
        self.endpoint = endpoint
        self.headers = {
            "x-api-key": api_key,
            "anthropic-version": anthropic_version,
            "Content-Type": "application/json",
        }
        self.max_tokens = max_tokens
        self._client = client or HTTPClient(default_timeout=60.0)

    @staticmethod
    def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for s in tools:
            if s.get("type") == "function" and "function" in s:
                fn = s["function"]
                out.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                })
            else:
                out.append(s)
        return out

    def _build_payload(self, req: ChatRequest) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": req.model,
            "system": req.system,
            "messages": req.messages,
            "max_tokens": req.max_tokens or self.max_tokens,
            "stream": req.stream,
        }
        if req.tools:
            payload["tools"] = self._convert_tools(req.tools)
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        payload.update(req.extra)
        return payload

    def _parse_usage(self, raw: Optional[Dict[str, Any]]) -> Optional[UsageInfo]:
        if not isinstance(raw, dict):
            return None
        u = raw.get("usage") or {}
        if not isinstance(u, dict):
            return None
        return UsageInfo(
            prompt_tokens=int(u.get("input_tokens", 0)),
            completion_tokens=int(u.get("output_tokens", 0)),
            total_tokens=int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)),
            raw=u,
        )

    def _response_to_llm(self, data: Dict[str, Any]) -> LLMResponse:
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        tool_calls: List[ToolCall] = []
        for b in blocks:
            if b.get("type") == "tool_use":
                args = b.get("input") or {}
                tool_calls.append(ToolCall(
                    id=b.get("id", str(_uuid.uuid4())),
                    name=b.get("name", ""),
                    arguments=args if isinstance(args, dict) else {},
                ))
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason"),
            usage=self._parse_usage(data),
            raw=data,
        )

    def chat(self, req: ChatRequest) -> LLMResponse:
        payload = self._build_payload(req)
        rsp = self._client.post(self.endpoint, headers=self.headers, json=payload, stream=False)
        return self._response_to_llm(rsp.json())

    async def achat(self, req: ChatRequest) -> LLMResponse:
        from .http_utils import AsyncHTTPClient
        async with AsyncHTTPClient(default_timeout=60.0) as c:
            payload = self._build_payload(req)
            rsp = await c.apost(self.endpoint, headers=self.headers, json=payload)
            return self._response_to_llm(rsp.json())

    def chat_stream(self, req: ChatRequest) -> Iterator[LLMEvent]:
        payload = self._build_payload(req)
        rsp = self._client.post(
            self.endpoint,
            headers={**self.headers, "Accept": "text/event-stream"},
            json=payload,
            stream=True,
        )
        return self._iter_anthropic_sse(rsp)

    async def achat_stream(self, req: ChatRequest) -> AsyncIterator[LLMEvent]:
        from .http_utils import AsyncHTTPClient
        async with AsyncHTTPClient(default_timeout=60.0) as c:
            payload = self._build_payload(req)
            rsp = await c.apost(
                self.endpoint,
                headers={**self.headers, "Accept": "text/event-stream"},
                json=payload,
            )
            async for evt in self._aiter_anthropic_sse(rsp):
                yield evt

    def _iter_anthropic_sse(self, rsp: httpx.Response) -> Iterator[LLMEvent]:
        """Anthropic SSE: ``event: <name>\\ndata: <json>`` 序列"""
        current: Optional[Dict[str, Any]] = None
        json_buf = ""
        finish_reason: Optional[str] = None
        usage: Optional[UsageInfo] = None
        event_type: Optional[str] = None

        for line in rsp.iter_lines():
            if line is None:
                continue
            if line.startswith("event: "):
                event_type = line[len("event: "):].strip()
                continue
            if not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            etype = evt.get("type") or event_type
            if etype == "content_block_start":
                cb = evt.get("content_block") or {}
                current = {
                    "type": cb.get("type", "text"),
                    "id": cb.get("id"),
                    "name": cb.get("name"),
                    "text": cb.get("text", "") or "",
                    "input": {},
                }
                json_buf = ""
            elif etype == "content_block_delta":
                if current is None:
                    continue
                delta = evt.get("delta") or {}
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    current["text"] = current.get("text", "") + chunk
                    yield LLMEvent(type="text", text=chunk)
                elif delta.get("type") == "input_json_delta":
                    json_buf += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                if current is not None:
                    if current.get("type") == "tool_use":
                        try:
                            current["input"] = json.loads(json_buf) if json_buf else {}
                        except json.JSONDecodeError:
                            current["input"] = {"_raw": json_buf}
                        yield LLMEvent(
                            type="tool_call",
                            tool_call=ToolCall(
                                id=current.get("id") or str(_uuid.uuid4()),
                                name=current.get("name", ""),
                                arguments=current.get("input", {}),
                            ),
                        )
                current = None
                json_buf = ""
            elif etype == "message_delta":
                delta = evt.get("delta") or {}
                if "stop_reason" in delta:
                    finish_reason = delta["stop_reason"]
                if "usage" in evt:
                    usage = self._parse_usage(evt)
            elif etype == "message_stop":
                break
        yield LLMEvent(type="done", stop_reason=finish_reason, usage=usage, raw=None)

    async def _aiter_anthropic_sse(self, rsp: httpx.Response) -> AsyncIterator[LLMEvent]:
        current: Optional[Dict[str, Any]] = None
        json_buf = ""
        finish_reason: Optional[str] = None
        usage: Optional[UsageInfo] = None
        event_type: Optional[str] = None
        async for line in rsp.aiter_lines():
            if line is None:
                continue
            if line.startswith("event: "):
                event_type = line[len("event: "):].strip()
                continue
            if not line.startswith("data: "):
                continue
            try:
                evt = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            etype = evt.get("type") or event_type
            if etype == "content_block_start":
                cb = evt.get("content_block") or {}
                current = {
                    "type": cb.get("type", "text"),
                    "id": cb.get("id"),
                    "name": cb.get("name"),
                    "text": cb.get("text", "") or "",
                    "input": {},
                }
                json_buf = ""
            elif etype == "content_block_delta":
                if current is None:
                    continue
                delta = evt.get("delta") or {}
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    current["text"] = current.get("text", "") + chunk
                    yield LLMEvent(type="text", text=chunk)
                elif delta.get("type") == "input_json_delta":
                    json_buf += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                if current is not None and current.get("type") == "tool_use":
                    try:
                        current["input"] = json.loads(json_buf) if json_buf else {}
                    except json.JSONDecodeError:
                        current["input"] = {"_raw": json_buf}
                    yield LLMEvent(
                        type="tool_call",
                        tool_call=ToolCall(
                            id=current.get("id") or str(_uuid.uuid4()),
                            name=current.get("name", ""),
                            arguments=current.get("input", {}),
                        ),
                    )
                current = None
                json_buf = ""
            elif etype == "message_delta":
                delta = evt.get("delta") or {}
                if "stop_reason" in delta:
                    finish_reason = delta["stop_reason"]
                if "usage" in evt:
                    usage = self._parse_usage(evt)
            elif etype == "message_stop":
                break
        yield LLMEvent(type="done", stop_reason=finish_reason, usage=usage, raw=None)
