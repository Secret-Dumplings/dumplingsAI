# Changelog

dumplingsAI 的所有显著变更记录。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- `http_utils`：中央 HTTP 客户端，retry + timeout + 错误分类（Phase 1）
- `errors.py`：异常类型体系（`APIError` / `RateLimitError` / `InternalServerError` 等）
- `token_utils`：基于 `tiktoken` 的 token 估算
- `BaseAgent` / `AnthropicAgent` 新增 `timeout` / `max_retries` 类属性
- `builtin_tool` 支持 `params_model: type[BaseModel]`（Pydantic 结构化输出）
- `httpx` 异步支持：`aconversation_with_tool`
- 全面 Pydantic 模型化（Phase 4）

### Changed
- 统一 README，新增 badge / 路线图
- `pyproject.toml` description 改成英文

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

[Unreleased]: https://github.com/Secret-Dumplings/dumplingsAI/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Secret-Dumplings/dumplingsAI/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Secret-Dumplings/dumplingsAI/releases/tag/v0.1.0