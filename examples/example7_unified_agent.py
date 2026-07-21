"""
示例：协议无关的 Agent 工厂

从 v0.2.2 起，可以不直接选基类（BaseAgent / AnthropicAgent），
而是继承统一的 ``dumplingsAI.Agent``，靠 ``protocol`` 字段决定实际协议。

本文件对比：
1. 直接选基类（旧写法，仍兼容）
2. 用 Agent + protocol 字段（新写法，可配置）
3. 用 Agent + protocol 字段做动态切换
"""
import os
import uuid

import dumplingsAI
from dotenv import load_dotenv

load_dotenv()


# ---------- 方式 1（旧写法，仍然兼容） ----------
@dumplingsAI.register_agent(uuid.uuid4().hex, "openai_legacy")
class OpenAILegacy(dumplingsAI.BaseAgent):
    """直接继承 BaseAgent —— OpenAI 协议"""
    prompt = "你是一个助手"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = os.getenv("OPENAI_MODEL")
    api_key = os.getenv("API_KEY")


@dumplingsAI.register_agent(uuid.uuid4().hex, "anthropic_legacy")
class AnthropicLegacy(dumplingsAI.anthropic_agent.AnthropicAgent):
    """直接继承 AnthropicAgent —— Anthropic 协议"""
    prompt = "你是一个助手"
    model_name = os.getenv("ANTHROPIC_MODEL")
    api_key = os.getenv("ANTHROPIC_API_KEY")


# ---------- 方式 2（新写法，Agent + protocol 字段） ----------
@dumplingsAI.register_agent(uuid.uuid4().hex, "openai_factory")
class OpenAIViaFactory(dumplingsAI.Agent):
    """用 Agent + protocol 选 OpenAI"""
    protocol = "openai"  # 这一行决定实际继承 BaseAgent
    prompt = "你是一个助手"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = os.getenv("OPENAI_MODEL")
    api_key = os.getenv("API_KEY")


@dumplingsAI.register_agent(uuid.uuid4().hex, "anthropic_factory")
class AnthropicViaFactory(dumplingsAI.Agent):
    """用 Agent + protocol 选 Anthropic"""
    protocol = "anthropic"  # 这一行决定实际继承 AnthropicAgent
    prompt = "你是一个助手"
    model_name = os.getenv("ANTHROPIC_MODEL")
    api_key = os.getenv("ANTHROPIC_API_KEY")


# ---------- 方式 3（动态切换：根据配置决定协议） ----------
# Python class body 的 name resolution 不会向 enclosing scope 找同名变量，
# 所以这里把协议值取到不冲突的变量名 ``_proto`` 上，再在类里用 ``protocol = _proto``。
def _make_agent(name: str):
    """从环境变量读协议，动态构造 Agent 类。"""
    _proto = os.getenv("AGENT_PROTOCOL")
    if _proto is not None:
        _proto = _proto.lower()
    _model = os.getenv("AGENT_MODEL")
    _provider = os.getenv("AGENT_API_PROVIDER")

    @dumplingsAI.register_agent(uuid.uuid4().hex, name)
    class DynamicAgent(dumplingsAI.Agent):
        protocol = _proto
        prompt = "你是一个助手"
        model_name = _model
        api_provider = _provider
        api_key = os.getenv("API_KEY")

    return DynamicAgent


DynamicAgentCls = _make_agent("dynamic_agent")


if __name__ == "__main__":
    from dumplingsAI import BaseAgent
    from dumplingsAI.anthropic_agent import AnthropicAgent

    # 验证派发结果
    cases = [
        ("OpenAILegacy",       OpenAILegacy,       BaseAgent),
        ("AnthropicLegacy",    AnthropicLegacy,    AnthropicAgent),
        ("OpenAIViaFactory",   OpenAIViaFactory,   BaseAgent),
        ("AnthropicViaFactory",AnthropicViaFactory,AnthropicAgent),
        ("DynamicAgentCls",    DynamicAgentCls,    None),  # 看环境变量
    ]
    for name, cls, expected_base in cases:
        bases = [b.__name__ for b in cls.__mro__ if b.__name__ in ("Agent", "BaseAgent", "AnthropicAgent")]
        ok = expected_base is None or issubclass(cls, expected_base)
        marker = "OK" if ok else "FAIL"
        print(f"[{marker}] {name:25s} MRO 含协议基类: {bases}")
    print()
    print("所有 Agent 都在 dumplingsAI.agent_list 里可用：")
    for name in sorted(dumplingsAI.agent_list):
        print(f"  - {name}")
