# dumplingsAI

> 一个轻量、模块化的多智能体协作框架，让 LLM 像"公司团队"一样分工完成任务。

[![PyPI](https://img.shields.io/pypi/v/dumplingsAI.svg)](https://pypi.org/project/dumplingsAI/)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![CI](https://github.com/Secret-Dumplings/dumplingsAI/actions/workflows/python-package.yml/badge.svg)](https://github.com/Secret-Dumplings/dumplingsAI/actions)

---

## 特性一览

- **多协议 Agent**：`BaseAgent`（OpenAI-compatible Chat Completions）+ `AnthropicAgent`（Anthropic Messages API），同一份 `agent_list` 共用
- **声明式注册**：`@register_agent` + `@tool_registry.register_tool`，单行注册
- **统一内置 schema**：`@builtin_tool` 装饰器从签名+类型注解自动推导，零硬编码
- **跨 Agent 协作**：`ask_for_help` / `list_agents` / `attempt_completion` / `reload` 四个内建工具
- **MCP 协议桥接**：标准 stdio MCP 服务器自动接入
- **Skill 开放标准**：兼容 `.claude/skills/` 目录，热加载
- **细粒度权限 ACL**：每个工具可指定允许使用的 Agent 列表
- **钩子系统**：`register_tool_hook(event_type, ...)` 监听工具调用前/后/错误

---

## 安装

```bash
pip install dumplingsAI
```

需要 Python 3.10+。可选功能：

```bash
pip install dumplingsAI[anthropic]  # Anthropic Agent 需要的 requests 已内置，无需额外
pip install dumplingsAI[async]      # Phase 3 上线后启用
```

---

## 快速开始

### 1. 准备 API Key

```bash
export API_KEY="sk-..."                    # OpenAI 协议
export ANTHROPIC_API_KEY="sk-ant-..."      # Anthropic 协议
```

### 2. 第一个 Agent

```python
import os
import dumplingsAI

@dumplingsAI.tool_registry.register_tool(
    allowed_agents=["weather"],   # None 或 [] 表示所有 Agent 可用
    description="查询某城市当前天气",
    name="get_weather",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名"}},
        "required": ["city"],
    },
)
def get_weather(city: str) -> str:
    return f"{city}今天晴，25°C"

@dumplingsAI.register_agent("agent-uuid-1", "weather", "天气小助手")
class WeatherAgent(dumplingsAI.BaseAgent):
    """走 OpenAI-compatible Chat Completions 的天气 Agent。"""
    prompt = "你是天气助手，使用 get_weather 工具查询天气。"
    api_provider = "https://api.example.com/v1/chat/completions"
    model_name = "qwen3.5-plus"
    api_key = os.getenv("API_KEY")

if __name__ == "__main__":
    agent = dumplingsAI.agent_list["weather"]
    agent.conversation_with_tool("北京今天天气怎么样？")
```

### 3. 多协议混用

```python
from dumplingsAI.anthropic_agent import AnthropicAgent

@dumplingsAI.register_agent("agent-uuid-2", "reviewer", "走 Claude 协议的评审 Agent")
class ReviewerAgent(AnthropicAgent):
    prompt = "你是评审助手。完成工作后用 attempt_completion 汇报。"
    api_provider = "https://api.anthropic.com"
    model_name = "claude-3-5-sonnet-latest"
    api_key = os.getenv("ANTHROPIC_API_KEY")

# 同一份 agent_list，OpenAI / Anthropic Agent 互通
weather = dumplingsAI.agent_list["weather"]
reviewer = dumplingsAI.agent_list["reviewer"]
reviewer.conversation_with_tool(
    f"刚才 weather Agent 说北京晴 25°C，请评审"
)
```

---

## 核心概念

### Agent 注册

`@register_agent(uuid, name, description=None)` 是双键装饰器：UUID 用于程序化访问，名称用于人类可读。

```python
@dumplingsAI.register_agent("my-uuid", "my_agent", "一句话说明 Agent 用途")
class MyAgent(dumplingsAI.BaseAgent):
    prompt = "..."                # 系统提示词
    api_provider = "..."          # API 端点
    model_name = "..."            # 模型名
    api_key = "..."               # 鉴权
    fc_model = True               # 是否启用 Function Calling
    stream = True                 # 是否流式响应
    timeout = 60                  # 单请求超时（Phase 1+）
    max_retries = 2               # 最大重试次数（Phase 1+）
```

### 工具注册

两种写法等价：

```python
# 写法 1：装饰器 + JSON Schema（传统）
@dumplingsAI.tool_registry.register_tool(
    allowed_agents=["my_agent"],
    name="add",
    description="求两数之和",
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "number"},
        },
        "required": ["a", "b"],
    },
)
def add(a: float, b: float) -> float:
    return a + b
```

```python
# 写法 2：内置工具的 schema 自动从签名/类型注解推导
from dumplingsAI import builtin_tool

@builtin_tool(
    description="求两数之和",
    params={"a": "第一个加数", "b": "第二个加数"},
)
def add(self, a: float, b: float) -> float:
    return a + b
```

### Agent 间的协作

每个 Agent 自带 4 个内建工具：

| 工具 | 用途 |
|------|------|
| `ask_for_help(agent_id, message)` | 委派任务给其他 Agent |
| `list_agents()` | 列出所有可协作的 Agent |
| `attempt_completion(report_content)` | 标记任务完成 |
| `reload()` | 重新拉取工具/技能列表 |

无需手写 prompt 教 LLM 怎么调——框架已把工具描述注入到 system prompt 里。

### 钩子

```python
class MyAgent(dumplingsAI.BaseAgent):
    def __init__(self):
        super().__init__()
        self.register_tool_hook(self._audit)

    def _audit(self, event_type, tool_name, tool_args, tool_result, task_id):
        # event_type: 'before' | 'after' | 'error'
        if event_type == 'error':
            logger.error(f"工具 {tool_name} 失败：{tool_result}")
```

### MCP 桥接

```python
import dumplingsAI

# 自动拉起 MCP 服务器并注册其所有工具
dumplingsAI.register_mcp_tools(
    server_path="mcp/weather_mcp/weather_server.py",
    allowed_agents=["weather"],
)
```

---

## API 参考

```python
from dumplingsAI import (
    BaseAgent,           # OpenAI 协议 Agent 基类
    builtin_tool,        # 内置工具装饰器
    register_agent,      # Agent 注册装饰器
    tool_registry,       # 工具注册器实例
    agent_list,          # 已注册 Agent 字典
    register_mcp_tools,  # MCP 工具注册
    skill_registry,      # Skill 注册表
    # AnthropicAgent 走子模块路径：
    # from dumplingsAI.anthropic_agent import AnthropicAgent
)
```

详见 [`docs/PROJECT.md`](https://github.com/Secret-Dumplings/AI_Company/blob/main/docs/PROJECT.md)（仓库内的 SDK 差距分析 + 完整设计文档）。

---

## 示例

仓库自带完整示例：

- `examples/basic_agent/agent_example.py` — 单 Agent 基础用法
- `examples/multi_agent/ask_for_help_example.py` — 多 Agent 协作
- `examples/anthropic_agent/agent_example.py` — Anthropic 协议示例
- `tests/test_placeholder.py` — 冒烟测试（验证包能 import、装饰器工作）

运行：

```bash
git clone https://github.com/Secret-Dumplings/AI_Company.git
cd AI_Company
uv sync
uv run python examples/basic_agent/agent_example.py
```

---

## 路线图

- ✅ Phase 0 — 注册式多 Agent 框架、工具 ACL、MCP 桥接、Skill 集成
- ✅ Phase 0.5 — `@builtin_tool` 装饰器统一 schema 来源
- ✅ Phase 0.6 — `AnthropicAgent` 与 OpenAI 协议共享 `agent_list`
- ✅ Phase 1 — `http_utils`（retry/timeout）+ 错误类型体系 + `tiktoken` 计数
- ✅ Phase 2 — Pydantic 结构化输出（`params_model`）
- ✅ Phase 3 — `httpx` 异步支持（`aconversation_with_tool`）
- ✅ Phase 4 — 全面 Pydantic 模型化

具体计划见 `docs/PROJECT.md` 附录 B。

---

## 开发与测试

```bash
git clone https://github.com/Secret-Dumplings/AI_Company.git
cd AI_Company
uv sync --group dev
uv run pytest Dumplings/tests/ -v
uv run ruff check Dumplings/
```

CI 在 `python-package.yml`，自动跑 ruff + pytest on Python 3.10 / 3.11 / 3.12。

---

## 贡献

欢迎 PR / Issue。提交前请跑 `uv run ruff check` + `uv run pytest`。

---

## 许可证

Apache License 2.0

Copyright 2025-2026 [Secret Dumplings](https://github.com/Secret-Dumplings)

```
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.