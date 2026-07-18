# -*- coding: utf-8 -*-
"""
Agent Skills 开放标准实现
==========================

基于 Claude Code 的 SKILL.md 规范，实现 dumplingsAI 的 Skills 系统。

每个 Skill 是一个目录，包含 SKILL.md 入口文件：
    - YAML frontmatter（--- 之间）：name, description, 调用控制等
    - Markdown 内容：Skill 的指令文本

用法:
    import dumplingsAI

    # 扫描并注册 skills
    dumplingsAI.skill_registry.scan_and_register([Path(".")])

    # 直接注册单个 skill 目录
    dumplingsAI.skill_registry.register_skill(Path(".claude/skills/my-skill"))

    # Agent 自动发现并使用 skills（通过 tool_registry 桥接）
"""
import re
import subprocess
from pathlib import Path
from typing import Optional

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .logging_config import logger

# ==================== Skill 类 ====================

class Skill:
    """
    单个 Skill 的解析和执行

    Skill 是一个目录 + SKILL.md 文件，遵循 Agent Skills 开放标准。
    """

    def __init__(self, skill_dir: Path):
        """
        从目录加载 Skill

        Args:
            skill_dir: Skill 目录路径，该目录下必须包含 SKILL.md
        """
        self.skill_dir = Path(skill_dir)
        self._skill_file = self.skill_dir / "SKILL.md"

        if not self._skill_file.exists():
            raise FileNotFoundError(
                f"SKILL.md 不存在: {self._skill_file}"
            )

        self.raw_content = self._skill_file.read_text(encoding="utf-8")
        self._parse_raw_content()

        logger.debug(
            f"Skill 已加载: {self.name} @ {self.skill_dir}"
        )

    def _parse_raw_content(self):
        """解析 SKILL.md 的 raw content，分离 frontmatter 和 markdown body"""
        raw = self.raw_content.strip()

        if raw.startswith("---"):
            parts = raw.split("---", 2)
            # parts[0] 为空（第一个 --- 之前）
            # parts[1] 为 YAML frontmatter
            # parts[2] 为 markdown 内容
            yaml_str = parts[1].strip()
            self._frontmatter = yaml.safe_load(yaml_str) or {}
            self.content = parts[2].strip()
        else:
            self._frontmatter = {}
            self.content = raw

        # 提取 frontmatter 字段
        self.name = self._frontmatter.get("name", self.skill_dir.name)
        self.description = self._frontmatter.get("description", "")
        self.when_to_use = self._frontmatter.get("when_to_use", "")
        self.argument_hint = self._frontmatter.get("argument-hint", "")
        self.arguments = self._frontmatter.get("arguments", [])
        self.disable_model_invocation = self._frontmatter.get("disable-model-invocation", False)
        self.user_invocable = self._frontmatter.get("user-invocable", True)
        self.context = self._frontmatter.get("context", "inline")
        self.agent_type = self._frontmatter.get("agent", None)
        self.paths = self._frontmatter.get("paths", [])
        self.shell = self._frontmatter.get("shell", "bash")
        self.model = self._frontmatter.get("model", None)
        self.effort = self._frontmatter.get("effort", None)
        self.allowed_tools = self._frontmatter.get("allowed-tools", [])
        self.hooks = self._frontmatter.get("hooks", None)

        # 生成 parameters schema（从 arguments 推导）
        self._build_parameters_schema()

        # 如果 description 为空，用 content 第一段
        if not self.description and self.content:
            first_para = self.content.split("\n\n")[0].strip()
            self.description = first_para[:200]

    def _build_parameters_schema(self):
        """从 arguments 字段生成 OpenAI parameters schema"""
        if isinstance(self.arguments, str):
            self.arguments = self.arguments.split()

        properties = {}
        required = []

        for i, arg in enumerate(self.arguments):
            if isinstance(arg, dict):
                # 支持字典格式：{"name": "issue", "description": "..."}
                arg_name = arg.get("name", f"arg{i}")
                arg_desc = arg.get("description", "")
            else:
                arg_name = str(arg)
                arg_desc = ""

            properties[arg_name] = {
                "type": "string",
                "description": arg_desc,
            }
            required.append(arg_name)

        self.parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    # ---- 渲染引擎 ----

    def render(self, arguments: Optional[dict] = None, extra_vars: Optional[dict] = None) -> str:
        """
        渲染 Skill 内容

        执行参数替换和 shell 预处理，返回最终的 prompt 文本。

        Args:
            arguments: 参数字典（来自工具调用）
            extra_vars: 额外变量（如 _session_id, _skill_dir）

        Returns:
            渲染后的 markdown 文本（作为 prompt 使用）
        """
        args = arguments or {}
        text = self.content

        # 1. 参数替换
        text = self._substitute_params(text, args)

        # 2. Shell 预处理
        text = self._execute_shell_commands(text)

        logger.debug(f"Skill '{self.name}' 渲染完成，输出长度: {len(text)}")
        return text

    def _substitute_params(self, text: str, arguments: dict) -> str:
        """
        参数替换引擎

        支持:
        - $name: 命名参数
        - $0-$9: 位置参数
        - $ARGUMENTS: 完整参数字符串
        - ${CLAUDE_SKILL_DIR}: skill 目录路径
        - ${CLAUDE_SESSION_ID}: 会话 ID
        """
        # 命名参数: $name
        for key, value in arguments.items():
            if key.startswith("_"):
                continue  # 跳过内部变量
            text = text.replace(f"${key}", str(value))

        # 位置参数: $0-$9
        arg_list = arguments.get("_arg_list", [])
        for i, val in enumerate(arg_list[:10]):
            text = text.replace(f"${i}", str(val))

        # $ARGUMENTS
        if "_all_arguments" in arguments:
            text = text.replace("$ARGUMENTS", str(arguments["_all_arguments"]))

        # ${CLAUDE_SKILL_DIR}
        text = text.replace("${CLAUDE_SKILL_DIR}", str(self.skill_dir))

        # ${CLAUDE_SESSION_ID}
        session_id = arguments.get("_session_id", "default")
        text = text.replace("${CLAUDE_SESSION_ID}", session_id)

        return text

    def _execute_shell_commands(self, text: str) -> str:
        """
        Shell 预处理

        执行 `` !`command` `` 内联命令和 `` ```! `` 代码块，输出替换占位符。
        """
        # ```! 代码块（多行命令）
        def replace_code_block(match):
            cmd = match.group(1).strip()
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(self.skill_dir),
                )
                return result.stdout
            except subprocess.TimeoutExpired:
                return "[Shell 命令执行超时]"
            except Exception as e:
                logger.error(f"Skill '{self.name}' shell 代码块执行失败: {e}")
                return f"[Shell 命令执行失败: {e}]"

        text = re.sub(r"```!\s*\n(.*?)```", replace_code_block, text, flags=re.S)

        # !`command` 内联命令
        def replace_inline(match):
            cmd = match.group(1)
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(self.skill_dir),
                )
                return result.stdout.strip()
            except subprocess.TimeoutExpired:
                return "[命令执行超时]"
            except Exception as e:
                logger.error(f"Skill '{self.name}' 内联命令执行失败: {e}")
                return f"[命令执行失败: {e}]"

        text = re.sub(r"!`([^`]+)`", replace_inline, text)

        return text

    # ---- 元数据接口 ----

    def get_full_description(self) -> str:
        """
        获取完整描述（description + when_to_use），截断至 1536 字符。

        用于技能列表显示，符合 Agent Skills 开放标准。
        """
        desc = self.description
        if self.when_to_use:
            desc = f"{desc}\n{self.when_to_use}"
        return desc[:1536]

    def get_tool_schema(self) -> dict:
        """生成 OpenAI function calling schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.get_full_description(),
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict:
        """序列化 Skill 为字典"""
        return {
            "name": self.name,
            "skill_dir": str(self.skill_dir),
            "description": self.description,
            "when_to_use": self.when_to_use,
            "context": self.context,
            "disable_model_invocation": self.disable_model_invocation,
            "user_invocable": self.user_invocable,
            "parameters": self.parameters,
            "paths": self.paths,
            "shell": self.shell,
        }

    def reload(self):
        """重新加载 SKILL.md"""
        try:
            self.raw_content = self._skill_file.read_text(encoding="utf-8")
            self._parse_raw_content()
            logger.info(f"Skill '{self.name}' 已重新加载")
        except Exception as e:
            logger.error(f"Skill '{self.name}' 重新加载失败: {e}")


# ==================== SkillRegistry 注册表 ====================

class SkillRegistry:
    """
    Skill 注册表 -- 管理所有已注册的 Skills

    提供:
    - 目录扫描和自动发现
    - Skill 注册和注销
    - 查询和搜索
    - 文件变更监控
    """

    def __init__(self):
        self._skills: dict = {}         # name -> Skill
        self._skill_dirs: dict = {}     # name -> Path（用于 reload）
        self._watchers: dict = {}       # Path -> Observer
        logger.trace("SkillRegistry 已创建")

    # ---- 目录扫描 ----

    def scan_and_register(self, base_paths, auto_watch: bool = True):
        """
        从目录扫描并注册所有 SKILL.md

        支持:
        - 顶级 .claude/skills/
        - 嵌套 .claude/skills/（monorepo 支持）

        Args:
            base_paths: 基础路径列表
            auto_watch: 是否自动监控目录变更
        """
        for base in base_paths:
            base = Path(base)
            if not base.exists():
                logger.warning(f"基础路径不存在，跳过: {base}")
                continue

            logger.debug(f"扫描 skills: {base}")

            # 1. 顶级 .claude/skills/
            skills_dir = base / ".claude" / "skills"
            if skills_dir.is_dir():
                self._scan_skills_dir(skills_dir, auto_watch)

            # 2. 嵌套发现
            for nested in base.rglob(".claude/skills"):
                if nested.is_dir():
                    self._scan_skills_dir(nested, auto_watch)

        logger.info(
            f"Skills 扫描完成，已注册 {len(self._skills)} 个 skills: {list(self._skills.keys())}"
        )

    def _scan_skills_dir(self, skills_dir: Path, auto_watch: bool = True):
        """扫描单个 skills 目录"""
        if not skills_dir.is_dir():
            return

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                self.register_skill(skill_dir)

        # 自动监控
        if auto_watch and skills_dir not in self._watchers:
            self._start_watching(skills_dir)

    # ---- 注册和注销 ----

    def register_skill(self, skill_dir: Path) -> Optional[Skill]:
        """
        注册单个 Skill 目录

        Args:
            skill_dir: Skill 目录路径

        Returns:
            Skill 实例，失败返回 None
        """
        try:
            skill = Skill(skill_dir)
            self._skills[skill.name] = skill
            self._skill_dirs[skill.name] = skill_dir

            # 桥接到 tool_registry
            from .skill_bridge import register_skill_as_tool
            register_skill_as_tool(skill)

            logger.info(f"Skill 已注册: {skill.name} @ {skill_dir}")
            return skill

        except Exception as e:
            logger.error(f"注册 Skill 失败 {skill_dir}: {e}")
            return None

    def unregister_skill(self, name: str):
        """
        注销 Skill

        Args:
            name: Skill 名称
        """
        skill = self._skills.pop(name, None)
        self._skill_dirs.pop(name, None)

        if skill:
            from .skill_bridge import unregister_skill_from_tool
            unregister_skill_from_tool(name)
            logger.info(f"Skill 已注销: {name}")

    def reload_skill(self, name: str):
        """重新加载指定 Skill"""
        skill = self._skills.get(name)
        if skill:
            skill.reload()
            # 更新 tool_registry 中的描述
            from .skill_bridge import register_skill_as_tool, unregister_skill_from_tool
            unregister_skill_from_tool(name)
            register_skill_as_tool(skill)

    # ---- 查询接口 ----

    def get_skill(self, name: str) -> Optional[Skill]:
        """按名称获取 Skill"""
        return self._skills.get(name)

    def list_skills(self) -> list:
        """列出所有 Skills 摘要"""
        return [s.to_dict() for s in self._skills.values()]

    def search_skills(self, keyword: str = "") -> list:
        """
        关键词搜索 Skills

        在 name, description, when_to_use 中搜索
        """
        if not keyword:
            return self.list_skills()

        kw = keyword.lower()
        results = []
        for skill in self._skills.values():
            if (
                kw in skill.name.lower()
                or kw in skill.description.lower()
                or kw in skill.when_to_use.lower()
            ):
                results.append(skill.to_dict())
        return results

    def get_callable_skills(self) -> list:
        """获取 AI 可调用的 Skills（排除 disable_model_invocation=True 的）"""
        return [s for s in self._skills.values() if not s.disable_model_invocation]

    def get_all_tool_schemas(self) -> list:
        """获取所有 AI 可调用的 Skill 的 OpenAI schema"""
        return [s.get_tool_schema() for s in self.get_callable_skills()]

    def get_skills_prompt_text(self, agent_uuid: str = None) -> str:
        """
        生成 Skills 的文本描述（供 Agent prompt 注入）

        格式与工具列表一致，便于 Agent 理解可用技能
        """
        skills_text = ""
        for skill in self.get_callable_skills():
            desc = skill.get_full_description()
            skills_text += f"- {skill.name}: {desc}\n"

        if skills_text:
            return f"\n\n你可以使用以下技能：\n{skills_text}"
        return ""

    # ---- 变更监控 ----

    def _start_watching(self, skills_dir: Path):
        """启动目录监控"""
        try:
            handler = _SkillChangeHandler(self)
            observer = Observer()
            observer.schedule(handler, str(skills_dir), recursive=True)
            observer.start()
            self._watchers[skills_dir] = observer
            logger.debug(f"目录监控已启动: {skills_dir}")
        except Exception as e:
            logger.warning(f"启动目录监控失败 {skills_dir}: {e}")

    def reload_if_changed(self):
        """重新加载所有变更过的 Skills"""
        for name, skill_dir in list(self._skill_dirs.items()):
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                skill = self._skills.get(name)
                if skill:
                    try:
                        current_content = skill_file.read_text(encoding="utf-8")
                        if current_content != skill.raw_content:
                            logger.info(f"检测到 Skill 变更，重新加载: {name}")
                            self.reload_skill(name)
                    except Exception as e:
                        logger.warning(f"检查 Skill 变更失败 {name}: {e}")

    def stop_watchers(self):
        """停止所有监控"""
        for observer in self._watchers.values():
            observer.stop()
        for observer in self._watchers.values():
            observer.join(timeout=2)
        self._watchers.clear()
        logger.info("所有 Skill 目录监控已停止")


# ==================== 文件变更处理器 ====================

class _SkillChangeHandler(FileSystemEventHandler):
    """SKILL.md 文件变更处理器"""

    def __init__(self, registry: SkillRegistry):
        super().__init__()
        self.registry = registry

    def on_modified(self, event):
        if event.is_directory:
            return
        if "SKILL.md" not in event.src_path:
            return

        # 从路径推导 skill 名称
        skill_path = Path(event.src_path).parent
        # 查找对应的 skill
        for name, skill_dir in self.registry._skill_dirs.items():
            if skill_dir == skill_path:
                logger.info(f"检测到 SKILL.md 变更: {event.src_path}")
                self.registry.reload_skill(name)
                break


# ==================== 全局实例 ====================

skill_registry = SkillRegistry()
logger.trace("skill_registry 全局实例已创建")
