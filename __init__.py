# -*- coding: utf-8 -*-
"""
dumplingsAI - 多智能体协作框架
==============================

基于 LLM 的轻量级多智能体协作系统框架，支持 OpenAI 兼容协议与 Anthropic 协议。

快速开始
--------

    import dumplingsAI
    from dotenv import load_dotenv
    load_dotenv()  # API_KEY 放在 .env

    @dumplingsAI.register_agent("uuid-1", "my_agent")
    class MyAgent(dumplingsAI.BaseAgent):
        prompt = "你是一个助手"
        api_provider = "https://api.example.com/v1/chat/completions"
        model_name = os.getenv("OPENAI_MODEL")
        api_key = os.getenv("OPENAI_API_KEY")

    agent = dumplingsAI.agent_list["my_agent"]
    agent.conversation_with_tool("你好")

Anthropic 协议用法::

    @dumplingsAI.register_agent("uuid-2", "claude_agent")
    class ClaudeAgent(dumplingsAI.Agent):
        protocol = "anthropic"
        prompt = "你是一个助手"
        model_name = os.getenv("ANTHROPIC_MODEL")
        api_key = os.getenv("ANTHROPIC_API_KEY")        # 可指向任意兼容端点（详见 AnthropicAgent docstring）

    dumplingsAI.agent_list["claude_agent"].conversation_with_tool("你好")

切协议只需改 ``protocol`` 字段（``"openai"`` / ``"anthropic"``），无需换基类。
详见 ``Agent`` docstring。

核心导出
--------

- ``register_agent`` : Agent 注册装饰器（双键：UUID + 名称）
- ``tool_registry``  : 工具注册器实例（@tool_registry.register_tool）
- ``Agent``          : **协议无关的 Agent 工厂基类**（用 ``protocol`` 字段选 openai / anthropic）
- ``BaseAgent``      : Agent 基类（OpenAI 协议，直接继承时使用）
- ``agent_list``     : 已注册 Agent 字典（按 UUID / 名称索引）
- ``anthropic_agent.AnthropicAgent`` : Anthropic 协议 Agent 基类（直接继承时使用）
- ``mcp_bridge``     : MCP 服务器集成（register_mcp_tools 等）
- ``skill``          : Agent Skills 开放标准集成

更多资源
--------

- 完整文档：https://github.com/Secret-Dumplings/dumplingsAI
- 示例代码：examples/ 目录
- 发布流程：仓库根目录 RELEASING.md
- 许可证：Apache License 2.0
"""

from .Agent_Base_ import Agent as BaseAgent
from .Agent_list import agent_list, register_agent
from .agent_tool import builtin_tool, tool_registry  # noqa: F401  (re-exported)

# 从 mcp_bridge 导入 MCP 相关功能
try:
    from .mcp_bridge import (
        close_all_mcp_sessions,
        close_all_mcp_sessions_sync,
        close_mcp_session,
        close_mcp_session_sync,
        get_session_info,
        mcp_session_context,
        register_mcp_tools,
        register_mcp_tools_async,
        start_health_check,
        stop_health_check,
    )
except ImportError:
    # mcp 未安装时提供兼容性
    pass

# 从 skill 导入 Skill 相关功能
from .skill import Skill, skill_registry
from .skill_bridge import register_skill_as_tool, unregister_skill_from_tool

# 版本号自动从包元数据读取，与 Dumplings/pyproject.toml 中的 version 字段保持同步。
# 覆盖方式（仅在打包失败等极端场景下使用）：import dumplingsAI; dumplingsAI.__version__ = "x"
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("dumplingsAI")
    except PackageNotFoundError:
        # 包未安装（极少见，例如源码直接以脚本运行）
        __version__ = "0.0.0+unknown"
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__author__ = "secret_dumplings"


# ======================================================================
# 协议无关的 Agent 工厂：``Agent`` + ``protocol`` 字段自动选基类
# ======================================================================
#
# 之前用户得手动区分 ``dumplingsAI.BaseAgent`` / ``dumplingsAI.anthropic_agent.AnthropicAgent``。
# 现在统一用 ``dumplingsAI.Agent``，靠类属性 ``protocol = "openai" | "anthropic"`` 决定实际继承谁。


class _ProtocolMeta(type):
    """协议分发 metaclass。

    当用户写 ``class MyAgent(dumplingsAI.Agent): protocol = "anthropic"`` 时，
    把 ``Agent`` 占位基类替换成对应的真实基类（``BaseAgent`` / ``AnthropicAgent``）。
    之后 ``MyAgent`` 实际是 ``AnthropicAgent`` 的子类，所有功能一致。
    """

    def __new__(mcs, name, bases, namespace, **kwargs):
        # Agent 是在 metaclass 之后才定义的；但 globals() 查找保证 __new__ 调用时
        # Agent 已存在（因为 Agent 类体在 metaclass 定义之后执行）。
        if globals().get("Agent") in bases:
            protocol = namespace.get("protocol", "openai")
            if not isinstance(protocol, str):
                raise TypeError(
                    f"{name}.protocol 必须是字符串，当前是 {type(protocol).__name__}"
                )
            real_base = _PROTOCOLS.get(protocol.lower())
            if real_base is None:
                raise ValueError(
                    f"{name}.protocol={protocol!r} 不支持。"
                    f"可选值：{sorted(_PROTOCOLS)}"
                )
            new_bases = tuple(real_base if b is Agent else b for b in bases)
            return super().__new__(mcs, name, new_bases, namespace, **kwargs)
        return super().__new__(mcs, name, bases, namespace, **kwargs)


# _PROTOCOLS 在 Agent 类定义之前就要准备好，否则 metaclass.__new__ 找不到真实基类。
# anthropic_agent 的 import 放这里，避免顶层 import 出错时整个包加载失败。
_PROTOCOLS = {"openai": BaseAgent, "anthropic": None}
try:
    from .anthropic_agent import AnthropicAgent as _AnthropicAgent
    _PROTOCOLS["anthropic"] = _AnthropicAgent
except ImportError:  # pragma: no cover
    _AnthropicAgent = None


class Agent(metaclass=_ProtocolMeta):
    """协议无关的 Agent 工厂基类。

    通过类属性 ``protocol`` 自动选择真实基类：

    =============  ====================================================
    ``protocol``   实际继承
    =============  ====================================================
    ``"openai"``   :class:`BaseAgent`（默认，OpenAI 兼容协议）
    ``"anthropic"``:class:`dumplingsAI.anthropic_agent.AnthropicAgent`
    =============  ====================================================

    用法::

        @dumplingsAI.register_agent("uuid-1", "my_agent")
        class MyAgent(dumplingsAI.Agent):
            protocol = "anthropic"   # 关键：决定继承哪个真实基类
            prompt = "你是一个助手"
            model_name = os.getenv("ANTHROPIC_MODEL")
            api_key = os.getenv("ANTHROPIC_API_KEY")

        # MyAgent 实际是 AnthropicAgent 的子类，行为与直接继承一致。
        # 切换协议只需改 protocol 字段，不需要换基类。

    兼容写法（直接选基类）仍然支持::

        @dumplingsAI.register_agent(...)
        class MyAgent(dumplingsAI.BaseAgent):                 # OpenAI
            ...

        @dumplingsAI.register_agent(...)
        class ClaudeAgent(dumplingsAI.anthropic_agent.AnthropicAgent):  # Anthropic
            ...

    选哪种？两种等价。``Agent`` + ``protocol`` 字段的好处是：协议可配置（可以从环境变量、
    config 文件、或者更高层 Agent 动态决定）。
    """

    protocol: str = "openai"


__all__ = [
    # 核心组件
    "register_agent",
    "tool_registry",
    "Agent",          # 协议无关的工厂基类（推荐）
    "BaseAgent",      # OpenAI 协议基类（直接继承时用）
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
    """在终端打印帮助信息。

    内容由两部分拼装，**全部自动生成，无硬编码字符串**：

    1. **静态文档**：直接 ``print(__doc__)``（即模块顶部 docstring）。
       改 docstring 一次，``help()`` 输出自动跟着改。
    2. **运行时状态**：从 ``agent_list`` / ``tool_registry`` 反射当前已注册的对象。
       不需要手动维护"当前已注册 Agent 列表"之类的字符串。

    Windows 终端自动切换到 UTF-8 编码，避免中文乱码。
    """
    import sys
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    # 1) 静态文档：模块 docstring
    print(__doc__)

    # 2) 运行时状态（反射当前已注册的 Agent / 工具）
    print(f"\n运行时状态（v{__version__}）")
    print("-" * 60)
    n_agents = len(agent_list)
    if n_agents:
        names = sorted(agent_list.keys())
        print(f"已注册 Agent: {n_agents} 个 → {', '.join(names)}")
    else:
        print("已注册 Agent: 0 个（请用 @dumplingsAI.register_agent 注册）")

    try:
        tools = list(tool_registry.list_tools() or [])
    except Exception:  # pragma: no cover
        tools = []
    if tools:
        print(f"已注册工具: {len(tools)} 个 → {', '.join(sorted(tools))}")
    else:
        print("已注册工具: 0 个（请用 @tool_registry.register_tool 注册）")

    # 3) 命令行入口提示（指向真实模块，不写死字符串）
    try:
        import dumplingsAI.cli as _cli
        cli_module = _cli.__name__
    except Exception:  # pragma: no cover
        cli_module = "dumplingsAI"
    print("\n命令行入口：")
    print(f"  $ python -m {cli_module} --help    # 全部子命令")
    print(f"  $ python -m {cli_module} --doctor  # 环境自检（Python / API Key / 已注册 Agent）")
    print(f"  $ python -m {cli_module} --demo    # 离线 demo（不连真实 LLM）")


# 方便交互式访问：``help(dumplingsAI)`` 会显示模块 docstring
help.__doc__ = __doc__
