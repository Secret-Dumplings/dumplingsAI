# -*- coding: utf-8 -*-
"""
Pydantic structured output 单测
"""
from typing import Optional

import pytest
from dumplingsAI.agent_tool import (
    _validate_tool_args_for,
    builtin_tool,
)
from pydantic import BaseModel, Field


class _Stub:
    """用于 collect_builtin_tools 的最小 stub"""
    def __init__(self):
        self.uuid = "stub-uuid"


def _make_agent_cls():
    """动态构造一个带 @builtin_tool 方法的类，让 collect_builtin_tools 能找到。"""
    class AddArgs(BaseModel):
        a: int
        b: int

    class Opt(BaseModel):
        x: int = 42
        name: str = "default"

    class Strict(BaseModel):
        x: int

    class _Agent:
        def __init__(self):
            self.uuid = "stub-uuid"

    @builtin_tool(description="add", params_model=AddArgs)
    def add(self, a: int, b: int) -> int:
        return a + b

    @builtin_tool(description="opt", params_model=Opt)
    def op(self, x: int = 42, name: str = "default") -> str:
        return f"{x}-{name}"

    @builtin_tool(description="strict", params_model=Strict)
    def fn(self, x: int) -> int:
        return x

    @builtin_tool(description="no model")
    def no_model(self, x: int) -> int:
        return x

    _Agent.add = add
    _Agent.op = op
    _Agent.fn = fn
    _Agent.no_model = no_model
    return _Agent


# Module-level decorated functions for schema-shape tests
def _schema_only():
    class AddArgs(BaseModel):
        a: float = Field(..., description="first")
        b: float = Field(..., description="second")

    @builtin_tool(description="sum two numbers", params_model=AddArgs)
    def add(self, a: float, b: float) -> float:
        return a + b

    class Opt(BaseModel):
        x: int = 0
        y: Optional[str] = None

    @builtin_tool(description="optional", params_model=Opt)
    def op(self, x: int = 0, y: Optional[str] = None) -> str:
        return f"{x}-{y}"

    return add, op


def test_params_model_generates_schema():
    add, _ = _schema_only()
    meta = add.__builtin_tool_meta__
    assert meta["name"] == "add"
    assert meta["description"] == "sum two numbers"
    assert meta["params_model"] is not None
    assert meta["input_schema"]["type"] == "object"
    assert "a" in meta["input_schema"]["properties"]
    assert meta["input_schema"]["properties"]["a"]["type"] == "number"
    assert "a" in meta["input_schema"]["required"]


def test_params_model_default_values_not_required():
    _, op = _schema_only()
    schema = op.__builtin_tool_meta__["input_schema"]
    assert "x" not in schema.get("required", [])
    assert "y" not in schema.get("required", [])


def test_validate_passes_through():
    Agent = _make_agent_cls()
    inst = Agent()
    validated = _validate_tool_args_for(inst, "add", {"a": 1, "b": 2})
    assert validated == {"a": 1, "b": 2}


def test_validate_default_fills_missing():
    """Pydantic model_dump 会把默认值填进去"""
    Agent = _make_agent_cls()
    inst = Agent()
    validated = _validate_tool_args_for(inst, "op", {})
    assert validated == {"x": 42, "name": "default"}


def test_validate_raises_on_bad_input():
    Agent = _make_agent_cls()
    inst = Agent()
    with pytest.raises(ValueError, match="params validation failed"):
        _validate_tool_args_for(inst, "fn", {"x": "not an int"})


def test_validate_unknown_tool_returns_args_unchanged():
    Agent = _make_agent_cls()
    inst = Agent()
    out = _validate_tool_args_for(inst, "some_external_tool", {"a": 1})
    assert out == {"a": 1}


def test_validate_tool_without_params_model_passthrough():
    Agent = _make_agent_cls()
    inst = Agent()
    out = _validate_tool_args_for(inst, "no_model", {"x": 5})
    assert out == {"x": 5}
