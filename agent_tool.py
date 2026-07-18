# -*- coding: utf-8 -*-
"""
tool.py  –  带超详细日志的工具注册器
附带通用：@builtin_tool 装饰器 — Agent 的内置工具（包括 ask_for_help /
list_agents / attempt_completion / reload 等）一律通过该装饰器声明，
工具的 schema（input_schema / description）由方法签名+类型注解自动推导，
框架不再硬编码任何内置工具元信息。

用法：
    export LOGURU_LEVEL=TRACE
    python your_main.py
"""
import inspect
import re
from functools import wraps
from typing import Any, List, Optional, Union, get_args, get_origin

from .logging_config import logger

# 日志配置已由 logging_config 模块统一管理

# ======================================================================
# 2.5 通用：@builtin_tool 装饰器
#     —— 把方法标记为 Agent 内置工具，schema 自动从签名/类型注解推导
# ======================================================================

# Python 基础类型 → JSON Schema type
_BUILTIN_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
    type(None): "null",
}


def _builtin_resolve_json_type(annotation: Any) -> str:
    """
    把 Python 类型注解尽量映射到 JSON Schema 的 ``type``。
    仅处理基础类型；遇到自定义类时统一回落为 ``string``，
    必要时可在 schema 层通过 ``format`` 字段补充语义。
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return "string"
    # Optional[X] / Union[X, Y] / X | Y -> 取第一个非 None 的子类型
    origin = get_origin(annotation)
    if origin is not None:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if args:
            return _builtin_resolve_json_type(args[0])
        return "string"
    if isinstance(annotation, type) and annotation in _BUILTIN_TYPE_MAP:
        return _BUILTIN_TYPE_MAP[annotation]
    return "string"


def _builtin_derive_param_desc(func, pname: str, fallback: Optional[str] = None) -> str:
    """
    尝试从函数的 Google/Sphinx 风格 docstring 中提取 pname 的描述。
    """
    doc = inspect.getdoc(func) or ""
    # Google 风格： "Args:\n    x: ...\n    y: ..."
    google = re.search(
        rf"^\s*{re.escape(pname)}\s*[:：]\s*(.+?)(?=\n\s*\n|\n\s*[A-Za-z_]+\s*[:：]|\Z)",
        doc, re.M | re.S,
    )
    if google:
        return google.group(1).strip().split("\n")[0]
    # Sphinx 风格： ":param x: ..."
    sphinx = re.search(
        rf":param\s+{re.escape(pname)}\s*:\s*(.+?)(?=:param|:return|:raises|\Z)",
        doc, re.S,
    )
    if sphinx:
        return sphinx.group(1).strip().split("\n")[0]
    return fallback or f"{pname} 参数"


_BUILTIN_DOC_PARA_RE = re.compile(r"\n\s*\n")


def _builtin_derive_description(func, override: Optional[str]) -> str:
    """优先取装饰器显式传入的描述，否则取 docstring 的第一段"""
    if override:
        return override
    doc = (inspect.getdoc(func) or "").strip()
    if not doc:
        return ""
    return _BUILTIN_DOC_PARA_RE.split(doc)[0].strip()


def builtin_tool(name: Optional[str] = None,
                 description: Optional[str] = None,
                 params: Optional[dict] = None):
    """
    把一个方法标记为 Agent 的内置工具，并自动派生其 OpenAI function schema。

    Args:
        name        : 工具名（默认 = 函数名）
        description : 工具描述（默认 = 函数 docstring 第一段）
        params      : 手工指定参数描述，形如 ``{"param_name": "中文说明"}``
                      未指定的参数：先尝试从 docstring 解析 Google/Sphinx 风格，
                      再退回 ``"<name> 参数"``

    Schema 来源：
        - ``input_schema.properties`` ← 函数签名（参数名 + 类型注解）
        - ``input_schema.required``   ← 没有默认值的参数
        - ``description``             ← 装饰器参数 / docstring

    使用示例::

        class MyAgent(AnthropicAgent):  # 或 BaseAgent
            @builtin_tool(
                description="请求其他Agent帮助",
                params={"agent_id": "目标Agent的UUID或名称", "message": "请求内容"},
            )
            def ask_for_help(self, agent_id: str, message: str) -> str:
                '''请求另一个Agent协作'''
                ...
    """
    user_params = dict(params or {})

    def decorator(func):
        tool_name = name or func.__name__
        sig = inspect.signature(func)

        properties = {}
        required = []
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            ptype = _builtin_resolve_json_type(param.annotation)
            pdesc = _builtin_derive_param_desc(func, pname, user_params.get(pname))
            properties[pname] = {"type": ptype, "description": pdesc}
            if param.default is inspect.Parameter.empty:
                required.append(pname)

        func.__builtin_tool_name__ = tool_name
        func.__builtin_tool_meta__ = {
            "name": tool_name,
            "description": _builtin_derive_description(func, description),
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }
        return func

    return decorator


def _builtin_promote_overrides(cls) -> None:
    """
    类层级钩子：子类覆盖 ``@builtin_tool`` 装饰的方法时，若未重新装饰，
    自动把父类的 meta 复制到子类方法上，保证子类 override 仍可被发现。
    """
    for klass in reversed(cls.__mro__[1:]):  # 从最基类到派生类
        for attr_name, parent_method in klass.__dict__.items():
            if not callable(parent_method):
                continue
            pmeta = getattr(parent_method, "__builtin_tool_meta__", None)
            if not pmeta:
                continue
            sub_method = cls.__dict__.get(attr_name)
            if sub_method is None:
                continue
            if getattr(sub_method, "__builtin_tool_meta__", None):
                continue
            try:
                sub_method.__builtin_tool_meta__ = dict(pmeta)
                sub_method.__builtin_tool_name__ = pmeta["name"]
            except (AttributeError, TypeError):
                # bound method 不支持 setattr；这里发生在类定义时期应该不会触发
                pass


# ======================================================================
# 2. 工具注册器 --------------------------------------------------------
# ======================================================================


class tool:
    """工具注册管理器（超详细日志版）"""
    def __init__(self):
        self._tools: dict = {}              # name -> info
        self._agent_permissions: dict = {}  # 预留
        self._uuid_to_name: dict = {}       # uuid -> name
        logger.trace("tool.__init__ -> empty registries created")

    # --------------------  uuid 映射 --------------------
    def register_agent_uuid(self, uuid: str, name: str):
        logger.trace(f"register_agent_uuid(uuid={uuid!r}, name={name!r}) enter")
        self._uuid_to_name[uuid] = name
        logger.debug(f"uuid映射已注册: {uuid} -> {name}")

    # --------------------  工具注册 --------------------
    def register_tool(
        self,
        allowed_agents: Union[str, List[str]] = None,
        description: str = "",
        name: Optional[str] = None,
        parameters: Optional[dict] = None,
    ):
        """
        工具注册装饰器（带日志）
        parameters: OpenAI function calling schema
        """
        frame = inspect.currentframe().f_back
        caller = f"{frame.f_code.co_filename}:{frame.f_lineno}"
        logger.trace(f"register_tool() called from {caller}")
        logger.trace(
            f"params -> allowed_agents={allowed_agents!r}, "
            f"description={description!r}, name={name!r}, parameters={parameters!r}"
        )

        def decorator(func):
            tool_name = name or func.__name__
            logger.trace(f"decorator applied on function {func.__name__!r}, tool_name={tool_name!r}")

            # 处理 allowed_agents
            if allowed_agents is None:
                permitted = None
                logger.trace("permitted_agents -> None (unlimited)")
            elif isinstance(allowed_agents, str):
                permitted = [allowed_agents]
                logger.trace(f"permitted_agents -> single str: {permitted}")
            else:
                permitted = list(allowed_agents)
                logger.trace(f"permitted_agents -> list: {permitted}")

            # 构建 OpenAI tools schema
            tool_schema = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": description,
                    "parameters": parameters or {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }

            # 真正注册
            self._tools[tool_name] = {
                "function": func,
                "allowed_agents": permitted,
                "description": description,
                "name": tool_name,
                "parameters": parameters,
                "schema": tool_schema
            }
            logger.debug(
                f"工具注册成功: {tool_name!r} -> {self._tools[tool_name]}"
            )

            @wraps(func)
            def wrapper(*args, **kwargs):
                logger.trace(
                    f"工具被调用: {tool_name!r} with args={args}, kwargs={kwargs}"
                )
                return func(*args, **kwargs)

            logger.trace(f"decorator返回wrapper，注册流程结束: {tool_name!r}")
            return wrapper

        return decorator

    # --------------------  权限检查 --------------------
    def check_permission(self, agent_name: str, tool_name: str) -> bool:
        logger.trace(
            f"check_permission(agent_name={agent_name!r}, tool_name={tool_name!r})"
        )
        if tool_name not in self._tools:
            logger.warning(f"工具 {tool_name!r} 未注册，拒接访问")
            return False

        # uuid -> name
        original_agent = agent_name
        if agent_name in self._uuid_to_name:
            agent_name = self._uuid_to_name[agent_name]
            logger.debug(f"uuid转换: {original_agent!r} -> {agent_name!r}")

        tool_info = self._tools[tool_name]
        allowed = tool_info["allowed_agents"]
        logger.trace(f"工具 {tool_name!r} 的 allowed_agents = {allowed!r}")

        if allowed is None:
            logger.trace("allowed_agents 为 None，放行")
            return True

        ok = agent_name in allowed
        logger.debug(
            f"权限检查结果: agent={agent_name!r} "
            f"{'✔' if ok else '✘'} 工具 {tool_name!r}"
        )
        return ok

    # --------------------  查询接口 --------------------
    def get_tool_info(self, tool_name: str) -> Optional[dict]:
        logger.trace(f"get_tool_info({tool_name!r})")
        return self._tools.get(tool_name)

    def get_tool_schema(self, tool_name: str) -> Optional[dict]:
        """返回单个工具的 OpenAI schema"""
        logger.trace(f"get_tool_schema({tool_name!r})")
        tool_info = self._tools.get(tool_name)
        return tool_info.get("schema") if tool_info else None

    def get_all_tools_schema(self, agent_uuid: str) -> list:
        """返回该 agent 有权限使用的所有工具的 schema"""
        logger.trace(f"get_all_tools_schema(agent_uuid={agent_uuid!r})")
        tools_schema = []
        for tool_name, tool_info in self._tools.items():
            if self.check_permission(agent_uuid, tool_name):
                tools_schema.append(tool_info.get("schema"))
        logger.debug(f"Agent {agent_uuid} 有权限的工具数: {len(tools_schema)}")
        return tools_schema

    def get_all_tools_info(self, agent_uuid: str) -> dict:
        """返回该 agent 有权限的所有工具信息（名称和描述）"""
        logger.trace(f"get_all_tools_info(agent_uuid={agent_uuid!r})")
        tools_info = {}
        for tool_name, tool_info in self._tools.items():
            if self.check_permission(agent_uuid, tool_name):
                tools_info[tool_name] = {
                    "description": tool_info["description"],
                    "parameters": tool_info.get("parameters", {})
                }
        logger.debug(f"Agent {agent_uuid} 有权限的工具: {list(tools_info.keys())}")
        return tools_info

    def list_tools(self) -> dict:
        logger.trace("list_tools() called")
        snapshot = {
            name: {
                "description": info["description"],
                "allowed_agents": info["allowed_agents"],
            }
            for name, info in self._tools.items()
        }
        logger.trace(f"当前注册工具: {list(snapshot.keys())}")
        return snapshot

    # --------------------  内置工具收集 --------------------
    @staticmethod
    def collect_builtin_tools(instance) -> list:
        """
        从 instance 的类层级收集被 ``@builtin_tool`` 装饰的方法的 schema。
        返回 OpenAI function-calling 格式，与 ``get_all_tools_schema`` 一致，
        便于直接拼装到请求体里。
        """
        schemas: list = []
        for klass in type(instance).__mro__:
            for attr_name, attr in klass.__dict__.items():
                if not callable(attr):
                    continue
                meta = getattr(attr, "__builtin_tool_meta__", None)
                if meta:
                    schemas.append({
                        "type": "function",
                        "function": {
                            "name": meta["name"],
                            "description": meta["description"],
                            "parameters": meta["input_schema"],
                        },
                    })
        # 子类覆盖在前；按 name 去重
        seen: set = set()
        out: list = []
        for s in schemas:
            n = s["function"]["name"]
            if n in seen:
                continue
            seen.add(n)
            out.append(s)
        return out


# 3. 全局实例 ----------------------------------------------------------
tool_registry = tool()
logger.trace("tool_registry 全局实例已创建")


# 4. 导出 ------------------------------------------------------------
__all__ = ["tool_registry", "builtin_tool", "_builtin_promote_overrides"]
