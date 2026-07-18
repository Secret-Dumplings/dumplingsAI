# -*- coding: utf-8 -*-
"""
冒烟测试：确保包能被导入。

CI 环境在没有真实 LLM API Key 时无法跑端到端 Agent，所以这里只验证
"包能被 import、核心类存在、装饰器/收集器工作"。
"""


def test_package_importable():
    import dumplingsAI  # noqa: F401


def test_core_exports():
    import dumplingsAI
    for name in ("BaseAgent", "register_agent",
                 "tool_registry", "builtin_tool", "agent_list",
                 "skill_registry", "register_mcp_tools", "skill_bridge"):
        assert hasattr(dumplingsAI, name), f"missing export: {name}"

    # AnthropicAgent 走子模块路径
    from dumplingsAI.anthropic_agent import AnthropicAgent  # noqa: F401


def test_builtin_tool_decorator_extracts_schema():
    from dumplingsAI import builtin_tool

    @builtin_tool(
        description="求两数之和",
        params={"a": "第一个加数", "b": "第二个加数"},
    )
    def add(self, a: float, b: float) -> float:
        """加法示例"""
        return a + b

    meta = add.__builtin_tool_meta__
    assert meta["name"] == "add"
    assert meta["description"] == "求两数之和"
    schema = meta["input_schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"].keys()) == {"a", "b"}
    assert set(schema["required"]) == {"a", "b"}
    # 类型注解 → JSON type
    assert schema["properties"]["a"]["type"] == "number"
    assert schema["properties"]["b"]["type"] == "number"


def test_collect_builtin_tools_picks_up_base_methods():
    """BaseAgent 上的 4 个内置方法应该被自动收集到 schema。"""
    from dumplingsAI import tool_registry

    class _Fake:
        pass

    # 临时挂一个空类继承 BaseAgent，绕过 __init__ 的网络连通性测试
    from dumplingsAI.Agent_Base_ import Agent as BaseAgent

    class Demo(BaseAgent):
        # 跳过 __init__：直接绕开
        def __init__(self):  # noqa: D401
            pass

    inst = Demo()
    schemas = tool_registry.collect_builtin_tools(inst)
    names = {s["function"]["name"] for s in schemas}
    assert "ask_for_help" in names
    assert "list_agents" in names
    assert "attempt_completion" in names
    assert "reload" in names
