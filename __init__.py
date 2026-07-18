# -*- coding: utf-8 -*-
"""
dumplingsAI - 多智能体协作框架
============================

基于 LLM 的轻量级多智能体协作系统框架。

快速开始
--------
>>> import dumplingsAI
>>> @dumplingsAI.register_agent("uuid", "name")
... class MyAgent(dumplingsAI.BaseAgent):
...     prompt = "你是一个助手"
...     api_provider = "https://api.example.com/v1/chat/completions"
...     model_name = "qwen3.5-plus"
...     api_key = "your-api-key"
>>> agent = dumplingsAI.agent_list["name"]
>>> agent.conversation_with_tool("你好")

核心组件
--------
- register_agent : Agent 注册装饰器
- tool_registry : 工具注册器实例
- BaseAgent : Agent 基类
- agent_list : 已注册 Agent 字典
- mcp_bridge : MCP 服务器集成模块
- skill : Agent Skills 开放标准集成

示例代码
--------
更多示例请查看 examples 文件夹：
- example1_basic.py : 单 Agent 基础用法
- example2_custom_tools.py : 注册自定义工具
- example3_multi_agent.py : 多 Agent 协作
- example4_mcp.py : MCP 服务器集成
- example5_custom_output.py : 自定义输出处理

许可证
-------
Apache License 2.0
"""

from .Agent_list import register_agent, agent_list
from .Agent_Base_ import Agent as BaseAgent
from .agent_tool import tool_registry, builtin_tool

# 从 mcp_bridge 导入 MCP 相关功能
try:
    from .mcp_bridge import (
        register_mcp_tools,
        register_mcp_tools_async,
        close_mcp_session,
        close_mcp_session_sync,
        close_all_mcp_sessions,
        close_all_mcp_sessions_sync,
        get_session_info,
        start_health_check,
        stop_health_check,
        mcp_session_context,
    )
except ImportError:
    # mcp 未安装时提供兼容性
    pass

# 从 skill 导入 Skill 相关功能
from .skill import skill_registry, Skill
from .skill_bridge import register_skill_as_tool, unregister_skill_from_tool

__version__ = "0.1.0"
__author__ = "secret_dumplings"
__all__ = [
    # 核心组件
    "register_agent",
    "tool_registry",
    "BaseAgent",
    "agent_list",
    # MCP 功能
    "register_mcp_tools",
    "register_mcp_tools_async",
    "close_mcp_session",
    "close_mcp_session_sync",
    "close_all_mcp_sessions",
    "close_all_mcp_sessions_sync",
    "get_session_info",
    "start_health_check",
    "stop_health_check",
    "mcp_session_context",
    # Skill 功能
    "skill_registry",
    "Skill",
    "register_skill_as_tool",
    "unregister_skill_from_tool",
    # 元信息
    "__version__",
    "__author__",
]


def help():
    """
    打印 dumplingsAI 框架的帮助信息和使用示例。

    示例:
        >>> import dumplingsAI
        >>> dumplingsAI.help()
    """
    import sys
    # Windows 终端兼容性：设置 UTF-8 编码
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("""
+======================================================================+
|                    dumplingsAI 多智能体协作框架                          |
|                         快速上手指南                                  |
+======================================================================+

[1. 创建第一个 Agent]

    @dumplingsAI.register_agent("unique-uuid", "my_agent")
    class MyAgent(dumplingsAI.BaseAgent):
        prompt = "你是一个智能助手"
        api_provider = "https://api.example.com/v1/chat/completions"
        model_name = "qwen3.5-plus"
        api_key = os.getenv("API_KEY")

    # 获取并运行
    agent = dumplingsAI.agent_list["my_agent"]
    agent.conversation_with_tool("你好")

[2. 注册自定义工具]

    @dumplingsAI.tool_registry.register_tool(
        allowed_agents=None,  # None 表示所有 Agent 可用
        name="get_weather",
        description="查询天气",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名"}
            },
            "required": ["city"]
        }
    )
    def get_weather(city: str) -> str:
        return f"{city}今天晴朗"

[3. 多 Agent 协作]

    # Agent 可以使用内置工具请求其他 Agent 帮助
    agent.conversation_with_tool(
        "请请求 time_agent 帮你查看当前时间"
    )

[4. 查看可用资源]

    # 查看所有注册工具
    dumplingsAI.tool_registry.list_tools()

    # 查看 Agent 可用工具
    agent = dumplingsAI.agent_list["my_agent"]
    agent.get_all_available_tools()

    # 查看所有 Agent
    list(dumplingsAI.agent_list.keys())

[5. MCP 集成]

    # 注册 MCP 服务器工具
    dumplingsAI.register_mcp_tools(
        server_path="path/to/mcp_server.py",
        allowed_agents=["my_agent"]
    )

[6. Skills 集成]

    # 扫描并注册 Skills（支持 .claude/skills/ 目录）
    from pathlib import Path
    dumplingsAI.skill_registry.scan_and_register([Path(".")])

    # 直接注册单个 Skill 目录
    dumplingsAI.skill_registry.register_skill(Path(".claude/skills/my-skill"))

    # Agent 自动发现并使用 Skills（通过 tool_registry 桥接）
    # Skills 会出现在 Agent 的工具列表中，可通过 Function Calling 调用

    # 查询 Skills
    dumplingsAI.skill_registry.list_skills()
    dumplingsAI.skill_registry.search_skills("关键词")

[配置说明]

    在项目根目录创建 .env 文件:
    API_KEY=your_api_key_here

[示例代码]

    查看 examples 文件夹获取更多示例:
    - examples/example1_basic.py      单 Agent 基础
    - examples/example2_custom_tools.py 自定义工具
    - examples/example3_multi_agent.py  多 Agent 协作
    - examples/example4_mcp.py         MCP 集成
    - examples/example5_custom_output.py 自定义输出

[文档]

    完整文档请查看 README.md

[许可证]

    Apache License 2.0
""")


# 方便交互式访问
help.__doc__ = __doc__