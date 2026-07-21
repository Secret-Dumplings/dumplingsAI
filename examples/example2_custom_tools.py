"""
示例 2：带自定义工具的 Agent

这个示例演示如何注册自定义工具并让 Agent 使用。
"""
import os

import dumplingsAI
from dotenv import load_dotenv

load_dotenv()


# ==================== 注册工具 ====================

@dumplingsAI.tool_registry.register_tool(
    allowed_agents=["calculator"],
    name="add",
    description="计算两个数的和",
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "第一个数"},
            "b": {"type": "number", "description": "第二个数"}
        },
        "required": ["a", "b"]
    }
)
def add(a: float, b: float) -> str:
    """加法计算"""
    return f"结果：{a + b}"


@dumplingsAI.tool_registry.register_tool(
    allowed_agents=["calculator"],
    name="multiply",
    description="计算两个数的乘积",
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "第一个数"},
            "b": {"type": "number", "description": "第二个数"}
        },
        "required": ["a", "b"]
    }
)
def multiply(a: float, b: float) -> str:
    """乘法计算"""
    return f"结果：{a * b}"


# ==================== 注册 Agent ====================

@dumplingsAI.register_agent("002", "calculator")
class CalculatorAgent(dumplingsAI.BaseAgent):
    """一个可以执行数学运算的计算器 Agent"""
    prompt = "你是一个计算器助手，可以使用 add 和 multiply 工具执行数学运算"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = os.getenv("OPENAI_MODEL")
    api_key = os.getenv("API_KEY")


if __name__ == "__main__":
    agent = dumplingsAI.agent_list["calculator"]

    print("=== 计算器 Agent 示例 ===")

    # 测试加法
    print("\n[测试加法]")
    agent.conversation_with_tool("请帮我计算 123 + 456")

    # 测试乘法
    print("\n[测试乘法]")
    agent.conversation_with_tool("请帮我计算 12 × 34")
