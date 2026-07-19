# Changelog

dumplingsAI 的所有显著变更记录。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-07-19

### Added
- **`http_utils.py`**：基于 httpx 的中央 HTTP 客户端
  - `HTTPClient`（同步）+ `AsyncHTTPClient`（异步）
  - 指数退避 retry（429 / 5xx / 网络错），可配 `max_retries`
  - `timeout` 可单次覆盖
  - 错误分类：抛 `errors.APIError` 子类（`RateLimitError` / `InternalServerError` / `TimeoutError` / `ConnectionError` ...）
- **`errors.py`**：异常类型体系，对齐官方 `openai-python` / `anthropic-sdk-python` 的错误模型
- **`llm_transport.py`**：LLM Transport 抽象层
  - `LLMTransport` 抽象 + `HttpxOpenAITransport` / `HttpxAnthropicTransport` 实现
  - `ChatRequest` / `LLMResponse` / `LLMEvent` / `ToolCall` / `UsageInfo` 中性数据类型
  - Agent 不再直写 HTTP / SSE 解析 / tool_call 抽取；以后换底层（aiohttp / OpenAI SDK）只动一个 transport
- **`tool_runner.py`**：工具执行的 ThreadPoolExecutor
  - `ToolRunner.submit()`：超时返回 `(None, task_id)`，让 LLM 看到 `task_id` 占位继续做别的
  - 自带 `get_status` / `wait` 收割
  - 取代旧版「熔断 N 轮」的长线任务支持
- **`agent_queue.py`**（v0.2 强化）：全局 `AgentQueue`（默认 2 worker，60s idle 退出）
  - `ask_for_help` 改走队列 + 循环检测 + 深度限制
  - 不再因递归栈过深炸出
- **Pydantic 结构化输出**（Phase 2）
  - `@builtin_tool` 新增 `params_model` 参数；自动 `model.model_json_schema()` + `model_validate(args)`
  - 校验失败把错误回灌给 LLM，让它重试
  - `Optional` / 默认值字段不进 `required`
- **异步支持**（Phase 3 起步）
  - `BaseAgent.aconversation_with_tool` / `AnthropicAgent.aconversation_with_tool`
  - 基于 `AsyncHTTPClient` + `transport.achat_stream`
  - `asyncio_mode = "auto"` 开启 pytest 自动识别
- **Token 计数**：新增 `tiktoken>=0.7` 依赖（`token_utils` 计划中的基础）
- **依赖迁移**：`requests` → `httpx`，新增 `pydantic>=2.6`
- **CI**：GH Actions 升级到 `astral-sh/setup-uv@v6`，3.10/3.11/3.12 全绿

### Changed
- `BaseAgent` / `AnthropicAgent.conversation_with_tool` 重构：去掉手写 `requests.post` + SSE 解析，改走 `transport.chat/achat`
- 删除 `max_tool_turns=16` 熔断；改为无熔断循环（长线任务由 `tool_runner` 异步后台支持）
- `tool_timeout: float = 60` + `tool_max_workers: int = 8` 类属性（Agent 自定义超时，默认 60s 兜底）
- `BaseAgent.Connectivity` 走 `HTTPClient`，错误统一抛 `errors.APIError`
- 删除 `AnthropicAgent._call_blocking` / `_call_stream` 死方法（旧 SSE 解析逻辑已搬到 transport）
- README 重写为 PyPI 友好版

### Fixed
- 旧版 GH Actions（pip + flake8 + 无测试）持续失败问题
- 子包名 `dumplings` → `dumplingsAI` 命名不一致（主仓同步更新）
- Pydantic 校验后 `model_dump()` 把默认值也填进去（避免签名里 `**kwargs` 漏 default 报 TypeError）

## [0.1.1] - 2026-07-19

### Added
- `@builtin_tool` 装饰器：内建工具 schema 自动从签名+类型注解+docstring 推导
- `tool_registry.collect_builtin_tools(instance)` 收集器
- `BaseAgent` / `AnthropicAgent` 4 个内建方法（`ask_for_help` / `list_agents` / `attempt_completion` / `reload`）改用 `@builtin_tool` 装饰
- `_builtin_promote_overrides`：子类覆盖 `__init_subclass__` 自动继承 schema
- GH Actions CI（ruff + pytest on 3.10/3.11/3.12）
- PyPI 发布 workflow + Trusted Publishing 配置
- `tests/test_placeholder.py`：包级冒烟测试

### Changed
- 删除硬编码 `builtin_tools` 字典 / `builtin_tools_schema` 列表
- 同步 Anthropic 协议 Agent 重构
- README 重写为 PyPI 友好格式

### Fixed
- 旧版 GH Actions 工作流（`pip + flake8 + 无测试`）持续失败问题
- 子包名 `dumplings` → `dumplingsAI` 命名不一致（主仓同步更新）

## [0.1.0] - 2025-11-24

### Added
- 初始版本：多 Agent 注册、`tool_registry`、XML/FC 双模式工具调用、MCP 桥接、Skill 集成
- `BaseAgent` 抽象基类
- CLI 入口 `main.py`

[Unreleased]: https://github.com/Secret-Dumplings/dumplingsAI/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Secret-Dumplings/dumplingsAI/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Secret-Dumplings/dumplingsAI/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Secret-Dumplings/dumplingsAI/releases/tag/v0.1.0