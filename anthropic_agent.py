# -*- coding: utf-8 -*-
"""
Anthropic 协议 Agent 基类

与 BaseAgent 共享 tool_registry / agent_list / skill_registry，
但把对话流程换成 Anthropic Messages API：
    https://docs.anthropic.com/en/api/messages

要点（与 OpenAI 协议差异）：
1. system prompt 不再是 messages 里的第一条，而是顶层 ``system`` 字段。
2. tools 顶层 schema： ``{name, description, input_schema}`` ，
   不再嵌套在 ``function`` 字段。
3. 工具调用通过 content blocks（``tool_use`` / ``tool_result``）往返：
   助手消息携带 ``tool_use`` 块 → 我们执行 → 用户消息携带 ``tool_result`` 块。
4. 流式事件是 ``message_start`` / ``content_block_start`` /
   ``content_block_delta`` / ``content_block_stop`` / ``message_delta`` /
   ``message_stop`` 序列，不是 OpenAI 的 ``chunk.choices``。
5. 多模态使用 ``{"type":"image","source":{...}}`` 块。
"""
import json
import os
import platform
import threading
import time
import uuid as _uuid
from typing import List, Optional

try:
    from .agent_tool import _builtin_promote_overrides, builtin_tool, tool_registry
    from .llm_transport import (
        ChatRequest,
        HttpxAnthropicTransport,
        LLMResponse,
    )
    from .logging_config import logger
except ImportError:
    raise ImportError("不可单独执行，不可单独 import")


# ======================================================================
# 1. Schema 转换：OpenAI tool schema ↔ Anthropic tool schema
# ======================================================================

def _openai_tool_to_anthropic(openai_schema: dict) -> dict:
    """
    tool_registry 内部统一存 OpenAI 风格：
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Anthropic 风格：
        {"name": ..., "description": ..., "input_schema": ...}
    """
    if openai_schema.get("type") == "function" and isinstance(openai_schema.get("function"), dict):
        fn = openai_schema["function"]
        return {
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }
    # 已经是 Anthropic 风格，直接返回
    return openai_schema


def _anthropic_tools_for_agent(agent_uuid: str) -> list:
    """收集 Agent 有权限使用的所有工具，并转成 Anthropic 格式"""
    schemas = tool_registry.get_all_tools_schema(agent_uuid) or []
    out = []
    for s in schemas:
        if s:
            out.append(_openai_tool_to_anthropic(s))
    return out

# ======================================================================
# 3. 流处理助手
# ======================================================================

def _extract_text(blocks: Optional[List[dict]]) -> str:
    if not blocks:
        return ""
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _extract_tool_uses(blocks: Optional[List[dict]]) -> List[dict]:
    if not blocks:
        return []
    out = []
    for b in blocks:
        if b.get("type") == "tool_use":
            out.append({
                "id": b.get("id"),
                "name": b.get("name"),
                "input": b.get("input") or {},
            })
    return out


# ======================================================================
# 4. 主类
# ======================================================================

class AnthropicAgent:
    """
    以 Anthropic Messages API 为核心的 Agent 基类。

    必填类属性:
        prompt        : 系统提示词
        api_provider  : base URL（默认 ``https://api.anthropic.com``，可指向任意
                        兼容 Anthropic Messages API 的服务，详见下文"自定义服务商"）
        model_name    : 模型名，如 ``claude-3-5-sonnet-latest``
        api_key       : ``x-api-key`` 头的值
    可选类属性:
        max_tokens        : 单次响应上限（默认 4096）
        stream            : 是否流式（默认 True）
        fc_model          : 兼容位，Anthropic 原生 tool_use，永远为 True
        anthropic_version : API 版本头（默认 ``2023-06-01``）

    自定义服务商
    ------------
    ``AnthropicAgent`` 不只面向官方 ``api.anthropic.com``，可指向任意兼容
    Anthropic Messages API 的服务：

    1. **官方 Anthropic** —— 留空 ``api_provider`` 即可，默认走 ``https://api.anthropic.com``
    2. **第三方代理 / 加速网关** —— ``api_provider = "https://your-proxy.example.com"``
    3. **完整 endpoint** —— ``api_provider = "https://your-proxy.example.com/v1/messages"``
    4. **OpenAI 兼容网关的 anthropic 路径** —— ``api_provider = "https://your-gateway.com/anthropic"``
    5. **AWS Bedrock** —— ``api_provider = "bedrock-runtime.<region>.amazonaws.com"``
       （需要额外 header，可在子类 ``__init__`` 里覆盖 ``self.headers``）

    框架的 :py:meth:`_endpoint` 会智能拼接：
        * 末尾是 ``/v1/messages`` → 原样使用
        * 末尾是 ``/v1``         → 拼上 ``/messages``
        * 其他                   → 拼上 ``/v1/messages``

    如果网关要求额外 header（如 ``Authorization: Bearer xxx`` / 租户 ID），在子类
    ``__init__`` 里覆盖 ``self.headers`` 即可：

        class MyAgent(AnthropicAgent):
            api_provider = "https://your-gateway.example.com/anthropic"
            api_key = "internal-tenant-key"

            def __init__(self, new_load=True):
                super().__init__(new_load=new_load)
                self.headers["X-Tenant-Id"] = "tenant-001"
                # 如果网关用 Bearer 而非 x-api-key：
                # self.headers["Authorization"] = f"Bearer {self.api_key}"
                # self.headers.pop("x-api-key", None)

    完整示例见 ``examples/example6_anthropic_custom_provider.py``。
    """

    # ---- 类属性默认值 ----
    prompt: Optional[str] = None
    api_provider: Optional[str] = "https://api.anthropic.com"
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    max_tokens: int = 4096
    fc_model: bool = True  # Anthropic 原生支持 tool_use；保留兼容位
    stream: bool = True
    description: Optional[str] = None
    anthropic_version: str = "2023-06-01"
    tool_timeout: float = 60.0  # 工具调用单次超时；超时即转后台 future
    tool_max_workers: int = 8  # 工具执行线程池大小

    # ---------------- 类层级钩子：子类覆盖 @builtin_tool 方法时自动复用父类的 meta ----------------
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        _builtin_promote_overrides(cls)

    # ---------------- 初始化 ----------------
    def __init__(self, new_load: bool = True):
        self.uuid = self.__class__.uuid
        self.name = self.__class__.name
        self.stream_run = False
        self.current_task_id = None
        self.tool_call_hooks: list = []
        # 工具执行线程池：超时即转后台 future，让 LLM 可以不阻塞等
        from .tool_runner import ToolRunner
        self._tool_runner = ToolRunner(
            timeout=self.tool_timeout,
            max_workers=self.tool_max_workers,
        )

        # 把 uuid -> name 关系登记进 tool_registry，复用 BaseAgent 的 ACL
        agent_name = getattr(self.__class__, "name", None) or getattr(self.__class__, "__name__", None)
        if agent_name and self.uuid:
            tool_registry.register_agent_uuid(self.uuid, agent_name)

        # 拼 system prompt（与 BaseAgent 一致的注入顺序）
        self._build_system_prompt()

        # Anthropic 风格 history：不含 system，元素形如 {role, content (str 或 block 列表)}
        if new_load:
            self.history = []
        else:
            self.history = []

        self.headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": self.anthropic_version,
            "Content-Type": "application/json",
        }

        self.os_name = platform.system()
        self.conversations_folder = os.getcwd()
        if self.os_name == "Windows":
            self.os_main_folder = os.getenv("USERPROFILE")
        elif self.os_name == "Linux":
            self.os_main_folder = os.path.expanduser("~")
        elif self.os_name == "Darwin":
            self.os_main_folder = os.getenv("HOME")

        threading.Thread(target=self._connectivity, daemon=True).start()

    # ---------------- system prompt 拼装 ----------------
    def _build_system_prompt(self) -> None:
        """拼装顺序：cls.prompt + 工具清单 + Skills 描述 + uuid 尾巴"""
        tools_info = tool_registry.get_all_tools_info(self.uuid)
        tools_prompt = ""
        tools_list = list(tools_info.keys())

        # 自动收集 @builtin_tool 装饰的内置工具
        builtin_schemas = tool_registry.collect_builtin_tools(self)
        builtin_names = {s["function"]["name"] for s in builtin_schemas}
        for name in builtin_names:
            if name not in tools_list:
                tools_list.append(name)

        if tools_list:
            tools_prompt = "\n\n你可以使用以下工具：\n"
            # 注册工具：描述来自工具注册时
            for name, info in tools_info.items():
                tools_prompt += f"- {name}: {info['description']}\n"
            # 内置工具（描述自动来自 @builtin_tool 装饰器）
            for s in builtin_schemas:
                n = s["function"]["name"]
                if n in tools_list and n not in tools_info:
                    tools_prompt += f"- {n}: {s['function']['description']}\n"
            tools_prompt += (
                "\n注意：本 Agent 走 Anthropic 协议，请通过 tool_use 块调用上述工具；"
                "不要使用 XML 标签。"
            )

        # Skills 信息（与 BaseAgent 同一段）
        try:
            from .skill import skill_registry
            tools_prompt += skill_registry.get_skills_prompt_text(self.uuid)
        except ImportError:
            pass

        self.system_prompt = (self.prompt or "") + tools_prompt + ", 你的uuid " + str(self.uuid)
        logger.debug(
            f"AnthropicAgent {self.name} 初始化，系统提示词长度：{len(self.system_prompt)}"
        )

    # ---------------- 通用辅助 ----------------
    def _generate_task_id(self) -> str:
        return str(_uuid.uuid4())

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _endpoint(self) -> str:
        """根据 api_provider 拼出 messages endpoint URL"""
        base = (self.api_provider or "https://api.anthropic.com").rstrip("/")
        if base.endswith("/v1/messages"):
            return base
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"

    # ---------------- 钩子 ----------------
    def register_tool_hook(self, hook_func):
        """
        注册工具调用钩子。hook 签名：
            hook(event_type, tool_name, tool_args, tool_result, task_id)
        event_type 取值：'before' | 'after' | 'error'
        """
        self.tool_call_hooks.append(hook_func)

    def _execute_hooks(self, event_type, tool_name, tool_args, tool_result=None):
        for hook in self.tool_call_hooks:
            try:
                hook(
                    event_type=event_type,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=tool_result,
                    task_id=self.current_task_id,
                )
            except Exception as e:
                logger.error(f"钩子执行失败：{e}")

    # ---------------- 连通性测试 ----------------
    def _connectivity(self):
        """异步触发一次 ping；不阻塞主流程"""
        from .errors import APIError
        from .http_utils import HTTPClient

        try:
            client = HTTPClient()
            rsp = client.post(
                self._endpoint(),
                headers=self.headers,
                json={
                    "model": self.model_name,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                max_retries=0,
            )
            if 200 <= rsp.status_code < 300:
                logger.info(f"{self.name} Anthropic 连接正常")
            else:
                logger.error(
                    f"{self.name} Anthropic 连接测试未通过：{rsp.status_code} {rsp.text[:200]}"
                )
        except APIError as e:
            logger.error(f"{self.name} Anthropic 连接测试未通过：{e}")
        except Exception as e:
            logger.error(f"{self.name} Anthropic 连接异常：{e}")

    # ---------------- 输出回调（默认实现，可被重写） ----------------
    def out(self, content: dict):
        """默认打印；通过重写可劫持输出"""
        if content.get("tool_name"):
            print(
                f"\n[工具] {content.get('tool_name')} 参数={content.get('tool_parameter')}"
            )
            return
        if content.get("task"):
            print(f"\n[完成] {content.get('message', '')}")
            return
        if content.get("message") is not None:
            print(content.get("message"), end="")

    # ---------------- 主对话入口 ----------------
    def conversation_with_tool(self, messages=None, tool: bool = False, images=None):
        """
        发起一次对话；遇到 tool_use 时自动执行并继续迭代，
        直至 end_turn / max_tokens / 显式 attempt_completion。

        工具调用走 ``ToolRunner``：超过 ``tool_timeout`` 自动转后台 future，
        允许长线任务不阻塞对话循环（无 ``max_tool_turns`` 熔断）。

        Args:
            messages: 字符串、消息 dict、消息 list
            tool    : True 表示由 ask_for_help 内部递归，不要再加 user 消息
            images  : 图片 URL 或 base64（仅在 messages 为 str 时生效）
        """
        work_history = self.history

        # 1. 拼新一轮的 user 消息
        if not tool and messages is not None:
            user_msg = self._build_user_message(messages, images)
            work_history.append(user_msg)

        # 2. 准备 tools schema（一次收集，每轮复用）
        tools_schema = self._collect_tools_schema()

        # 3. 通过 transport 调 LLM
        transport = HttpxAnthropicTransport(
            endpoint=self._endpoint(),
            api_key=self.api_key,
            anthropic_version=self.anthropic_version,
            max_tokens=self.max_tokens,
        )

        # 4. tool_use 循环（无熔断；只受 attempt_completion / 用户主动结束影响）
        full_text = ""
        while True:  # noqa: PLR1700 — 设计上无限循环，靠 LLM 自行 attempt_completion 结束
            req = ChatRequest(
                model=self.model_name,
                system=self.system_prompt,
                messages=list(work_history),
                tools=tools_schema,
                stream=self.stream,
                max_tokens=self.max_tokens,
            )

            assistant_blocks: list = []
            tool_uses: list = []

            try:
                if self.stream:
                    for evt in transport.chat_stream(req):
                        if evt.type == "text":
                            assistant_blocks.append({"type": "text", "text": evt.text})
                            self.out({"message": evt.text})
                        elif evt.type == "tool_call" and evt.tool_call is not None:
                            tool_uses.append({
                                "id": evt.tool_call.id,
                                "name": evt.tool_call.name,
                                "input": evt.tool_call.arguments,
                            })
                        elif evt.type == "usage" and evt.usage is not None:
                            logger.debug(
                                f"{self.name} usage: in={evt.usage.prompt_tokens} out={evt.usage.completion_tokens}"
                            )
                else:
                    llm_rsp: LLMResponse = transport.chat(req)
                    for tc in llm_rsp.tool_calls:
                        tool_uses.append({
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                    if llm_rsp.text:
                        full_text += llm_rsp.text
                        self.out({"message": llm_rsp.text})
                    if llm_rsp.usage is not None:
                        logger.debug(
                            f"{self.name} usage: in={llm_rsp.usage.prompt_tokens} out={llm_rsp.usage.completion_tokens}"
                        )
            except Exception as e:
                logger.error(f"{self.name} Anthropic 调用失败：{e}")
                raise

            # 若流式模式且没有显式 text 块，也存一个 text 块占位
            if self.stream and not any(b.get("type") == "text" for b in assistant_blocks) and full_text:
                assistant_blocks.append({"type": "text", "text": full_text})

            work_history.append({"role": "assistant", "content": assistant_blocks})
            if not tool_uses:
                break

            # 5. 顺序执行每一个 tool_use（走 ToolRunner，支持超时转后台）
            tool_results: list = []
            for tu in tool_uses:
                self.current_task_id = self._generate_task_id()
                self._execute_hooks("before", tu["name"], tu["input"])
                self.out({"tool_name": tu["name"], "tool_parameter": tu["input"]})
                try:
                    result, async_id = self._dispatch_tool(tu["name"], tu["input"])
                except Exception as e:
                    result = f"工具执行失败：{e}"
                    self._execute_hooks("error", tu["name"], tu["input"], result)
                else:
                    self._execute_hooks("after", tu["name"], tu["input"], result)
                    self.out({"tool_result": result, "tool_name": tu["name"]})

                # 超时 → 转后台
                if async_id is not None:
                    result = (
                        f"[tool {tu['name']} still running in background as task_id={async_id}; "
                        f"check via self._tool_runner.get_status('{async_id}') "
                        f"or wait via self._tool_runner.wait('{async_id}')]"
                    )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                })

            # 6. 把所有 tool_result 装回 user 消息
            work_history.append({"role": "user", "content": tool_results})

        # 返回最后一轮 assistant 的纯文本
        last = work_history[-1].get("content", []) if work_history else []
        return "".join(b.get("text", "") for b in last if b.get("type") == "text")

    async def aconversation_with_tool(self, messages=None, tool: bool = False, images=None):
        """
        异步版 conversation_with_tool（基于 transport.achat_stream）。

        用法::

            import asyncio
            result = asyncio.run(agent.aconversation_with_tool("hi"))

        注意：tool_runner 仍是 ThreadPoolExecutor（同步执行工具），但
        LLM 调用走 async，事件循环不被阻塞。
        """
        work_history = self.history

        if not tool and messages is not None:
            user_msg = self._build_user_message(messages, images)
            work_history.append(user_msg)

        tools_schema = self._collect_tools_schema()
        transport = HttpxAnthropicTransport(
            endpoint=self._endpoint(),
            api_key=self.api_key,
            anthropic_version=self.anthropic_version,
            max_tokens=self.max_tokens,
        )

        full_text = ""
        while True:
            req = ChatRequest(
                model=self.model_name,
                system=self.system_prompt,
                messages=list(work_history),
                tools=tools_schema,
                stream=self.stream,
                max_tokens=self.max_tokens,
            )

            assistant_blocks: list = []
            tool_uses: list = []

            try:
                if self.stream:
                    async for evt in transport.achat_stream(req):
                        if evt.type == "text":
                            assistant_blocks.append({"type": "text", "text": evt.text})
                            self.out({"message": evt.text})
                        elif evt.type == "tool_call" and evt.tool_call is not None:
                            tool_uses.append({
                                "id": evt.tool_call.id,
                                "name": evt.tool_call.name,
                                "input": evt.tool_call.arguments,
                            })
                else:
                    llm_rsp: LLMResponse = await transport.achat(req)
                    for tc in llm_rsp.tool_calls:
                        tool_uses.append({
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })
                    if llm_rsp.text:
                        full_text += llm_rsp.text
                        self.out({"message": llm_rsp.text})
            except Exception as e:
                logger.error(f"{self.name} Anthropic 异步调用失败：{e}")
                raise

            work_history.append({"role": "assistant", "content": assistant_blocks})
            if not tool_uses:
                break

            tool_results: list = []
            for tu in tool_uses:
                self.current_task_id = self._generate_task_id()
                self._execute_hooks("before", tu["name"], tu["input"])
                self.out({"tool_name": tu["name"], "tool_parameter": tu["input"]})
                try:
                    result, async_id = self._dispatch_tool(tu["name"], tu["input"])
                except Exception as e:
                    result = f"tools execution failed: {e}"
                    self._execute_hooks("error", tu["name"], tu["input"], result)
                else:
                    self._execute_hooks("after", tu["name"], tu["input"], result)
                    self.out({"tool_result": result, "tool_name": tu["name"]})

                if async_id is not None:
                    result = (
                        f"[tool {tu['name']} still running in background as task_id={async_id}]"
                    )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                })

            work_history.append({"role": "user", "content": tool_results})

        last = work_history[-1].get("content", []) if work_history else []
        return "".join(b.get("text", "") for b in last if b.get("type") == "text")

    def _build_user_message(self, messages, images) -> dict:
        """把用户输入规范成 Anthropic user message"""
        if isinstance(messages, dict):
            return messages
        if isinstance(messages, list):
            # 直接取最后一个 user 消息
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    return m
            if messages and isinstance(messages[-1], dict):
                return messages[-1]
        # str：拼 content blocks
        content: list = []
        if images:
            for img in images:
                if isinstance(img, str) and img.startswith(("http://", "https://")):
                    content.append({
                        "type": "image",
                        "source": {"type": "url", "url": img},
                    })
                else:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": str(img),
                        },
                    })
        content.append({"type": "text", "text": str(messages)})
        return {"role": "user", "content": content}

    def _collect_tools_schema(self) -> list:
        """汇总 Anthropic 工具 schema：注册工具 + 自动构建的内置工具 + Skills"""
        schemas = _anthropic_tools_for_agent(self.uuid)
        existing = {s.get("name") for s in schemas}

        # 自动从 @builtin_tool 装饰的方法构建（来源：tool_registry.collect_builtin_tools）
        for s in tool_registry.collect_builtin_tools(self):
            anth = _openai_tool_to_anthropic(s)
            if anth.get("name") not in existing:
                schemas.append(anth)
                existing.add(anth.get("name"))

        try:
            from .skill import skill_registry
            for schema in skill_registry.get_all_tool_schemas() or []:
                anth = _openai_tool_to_anthropic(schema)
                if anth.get("name") not in existing:
                    schemas.append(anth)
                    existing.add(anth.get("name"))
        except ImportError:
            pass

        return schemas

    # ---------------- API 调用层（已迁移到 LLM Transport）----------------
    # 旧 _call / _call_blocking / _call_stream 在 v0.2 里被 HttpxAnthropicTransport 取代。
    # 新代码请用：
    #     from dumplingsAI.llm_transport import HttpxAnthropicTransport
    #     transport = HttpxAnthropicTransport(...)
    #     rsp = transport.chat(req)  # 或 transport.chat_stream(req)
    def _call(self, payload):  # pragma: no cover - 已废弃
        raise NotImplementedError(
            "AnthropicAgent._call 已废弃；请通过 HttpxAnthropicTransport 调 LLM。"
        )

    # ---------------- 工具分发 ----------------
    def _dispatch_tool(self, name: str, arguments: dict):
        """
        工具调用优先级：
            1) Agent 自身被 ``@builtin_tool`` 装饰的同名方法
            2) ``tool_registry`` 中已注册的工具

        实际执行走 ``self._tool_runner``（``ThreadPoolExecutor``）：
        - ``tool_timeout`` 秒内完成 → 直接返回 (result, None)
        - 超时 → 后台 future 继续跑，返回 (None, task_id)
        - LLM 拿到 task_id 描述，可继续做别的

        入参先经 Pydantic 校验（如果 @builtin_tool 提供了 params_model）。
        """
        # Pydantic 校验
        from .agent_tool import _validate_tool_args_for
        arguments = _validate_tool_args_for(self, name, arguments)

        # 1) 内置工具
        builtin_names = {s["function"]["name"] for s in tool_registry.collect_builtin_tools(self)}
        if name in builtin_names:
            method = getattr(self, name, None)
            if callable(method):
                if arguments:
                    result, async_id = self._tool_runner.submit(
                        method, tool_name=name, timeout=self.tool_timeout, **arguments,
                    )
                else:
                    result, async_id = self._tool_runner.submit(
                        method, tool_name=name, timeout=self.tool_timeout,
                    )
                return result, async_id
        # 2) tool_registry
        if tool_registry.check_permission(self.uuid, name):
            tool_info = tool_registry.get_tool_info(name)
            if tool_info:
                func = tool_info["function"]
                try:
                    result, async_id = self._tool_runner.submit(
                        func, tool_name=name, timeout=self.tool_timeout, **arguments,
                    )
                except TypeError:
                    # 兼容 XML 风格工具：传 xml=str(arguments)
                    result, async_id = self._tool_runner.submit(
                        func, tool_name=name, timeout=self.tool_timeout, xml=str(arguments),
                    )
                return result, async_id
        return (f"找不到工具：{name}", None)

    # ---------------- 内置工具（@builtin_tool 装饰器自动从签名推导 schema）----------------

    @builtin_tool(
        description="请求其他Agent帮助，调用另一个Agent完成子任务并把它的回复作为工具结果返回。",
        params={
            "agent_id": "目标Agent的UUID或名称（已在 Dumplings.agent_list 中注册）",
            "message": "要发送给目标Agent的内容",
        },
    )
    def ask_for_help(self, agent_id: str, message: str) -> str:
        """请求另一个 Agent 协助（支持 UUID 或名称）

        走全局队列：避免递归栈过深 / 循环调用自动超时。
        - 循环检测：若 target 在当前调用链里直接拒绝
        - 深度限制：链长达到 max_depth 也直接拒绝
        - 串行执行：worker pool 中每个 worker 一次只跑一个 Job
        """
        from .Agent_list import agent_list
        from .agent_queue import get_call_chain, get_default_queue

        target = agent_list.get(agent_id)
        if target is None:
            target = next(
                (a for a in agent_list.values() if a.name == agent_id),
                None,
            )
        if target is None:
            return f"未找到 Agent：{agent_id}"
        try:
            chain = get_call_chain()
            queue = get_default_queue()
            return queue.submit(
                target_uuid=target.uuid,
                call_fn=lambda: str(target.conversation_with_tool(message)),
                caller_chain=chain,
            )
        except Exception as e:
            logger.error(f"ask_for_help 失败：{e}")
            return f"协助请求失败：{e}"

    @builtin_tool(
        description="列出当前系统内所有已注册的Agent及其UUID/名称，便于发现协作对象。",
    )
    def list_agents(self) -> str:
        """列出全部已注册 Agent"""
        from .Agent_list import agent_list
        lines = []
        seen = set()
        for key, inst in agent_list.items():
            uuid = getattr(inst, "uuid", None)
            if uuid and uuid == key and uuid not in seen:
                seen.add(uuid)
                lines.append(f"- name={inst.name} uuid={uuid}")
        return "\n".join(lines) if lines else "(无已注册 Agent)"

    @builtin_tool(
        description="标记当前任务完成并退出对话循环；可附 report_content 作为最终汇报。",
        params={"report_content": "最终汇报内容（可空）"},
    )
    def attempt_completion(self, report_content: str = "") -> str:
        """标记任务完成并触发 out()，返回 report_content"""
        self.out({"task": True, "message": report_content or ""})
        return report_content or ""

    @builtin_tool(
        description="重新拉取你自己当前可用的工具/技能列表，重置系统提示词；当你认为环境已变、想刷新时调用。",
    )
    def reload(self) -> str:
        """重新拼装 system_prompt 并清空 history"""
        self._build_system_prompt()
        self.history = []
        logger.info(f"{self.name} 已 reload")
        return "reloaded"
