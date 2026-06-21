# -*- coding: utf-8 -*-
"""
MCP Bridge - 将 MCP 服务器工具转换为标准工具（非 XML）
使用 dumplingsAI.tool_registry.register_tool 直接注册

改进：
- 使用 asyncio.Lock 替代 threading.Lock
- 复用事件循环
- 添加会话健康检查和自动回收
- 添加上下文管理器支持
"""
import asyncio
import os
import time
from typing import Optional, Dict, Any, List
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from contextlib import asynccontextmanager
from .agent_tool import tool_registry
from .logging_config import logger


# ==================== 全局会话池 ====================
MCP_SESSION_POOL: Dict[str, Dict[str, Any]] = {}
SESSION_LOCK = asyncio.Lock()

# 全局事件循环复用器
_event_loop: Optional[asyncio.AbstractEventLoop] = None


def get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """
    获取或创建全局事件循环
    避免每次工具调用都创建新事件循环
    """
    global _event_loop
    if _event_loop is None or _event_loop.is_closed():
        try:
            _event_loop = asyncio.get_event_loop()
        except RuntimeError:
            # 如果没有当前线程的事件循环，创建一个新的
            _event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_event_loop)
    return _event_loop


class MCPSessionPool:
    """
    MCP 会话池管理器
    提供会话健康检查和自动回收功能
    """

    def __init__(self, max_idle_time: int = 3600):
        """
        初始化会话池

        Args:
            max_idle_time: 最大空闲时间（秒），默认 1 小时
        """
        self._pool: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._max_idle_time = max_idle_time
        self._health_check_task: Optional[asyncio.Task] = None

    async def start_health_check(self, interval: int = 300) -> None:
        """
        启动健康检查任务

        Args:
            interval: 检查间隔（秒），默认 5 分钟
        """
        async def _health_check_loop():
            while True:
                await asyncio.sleep(interval)
                await self.health_check()

        self._health_check_task = asyncio.create_task(_health_check_loop())
        logger.info(f"MCP 会话池健康检查已启动，间隔 {interval} 秒")

    async def stop_health_check(self) -> None:
        """停止健康检查任务"""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None
            logger.info("MCP 会话池健康检查已停止")

    async def health_check(self) -> int:
        """
        检查并回收空闲会话

        Returns:
            int: 回收的会话数量
        """
        async with self._lock:
            now = time.time()
            expired = []

            for path, info in self._pool.items():
                last_used = info.get("last_used", 0)
                if now - last_used > self._max_idle_time:
                    expired.append(path)

            recycled_count = 0
            for path in expired:
                if await self._close_session(path):
                    recycled_count += 1
                    logger.info(f"回收空闲 MCP 会话：{path}")

            return recycled_count

    async def get_session(self, server_path: str) -> ClientSession:
        """
        获取或创建会话

        Args:
            server_path: MCP 服务器路径

        Returns:
            ClientSession: MCP 会话
        """
        async with self._lock:
            if server_path in self._pool:
                session_info = self._pool[server_path]
                if session_info.get("initialized"):
                    session_info["last_used"] = time.time()
                    logger.debug(f"复用现有 MCP 会话：{server_path}")
                    return session_info["session"]

            # 创建新会话
            session_info = await _initialize_mcp_session(server_path)
            session_info["last_used"] = time.time()
            self._pool[server_path] = session_info
            logger.info(f"创建新 MCP 会话：{server_path}")
            return session_info["session"]

    async def close_session(self, server_path: str) -> bool:
        """关闭指定会话"""
        async with self._lock:
            return await self._close_session(server_path)

    async def _close_session(self, server_path: str) -> bool:
        """内部方法：关闭会话"""
        session_info = self._pool.get(server_path)
        if not session_info:
            logger.warning(f"MCP 会话不存在：{server_path}")
            return False

        try:
            logger.info(f"正在关闭 MCP 会话：{server_path}")

            session = session_info.get("session")
            context = session_info.get("context")

            if session:
                await session.__aexit__(None, None, None)
            if context:
                await context.__aexit__(None, None, None)

            del self._pool[server_path]
            logger.success(f"MCP 会话已关闭：{server_path}")
            return True

        except Exception as e:
            logger.error(f"关闭 MCP 会话失败 {server_path}: {e}")
            if server_path in self._pool:
                del self._pool[server_path]
            return False

    async def close_all(self) -> int:
        """关闭所有会话"""
        async with self._lock:
            server_paths = list(self._pool.keys())
            closed_count = 0

            for server_path in server_paths:
                try:
                    if await self._close_session(server_path):
                        closed_count += 1
                except Exception as e:
                    logger.error(f"关闭会话时出错 {server_path}: {e}")

            logger.info(f"共关闭 {closed_count} 个 MCP 会话")
            return closed_count

    def get_session_info(self, server_path: Optional[str] = None) -> Dict[str, Any]:
        """获取会话信息"""
        if server_path is None:
            return {
                path: {
                    "initialized": info.get("initialized", False),
                    "tools_count": len(info.get("tools", [])),
                    "resources_count": len(info.get("resources", [])),
                    "last_used": info.get("last_used", 0)
                }
                for path, info in self._pool.items()
            }
        else:
            info = self._pool.get(server_path)
            if info:
                return {
                    "initialized": info.get("initialized", False),
                    "tools_count": len(info.get("tools", [])),
                    "resources_count": len(info.get("resources", [])),
                    "last_used": info.get("last_used", 0),
                    "tools": [t.name for t in info.get("tools", [])],
                    "resources": [r.uri for r in info.get("resources", [])]
                }
            return {}


# 全局会话池实例
_global_session_pool = MCPSessionPool()


@asynccontextmanager
async def mcp_session_context(server_path: str):
    """
    MCP 会话上下文管理器

    Usage:
        async with mcp_session_context("path/to/server.py") as session:
            # 使用 session
            pass
    """
    session = None
    try:
        session = await _global_session_pool.get_session(server_path)
        yield session
    except Exception as e:
        logger.error(f"MCP 会话上下文中有异常：{e}")
        raise
    finally:
        # 上下文管理器不负责关闭会话，会话由池管理
        pass


async def _initialize_mcp_session(server_path: str) -> Dict[str, Any]:
    """
    异步初始化 MCP 服务器会话

    注意：此函数不再负责加锁，调用者需自行管理并发
    """
    # 验证文件存在
    if not os.path.isfile(server_path):
        raise FileNotFoundError(f"MCP 服务器脚本不存在：{server_path}")

    # 确定执行命令
    cmd = "python" if server_path.endswith(".py") else "node"
    logger.debug(f"使用命令启动 MCP 服务器：{cmd} {server_path}")

    try:
        # 创建 stdio 客户端
        params = StdioServerParameters(command=cmd, args=[server_path], env=None)

        # 注意：__aenter__ 返回的是 (transport, session)
        # transport 是 (reader, writer) 元组
        stdio_ctx = stdio_client(params)
        transport = await stdio_ctx.__aenter__()
        reader, writer = transport
        session = ClientSession(reader, writer)

        # 初始化会话
        await session.__aenter__()
        await session.initialize()

        # 获取工具列表
        tools_response = await session.list_tools()
        tools = tools_response.tools
        logger.info(f"MCP 服务器 {server_path} 共有 {len(tools)} 个工具")

        # 获取资源列表
        resources_response = await session.list_resources()
        resources = resources_response.resources
        logger.info(f"MCP 服务器 {server_path} 共有 {len(resources)} 个资源")

        # 保存到会话池
        session_info = {
            "session": session,
            "transport": transport,
            "context": stdio_ctx,
            "tools": tools,
            "resources": resources,
            "initialized": True,
            "server_path": server_path,
            "last_used": time.time()
        }

        logger.success(f"MCP 会话初始化成功：{server_path}")
        return session_info

    except Exception as e:
        logger.error(f"MCP 会话初始化失败 {server_path}: {e}")
        raise


def _make_tool_wrapper(tool_name: str, server_path: str, input_schema: Dict[str, Any]):
    """
    创建工具包装器
    将 MCP 工具转换为标准工具，支持同步调用
    """
    def sync_wrapper(**kwargs) -> str:
        try:
            # 使用全局事件循环执行异步调用
            loop = get_or_create_event_loop()

            # 获取会话
            session_info = None
            if server_path in MCP_SESSION_POOL:
                session_info = MCP_SESSION_POOL[server_path]

            if not session_info or not session_info.get("initialized"):
                error_msg = f"MCP 会话未初始化：{server_path}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            session = session_info["session"]

            logger.trace(f"调用 MCP 工具：{tool_name} @ {server_path}, args={kwargs}")

            # 在事件循环中执行异步调用
            result = loop.run_until_complete(session.call_tool(tool_name, kwargs))
            content = result.content or ""
            logger.debug(f"工具 {tool_name} 返回：{content[:100]}")
            return content

        except Exception as e:
            error_msg = f"调用工具失败 {tool_name}: {str(e)}"
            logger.error(error_msg)
            raise

    return sync_wrapper


def _make_resource_wrapper(resource_uri: str, server_path: str):
    """
    创建资源包装器
    将 MCP 资源转换为工具，支持同步调用
    """
    def sync_wrapper() -> str:
        try:
            # 使用全局事件循环执行异步调用
            loop = get_or_create_event_loop()

            # 获取会话
            session_info = None
            if server_path in MCP_SESSION_POOL:
                session_info = MCP_SESSION_POOL[server_path]

            if not session_info or not session_info.get("initialized"):
                error_msg = f"MCP 会话未初始化：{server_path}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            session = session_info["session"]

            logger.trace(f"读取 MCP 资源：{resource_uri} @ {server_path}")

            # 在事件循环中执行异步调用
            result = loop.run_until_complete(session.read_resource(resource_uri))
            content = result.contents or ""
            logger.debug(f"资源 {resource_uri} 内容长度：{len(content)}")
            return content

        except Exception as e:
            error_msg = f"读取资源失败 {resource_uri}: {str(e)}"
            logger.error(error_msg)
            raise

    return sync_wrapper


def _convert_mcp_schema_to_openai(mcp_schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 MCP 的 inputSchema 转换为 OpenAI Function Calling 格式
    """
    if not mcp_schema:
        return {
            "type": "object",
            "properties": {},
            "required": []
        }

    # MCP schema 已经是 JSON Schema 格式，直接返回
    # 确保包含必需字段
    result = {
        "type": "object",
        "properties": mcp_schema.get("properties", {}),
        "required": mcp_schema.get("required", [])
    }
    return result


async def register_mcp_tools_async(
    server_path: str,
    register_resources: bool = True,
    allowed_agents=None
) -> int:
    """
    异步注册 MCP 服务器的所有工具为标准工具（非 XML）

    Args:
        server_path: MCP 服务器脚本路径
        register_resources: 是否注册资源为工具 (默认 True)
        allowed_agents: 允许使用这些工具的 Agent 列表 (None 表示所有 Agent)

    Returns:
        int: 注册成功的工具数量
    """
    try:
        # 使用会话池获取或创建会话
        async with SESSION_LOCK:
            session_info = await _initialize_mcp_session(server_path)
            MCP_SESSION_POOL[server_path] = session_info

        tools = session_info["tools"]
        resources = session_info["resources"]

        registered_count = 0

        # 注册工具
        for tool in tools:
            tool_name = tool.name
            desc = tool.description or f"MCP 工具：{tool_name}"
            input_schema = tool.inputSchema

            logger.debug(f"注册工具：{tool_name}")

            # 转换 schema
            openai_schema = _convert_mcp_schema_to_openai(input_schema)

            # 创建包装器并注册
            wrapper = _make_tool_wrapper(tool_name, server_path, input_schema)

            tool_registry.register_tool(
                name=tool_name,
                description=desc,
                allowed_agents=allowed_agents,
                parameters=openai_schema
            )(wrapper)

            registered_count += 1
            logger.success(f"已注册标准工具 <{tool_name}>")

        # 注册资源 (如果启用)
        if register_resources and resources:
            for resource in resources:
                # 从 URI 提取工具名 (移除特殊字符)
                uri = resource.uri
                # 创建资源工具名：例如 file_test_txt
                resource_name = f"read_{uri.split('://')[-1].replace('/', '_').replace('.', '_')}"
                desc = f"读取 MCP 资源：{uri}"

                logger.debug(f"注册资源工具：{resource_name} ({uri})")

                # 创建包装器并注册
                wrapper = _make_resource_wrapper(uri, server_path)

                tool_registry.register_tool(
                    name=resource_name,
                    description=desc,
                    allowed_agents=allowed_agents,
                    parameters={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                )(wrapper)

                registered_count += 1
                logger.success(f"已注册资源工具 <{resource_name}>")

        logger.info(f"MCP 服务器 {server_path} 共注册 {registered_count} 个工具")
        return registered_count

    except Exception as e:
        logger.error(f"注册 MCP 工具失败 {server_path}: {e}")
        raise


def register_mcp_tools(
    server_path: str,
    register_resources: bool = True,
    allowed_agents=None
) -> int:
    """
    同步入口：注册 MCP 服务器的所有工具为标准工具

    Args:
        server_path: MCP 服务器脚本路径
        register_resources: 是否注册资源为工具 (默认 True)
        allowed_agents: 允许使用这些工具的 Agent 列表 (None 表示所有 Agent)

    Returns:
        int: 注册成功的工具数量

    Example:
        >>> register_mcp_tools("path/to/mcp_server.py")
        3
    """
    if not os.path.isfile(server_path):
        raise FileNotFoundError(f"MCP 服务器脚本不存在：{server_path}")

    # 运行异步注册
    return asyncio.run(
        register_mcp_tools_async(server_path, register_resources, allowed_agents)
    )


async def close_mcp_session(server_path: str) -> bool:
    """
    异步关闭指定的 MCP 会话

    Args:
        server_path: MCP 服务器脚本路径

    Returns:
        bool: 是否成功关闭
    """
    return await _global_session_pool.close_session(server_path)


def close_mcp_session_sync(server_path: str) -> bool:
    """
    同步关闭指定的 MCP 会话
    """
    return asyncio.run(close_mcp_session(server_path))


async def close_all_mcp_sessions() -> int:
    """
    异步关闭所有 MCP 会话

    Returns:
        int: 成功关闭的会话数量
    """
    return await _global_session_pool.close_all()


def close_all_mcp_sessions_sync() -> int:
    """
    同步关闭所有 MCP 会话
    """
    return asyncio.run(close_all_mcp_sessions())


def get_session_info(server_path: Optional[str] = None) -> Dict[str, Any]:
    """
    获取会话信息

    Args:
        server_path: MCP 服务器脚本路径 (如果为 None，返回所有会话)

    Returns:
        Dict: 会话信息
    """
    return _global_session_pool.get_session_info(server_path)


def start_health_check(interval: int = 300) -> None:
    """
    启动健康检查任务

    Args:
        interval: 检查间隔（秒），默认 5 分钟
    """
    loop = get_or_create_event_loop()
    loop.run_until_complete(_global_session_pool.start_health_check(interval))


def stop_health_check() -> None:
    """停止健康检查任务"""
    loop = get_or_create_event_loop()
    loop.run_until_complete(_global_session_pool.stop_health_check())


# ==================== 兼容性接口 ====================
# 保留旧接口名，向后兼容
connect_and_register = register_mcp_tools