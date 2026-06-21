"""
示例 4：MCP 服务器集成

这个示例演示如何注册 MCP 服务器的工具。
"""
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()


@dumplingsAI.register_agent("mcp-agent", "mcp_agent")
class MCPAgent(dumplingsAI.BaseAgent):
    """可以使用 MCP 服务器工具的 Agent"""
    prompt = "你是一个可以使用 MCP 服务器工具的助手"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")


if __name__ == "__main__":
    # 注册 MCP 服务器的所有工具
    # 注意：需要修改为实际的 MCP 服务器路径
    # dumplingsAI.register_mcp_tools(
    #     server_path="path/to/mcp_server.py",
    #     register_resources=True,
    #     allowed_agents=["mcp_agent"]
    # )

    print("=== MCP 集成示例 ===")
    print("取消注释并修改 server_path 以使用 MCP 功能")