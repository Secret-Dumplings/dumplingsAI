"""
示例 3：多 Agent 协作

这个示例演示如何创建多个 Agent 并让它们相互协作。
"""
import os

import dumplingsAI
from dotenv import load_dotenv

load_dotenv()


# ==================== 时间查询 Agent ====================

@dumplingsAI.register_agent("time-uuid", "time_agent")
class TimeAgent(dumplingsAI.BaseAgent):
    """时间管理者，负责提供当前时间"""
    prompt = "你是时间管理者，当被询问时间时，直接回答当前时间是 2026 年 3 月 15 日"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")


# ==================== 调度 Agent ====================

@dumplingsAI.register_agent("schedule-uuid", "scheduling_agent")
class SchedulingAgent(dumplingsAI.BaseAgent):
    """调度助手，可以请求其他 Agent 帮助"""
    prompt = "你是一个调度助手，当需要查询时间时，使用 ask_for_help 工具请求 time_agent 帮助"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")


# ==================== 运行示例 ====================

if __name__ == "__main__":
    print("=== 多 Agent 协作示例 ===")

    # 获取调度 Agent
    scheduler = dumplingsAI.agent_list["scheduling_agent"]

    # 请求调度 Agent 查询时间（它会向 time_agent 求助）
    scheduler.conversation_with_tool(
        "你好，请请求 time_agent 帮你查看当前时间"
    )

    # 查看已注册的 Agent
    print("\n=== 已注册的 Agent ===")
    print(dumplingsAI.tool_registry.list_tools())
