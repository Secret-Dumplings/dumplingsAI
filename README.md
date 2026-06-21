# dumplingsAI

> dumplingsAI开创AI新架构

## 目录

- [简介](#简介)
- [安装](#安装)
- [快速开始](#快速开始)
- [核心概念](#核心概念)
- [API 参考](#api-参考)
- [示例](#示例)
- [常见问题](#常见问题)

---

## 简介

dumplingsAI 是一个轻量级的多智能体协作框架，支持：

- **多智能体注册与管理** - 通过装饰器快速注册 Agent
- **工具注册系统** - 将函数注册为 Agent 可调用的工具
- **MCP 协议支持** - 兼容 Model Context Protocol，可接入外部 MCP 服务器
- **双模式工具调用** - 支持 Function Calling 和 XML 两种调用方式
- **Agent 间协作** - Agent 可通过内置工具相互请求帮助

### 核心特性

| 特性 | 说明 |
|------|------|
| 装饰器注册 | 使用 `@register_agent` 快速定义 Agent |
| 工具权限控制 | 可为每个工具指定允许使用的 Agent |
| 内置工具 | `ask_for_help`、`list_agents`、`attempt_completion`、`reload` |
| 日志系统 | 基于 loguru 的统一日志配置 |
| 会话池管理 | MCP 会话自动复用与健康检查 |

---

## 安装

### 使用 uv（推荐）

```bash
uv add git+https://github.com/Secret-Dumplings/dumplingsAI.git
```

### 依赖说明

dumplingsAI 的核心依赖：

```toml
dependencies = [
    "beautifulsoup4>=4.14.2",   # XML/HTML 解析
    "loguru>=0.7.3",            # 日志系统
    "lxml>=6.0.2",              # XML 解析器后端
    "mcp>=1.14.1",              # MCP 协议支持
    "requests>=2.32.5",         # HTTP 请求
]
```

---

## 快速开始

### 1. 配置环境变量

在项目根目录创建 `.env` 文件：

```bash
API_KEY=your_api_key_here
```

### 2. 创建第一个 Agent

```python
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()

@dumplingsAI.register_agent("unique-uuid-here", "my_agent")
class MyAgent(dumplingsAI.BaseAgent):
    """Agent 的简介，描述其职责和能力"""
    prompt = "你是一个名为汤圆 AI 的智能助手"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")
    fc_model = True  # 使用 Function Calling 模式

# 运行
agent = dumplingsAI.agent_list["my_agent"]
agent.conversation_with_tool("你好，请介绍一下自己")
```

### 3. 注册自定义工具

```python
@dumplingsAI.tool_registry.register_tool(
    allowed_agents=["my_agent"],  # 允许使用的 Agent
    name="get_weather",
    description="查询天气信息",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称"}
        },
        "required": ["city"]
    }
)
def get_weather(city: str) -> str:
    return f"{city}今天晴朗，温度 25°C"
```

---

## 核心概念

### Agent 注册

使用 `@register_agent(uuid, name)` 装饰器注册 Agent：

```python
@dumplingsAI.register_agent("uuid-here", "agent_name")
class MyAgent(dumplingsAI.BaseAgent):
    prompt = "你的系统提示词"
    api_provider = "API 端点"
    model_name = "模型名称"
    api_key = os.getenv("API_KEY")
```

**必填属性：**

| 属性 | 说明 |
|------|------|
| `prompt` | Agent 的系统提示词 |
| `api_provider` | LLM API 端点 URL |
| `model_name` | 使用的模型名称 |
| `api_key` | API 密钥 |

**可选属性：**

| 属性 | 默认值 | 说明 |
|------|--------|------|
| `fc_model` | `True` | 是否使用 Function Calling 模式 |
| `stream` | `True` | 是否使用流式响应 |

### 工具注册

使用 `@tool_registry.register_tool()` 装饰器注册工具：

```python
@dumplingsAI.tool_registry.register_tool(
    allowed_agents=None,  # None 表示所有 Agent 可用
    name="tool_name",
    description="工具描述",
    parameters={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "参数说明"}
        },
        "required": ["param1"]
    }
)
def my_tool(param1: str) -> str:
    return f"结果：{param1}"
```

### 工具调用模式

dumplingsAI 支持两种工具调用模式：

#### Function Calling 模式（推荐）

```python
class MyAgent(dumplingsAI.BaseAgent):
    fc_model = True  # 启用 Function Calling
```

Agent 会以标准 function calling 格式调用工具。

#### XML 模式

```python
class MyAgent(dumplingsAI.BaseAgent):
    fc_model = False  # 使用 XML 模式
```

Agent 使用 XML 标签调用工具：

```xml
<get_weather><city>北京</city></get_weather>
```

---

## API 参考

### 模块导出

```python
from dumplingsAI import (
    register_agent,      # Agent 注册装饰器
    tool_registry,       # 工具注册器实例
    BaseAgent,           # Agent 基类
    agent_list,          # 已注册的 Agent 字典
)
```

### register_agent(uuid, name)

类装饰器，注册一个 Agent 类。

```python
@dumplingsAI.register_agent("uuid-123", "helper_bot")
class HelperAgent(dumplingsAI.BaseAgent):
    ...
```

### tool_registry.register_tool(...)

注册工具的装饰器。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `allowed_agents` | `str`, `list`, `None` | 允许使用的 Agent UUID/名称列表 |
| `name` | `str` | 工具名称（可选，默认使用函数名） |
| `description` | `str` | 工具描述 |
| `parameters` | `dict` | OpenAI Function Calling schema |

### BaseAgent 类

所有 Agent 的基类，提供核心功能。

**主要方法：**

| 方法 | 说明 |
|------|------|
| `conversation_with_tool(messages, tool, images)` | 进行对话，支持工具调用 |
| `pack(content)` | 打包输出内容 |
| `out(content)` | 输出处理方法（可重写） |
| `reload()` | 重新加载 Agent 配置 |
| `ask_for_help(agent_id, message)` | 请求其他 Agent 帮助 |
| `list_agents()` | 列出所有可用 Agent |
| `attempt_completion(report_content)` | 标记任务完成 |

### agent_list

全局字典，存储所有已注册的 Agent。

```python
# 通过 UUID 获取
agent = dumplingsAI.agent_list["uuid-123"]

# 通过名称获取
agent = dumplingsAI.agent_list["agent_name"]
```

---

## 示例

### 示例 1：单 Agent 基础用法

```python
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()

@dumplingsAI.register_agent("001", "simple_agent")
class SimpleAgent(dumplingsAI.BaseAgent):
    prompt = "你是一个简单的问答助手"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

if __name__ == "__main__":
    agent = dumplingsAI.agent_list["simple_agent"]
    agent.conversation_with_tool("你好")
```

### 示例 2：带自定义工具的 Agent

```python
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()

# 注册工具
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
    return str(a + b)

# 注册 Agent
@dumplingsAI.register_agent("002", "calculator")
class CalculatorAgent(dumplingsAI.BaseAgent):
    prompt = "你是一个计算器，可以执行数学运算"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

if __name__ == "__main__":
    agent = dumplingsAI.agent_list["calculator"]
    agent.conversation_with_tool("请帮我计算 123 + 456")
```

### 示例 3：多 Agent 协作

```python
import os
import dumplingsAI
from dotenv import load_dotenv

load_dotenv()

# 时间查询 Agent
@dumplingsAI.register_agent("time-uuid", "time_agent")
class TimeAgent(dumplingsAI.BaseAgent):
    prompt = "你是时间管理者，负责提供当前时间"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

# 调度 Agent
@dumplingsAI.register_agent("schedule-uuid", "scheduling_agent")
class SchedulingAgent(dumplingsAI.BaseAgent):
    prompt = "你是一个调度助手，可以请求其他 Agent 帮助"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

if __name__ == "__main__":
    scheduler = dumplingsAI.agent_list["scheduling_agent"]
    # 请求时间 Agent 帮助
    scheduler.conversation_with_tool(
        "请请求 time_agent 帮你查看当前时间"
    )
```

### 示例 4：注册 MCP 服务器工具

```python
import dumplingsAI

# 注册 MCP 服务器的所有工具
dumplingsAI.register_mcp_tools(
    server_path="path/to/mcp_server.py",
    register_resources=True,
    allowed_agents=["my_agent"]
)
```

### 示例 5：自定义输出处理

```python
@dumplingsAI.register_agent("003", "custom_agent")
class CustomAgent(dumplingsAI.BaseAgent):
    prompt = "你是一个自定义输出的 Agent"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

    def out(self, content):
        """重写输出方法，实现自定义处理"""
        if content.get("tool_name"):
            print(f"🔧 调用工具：{content.get('tool_name')}")
            return
        if content.get("task"):
            print("\n✅ 任务完成")
        else:
            print(content.get("message"), end="")
```

---

## 常见问题

### Q: 如何查看已注册的工具？

```python
# 查看所有注册工具
print(dumplingsAI.tool_registry.list_tools())

# 查看 Agent 可用的工具
agent = dumplingsAI.agent_list["my_agent"]
tools = agent.get_all_available_tools()
print(tools)
```

### Q: 如何查看会话信息？

```python
# 查看 MCP 会话信息
from dumplingsAI.mcp_bridge import get_session_info
print(get_session_info())
```

### Q: 如何关闭所有 MCP 会话？

```python
from dumplingsAI.mcp_bridge import close_all_mcp_sessions_sync
close_all_mcp_sessions_sync()
```

### Q: Function Calling 和 XML 模式有什么区别？

| 模式 | 优点 | 适用场景 |
|------|------|----------|
| Function Calling | 标准化、参数解析准确 | 支持 function calling 的模型 |
| XML | 兼容性好、易于调试 | 不支持 function calling 的模型 |

### Q: 如何让 Agent 支持多模态？

```python
# 传递图片（base64 或 URL）
agent.conversation_with_tool(
    messages="这张图片里有什么？",
    images=["data:image/png;base64,..."]  # 或图片 URL
)
```

---

## 许可证

Apache License 2.0

Copyright [2025/11/24] [secret_dumplings]

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.