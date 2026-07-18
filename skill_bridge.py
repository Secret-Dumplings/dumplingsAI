# -*- coding: utf-8 -*-
"""
Skill Bridge - 将 Skill 注册为 tool_registry 中的工具
=====================================================

桥接层，使 Agent 可以通过 tool_registry 发现并调用 Skills。

集成流程:
    SkillRegistry.register_skill(skill_dir)
        └── 创建 Skill 实例
        └── 调用 register_skill_as_tool(skill)
            └── 创建代理函数
            └── tool_registry.register_tool()(proxy_func)
                └── 工具注册完成，Agent 可调用

这与 mcp_bridge.py 的流程一致：
    register_mcp_tools_async(server_path)
        └── 创建 MCP 包装器
        └── tool_registry.register_tool()(wrapper)
"""
from .agent_tool import tool_registry
from .logging_config import logger


def _create_skill_proxy(skill):
    """
    创建 Skill 代理函数

    代理函数接收 kwargs（来自 Function Calling 或 XML 解析），
    调用 skill.render() 渲染 SKILL.md 内容为 prompt 文本，
    并返回作为工具执行结果。

    Args:
        skill: Skill 实例

    Returns:
        代理函数
    """
    def proxy_function(**kwargs):
        logger.debug(f"Skill 代理被调用: {skill.name}, kwargs={kwargs}")
        try:
            rendered = skill.render(kwargs)
            logger.debug(
                f"Skill '{skill.name}' 执行成功，结果长度: {len(str(rendered))}"
            )
            return rendered
        except Exception as e:
            logger.error(f"Skill '{skill.name}' 执行失败: {e}")
            return f"Skill 执行出错: {str(e)}"

    return proxy_function


def register_skill_as_tool(skill) -> bool:
    """
    将 Skill 注册为 tool_registry 中的工具

    注册后，Agent 可以通过 Function Calling 调用此 Skill。

    Args:
        skill: Skill 实例

    Returns:
        bool: 是否注册成功
    """
    try:
        proxy_func = _create_skill_proxy(skill)

        # 复用 tool_registry.register_tool 装饰器的底层逻辑
        # （注意: register_tool 返回的是 decorator，需要手动调用）
        decorator = tool_registry.register_tool(
            allowed_agents=None,  # 默认所有 Agent 可用
            description=skill.get_full_description(),
            name=skill.name,
            parameters=skill.parameters,
        )
        decorator(proxy_func)

        logger.info(f"Skill '{skill.name}' 已作为工具注册到 tool_registry")
        return True

    except Exception as e:
        logger.error(f"注册 Skill 为工具失败 '{skill.name}': {e}")
        return False


def unregister_skill_from_tool(skill_name: str) -> bool:
    """
    从 tool_registry 中移除与 Skill 对应的工具

    Args:
        skill_name: Skill 名称

    Returns:
        bool: 是否移除成功
    """
    if skill_name in tool_registry._tools:
        del tool_registry._tools[skill_name]
        logger.info(f"已从 tool_registry 移除 Skill 工具: {skill_name}")
        return True
    logger.warning(f"tool_registry 中不存在 Skill 工具: {skill_name}")
    return False
