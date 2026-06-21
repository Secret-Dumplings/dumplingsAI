"""
示例 1：单 Agent 基础用法

这个示例演示如何创建一个简单的 Agent 并与之对话。
"""
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()


@dumplingsAI.register_agent("001", "simple_agent")
class SimpleAgent(dumplingsAI.BaseAgent):
    """一个简单的问答助手 Agent"""
    prompt = "你是一个名为汤圆 AI 的简单问答助手，友好地回答用户的问题"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")


if __name__ == "__main__":
    # 获取 Agent 实例
    agent = dumplingsAI.agent_list["simple_agent"]

    # 开始对话
    print("=== 简单 Agent 示例 ===")
    agent.conversation_with_tool("你好，请介绍一下你自己")