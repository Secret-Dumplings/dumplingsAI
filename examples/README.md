# dumplingsAI 示例代码

本文件夹包含 dumplingsAI 框架的使用示例，按复杂度递增排列。

## 运行示例

### 前置准备

1. 安装依赖：
```bash
cd ..
pip install -e .
```

2. 配置环境变量：
在项目根目录创建 `.env` 文件：
```bash
API_KEY=your_api_key_here
```

### 运行示例

```bash
# 示例 1：单 Agent 基础用法
python example1_basic.py

# 示例 2：带自定义工具的 Agent
python example2_custom_tools.py

# 示例 3：多 Agent 协作
python example3_multi_agent.py

# 示例 4：MCP 服务器集成
python example4_mcp.py

# 示例 5：自定义输出处理
python example5_custom_output.py
```

## 示例说明

| 文件 | 说明 | 难度 |
|------|------|------|
| `example1_basic.py` | 单 Agent 基础用法，展示如何创建和运行 Agent | ⭐ |
| `example2_custom_tools.py` | 注册自定义工具，展示工具注册和参数定义 | ⭐⭐ |
| `example3_multi_agent.py` | 多 Agent 协作，展示 Agent 间的通讯和协作 | ⭐⭐ |
| `example4_mcp.py` | MCP 服务器集成，展示如何接入外部 MCP 工具 | ⭐⭐⭐ |
| `example5_custom_output.py` | 自定义输出处理，展示如何重写 Agent 输出方法 | ⭐⭐ |

## 核心概念

### Agent 注册

```python
@dumplingsAI.register_agent("uuid", "name")
class MyAgent(dumplingsAI.BaseAgent):
    prompt = "系统提示词"
    api_provider = "API 端点"
    model_name = "模型名称"
    api_key = os.getenv("API_KEY")
```

### 工具注册

```python
@dumplingsAI.tool_registry.register_tool(
    allowed_agents=None,
    name="tool_name",
    description="工具描述",
    parameters={...}  # OpenAI Function Calling schema
)
def my_tool(param: str) -> str:
    return "结果"
```

### 工具调用模式

- **Function Calling** (`fc_model=True`): 标准化调用，推荐
- **XML** (`fc_model=False`): 兼容性好，使用 XML 标签

## 更多信息

- 完整文档：`../README.md`
- 许可证：`../../LICENSE`