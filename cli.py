# -*- coding: utf-8 -*-
"""
Dumplings 命令行入口
====================

::

    dumplings --help        全部子命令
    dumplings --doctor      环境自检（Python / httpx / pydantic / API Key）
    dumplings --demo        跑一个最小离线示例（不连真 LLM）

也可以通过 ``python -m dumplings`` 或 ``python -m dumplingsAI`` 进入。
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from typing import List, Optional

from ._banner import print_banner
from ._banner import silence as silence_banner

__all__ = ["main", "cmd_doctor", "cmd_demo", "cmd_help"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dumplings",
        description=(
            "dumplingsAI 命令行入口。\n"
            "主要用途：环境自检 + 离线 demo + 帮助。\n"
            "（dumplingsAI 仍可用，但 dumplings 是新的主入口。）"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--doctor", action="store_true",
        help="环境自检：Python 版本 / 关键依赖 / API Key / Agent 注册",
    )
    p.add_argument(
        "--demo", action="store_true",
        help="跑一个最小离线 demo（不连真实 LLM）",
    )
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="不打启动 banner",
    )
    p.add_argument(
        "--version", action="store_true",
        help="打印版本号并退出",
    )
    return p


def _check_python_version() -> tuple[bool, str]:
    v = sys.version_info
    ok = (v >= (3, 10))
    return ok, f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else "（< 3.10 不受支持）")


def _check_module(name: str) -> tuple[bool, str]:
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "(no __version__)")
        return True, ver
    except ImportError as e:
        return False, f"ImportError: {e}"


def _check_api_keys() -> list[tuple[str, bool, str]]:
    """检查常见 LLM provider 的 API Key 环境变量"""
    keys = [
        ("API_KEY", "OpenAI 兼容 / 自家网关通用"),
        ("OPENAI_API_KEY", "OpenAI 官方"),
        ("ANTHROPIC_API_KEY", "Anthropic 官方"),
        ("DASHSCOPE_API_KEY", "阿里云 DashScope"),
    ]
    out: list[tuple[str, bool, str]] = []
    for env, who in keys:
        val = os.environ.get(env)
        out.append((env, bool(val), who + (" — " + (val[:8] + "..." if val else "未设置"))))
    return out


def _check_agents() -> tuple[bool, list[str]]:
    try:
        from dumplingsAI import agent_list
        names = sorted({a.name for a in agent_list.values() if hasattr(a, "name")})
        return True, names
    except Exception as e:
        return False, [f"import agent_list 失败：{e}"]


def cmd_doctor(_args: argparse.Namespace) -> int:
    """环境自检"""
    print("dumplingsAI Doctor\n" + "=" * 50)

    # Python 版本
    ok_py, py = _check_python_version()
    print(f"  {'✓' if ok_py else '✗'} Python {py}")

    # 关键依赖
    for name in ("httpx", "pydantic", "tiktoken", "loguru", "mcp"):
        ok, info = _check_module(name)
        print(f"  {'✓' if ok else '✗'} {name} {info}")

    # API Key
    print("  ─ API Key 环境变量")
    for env, ok, label in _check_api_keys():
        marker = "✓" if ok else "✗"
        print(f"    {marker} {env:18s}  {label}")

    # Agent 注册
    ok_ag, names = _check_agents()
    if ok_ag:
        if names:
            print(f"  ✓ 已注册 Agent: {', '.join(names)}")
        else:
            print("  ─ (尚未注册任何 Agent)")
    else:
        print(f"  ✗ {names[0]}")

    print()
    if not ok_py:
        print("⚠ Python 版本过低，请升级到 3.10+")
    print("下一步：dumplings --demo 跑一个最简示例")
    return 0


def cmd_demo(_args: argparse.Namespace) -> int:
    """离线 demo：不连 LLM，直接看框架结构"""
    print("dumplingsAI 离线 Demo\n" + "=" * 50)
    print("本 demo 不连真实 LLM，仅展示框架对象。\n")

    from dumplingsAI import (
        BaseAgent,
        agent_list,
        builtin_tool,
        register_agent,
    )
    from dumplingsAI.agent_tool import collect_builtin_tools
    from dumplingsAI.errors import classify

    print("  ✓ BaseAgent / builtin_tool / tool_registry / register_agent import OK")
    print(f"  ✓ classify(429, 'rate') → {classify(429, 'rate limited').__class__.__name__}")

    # 临时构造一个 Agent
    import uuid as _uuid
    u = _uuid.uuid4().hex

    @register_agent(u, "demo_agent", "demo 用 Agent")
    class DemoAgent(BaseAgent):
        prompt = "demo"
        api_provider = "https://example.com"
        model_name = "demo-model"
        api_key = "demo-key"

        @builtin_tool(description="hello world")
        def hello(self, name: str) -> str:
            return f"hello, {name}!"

    ag = agent_list["demo_agent"]
    schemas = collect_builtin_tools(ag)
    print(f"  ✓ DemoAgent 已注册，发现 {len(schemas)} 个内建工具")
    for s in schemas:
        print(f"      - {s['function']['name']}")

    print("\n  → 接下来你可以：")
    print("      import dumplings  (或 import dumplingsAI)")
    print("      agent = agent_list['demo_agent']")
    print("      agent.conversation_with_tool('hi')")
    return 0


def cmd_help(_args: argparse.Namespace) -> int:
    """打印简版 help"""
    _build_parser().print_help()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.quiet:
        silence_banner()

    if args.version:
        from dumplingsAI import __version__
        print(__version__)
        return 0

    if not (args.doctor or args.demo):
        # 默认行为：先打 banner 再走 doctor，让用户看到诊断
        print_banner()
        return cmd_doctor(args)

    if args.doctor:
        return cmd_doctor(args)
    if args.demo:
        return cmd_demo(args)
    return 0
