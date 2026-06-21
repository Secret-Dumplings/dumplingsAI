"""
示例 5：自定义输出处理

这个示例演示如何重写 Agent 的输出方法实现自定义处理。
"""
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()


@dumplingsAI.register_agent("custom-uuid", "custom_agent")
class CustomAgent(dumplingsAI.BaseAgent):
    """具有自定义输出处理的 Agent"""
    prompt = "你是一个具有自定义输出格式的助手"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

    def out(self, content):
        """重写输出方法，实现自定义处理"""
        if content.get("tool_name"):
            print(f"[TOOL] 调用：{content.get('tool_name')}")
            print(f"       参数：{content.get('tool_parameter')}")
            return
        if content.get("task"):
            print("\n[DONE] 任务完成")
        elif content.get("other"):
            print(f"[INFO] {content.get('message')}")
        else:
            print(content.get("message"), end="")


if __name__ == "__main__":
    agent = dumplingsAI.agent_list["custom_agent"]

    print("=== 自定义输出示例 ===")
    agent.conversation_with_tool("你好，请用你的方式回答")