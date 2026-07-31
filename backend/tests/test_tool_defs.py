"""M1 Phase 3 - ToolDef schema + echo/datetime mock 工具。

Source: spec/m1-react-loop AC-1 + Solution 模块划分
- 蓝图 §3.8: ToolDef schema(OpenAI 2020-12 兼容)
- 蓝图 §9.6 step 8: M1 内置 echo/datetime mock 工具演示 tool_call/tool_result
- spec AC-1: ReAct 循环执行需产出 thinking→tool_call→tool_result→final 四类事件
"""
import asyncio
from typing import Any, Awaitable, Callable

import pytest

from private_agent.tools.defs import ECHO_TOOL, DATETIME_TOOL, ToolDef, ToolResult


# ──────────────────────────────────────────────────────────────────────────────
# ToolDef dataclass
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_def_has_required_fields():
    """ToolDef 含 name/description/parameters_schema/handler 四个字段。"""
    async def _h(args: dict) -> ToolResult:
        return ToolResult(output="ok")

    td = ToolDef(
        name="demo",
        description="demo tool",
        parameters_schema={"type": "object", "properties": {}},
        handler=_h,
    )
    assert td.name == "demo"
    assert td.description == "demo tool"
    assert td.parameters_schema == {"type": "object", "properties": {}}
    assert callable(td.handler)


def test_tool_def_to_openai_schema_returns_function_format():
    """to_openai_schema() 返回 OpenAI 2020-12 兼容格式。"""
    async def _h(args: dict) -> ToolResult:
        return ToolResult(output="ok")

    td = ToolDef(
        name="echo",
        description="Echo back input",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=_h,
    )
    schema = td.to_openai_schema()
    assert schema == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo back input",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# echo mock 工具
# ──────────────────────────────────────────────────────────────────────────────


def test_echo_tool_definition_fields():
    """ECHO_TOOL 字段符合 §3.8 schema。"""
    assert ECHO_TOOL.name == "echo"
    assert isinstance(ECHO_TOOL.description, str) and ECHO_TOOL.description
    assert ECHO_TOOL.parameters_schema["type"] == "object"
    assert "text" in ECHO_TOOL.parameters_schema["properties"]
    assert ECHO_TOOL.parameters_schema["required"] == ["text"]
    assert callable(ECHO_TOOL.handler)


def test_echo_tool_handler_returns_input_text():
    """echo handler 接收 {text} 返回 ToolResult(output=text)。"""
    result = asyncio.run(ECHO_TOOL.handler({"text": "hello"}))
    assert isinstance(result, ToolResult)
    assert result.output == "hello"


def test_echo_tool_to_openai_schema_has_text_param():
    """ECHO_TOOL.to_openai_schema() 含 text 字符串参数。"""
    schema = ECHO_TOOL.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    params = schema["function"]["parameters"]
    assert params["properties"]["text"]["type"] == "string"
    assert params["required"] == ["text"]


# ──────────────────────────────────────────────────────────────────────────────
# datetime mock 工具
# ──────────────────────────────────────────────────────────────────────────────


def test_datetime_tool_definition_fields():
    """DATETIME_TOOL 字段符合 §3.8 schema。"""
    assert DATETIME_TOOL.name == "datetime"
    assert isinstance(DATETIME_TOOL.description, str) and DATETIME_TOOL.description
    assert DATETIME_TOOL.parameters_schema["type"] == "object"
    assert "properties" in DATETIME_TOOL.parameters_schema


def test_datetime_tool_handler_returns_iso_string():
    """datetime handler 返回 ToolResult(output=ISO 8601 字符串)。"""
    result = asyncio.run(DATETIME_TOOL.handler({}))
    assert isinstance(result, ToolResult)
    # ISO 8601 格式基本校验:含 'T' 且可被 fromisoformat 解析
    assert "T" in result.output
    from datetime import datetime
    datetime.fromisoformat(result.output)


def test_datetime_tool_to_openai_schema_no_required_params():
    """DATETIME_TOOL 无必填参数。"""
    schema = DATETIME_TOOL.to_openai_schema()
    params = schema["function"]["parameters"]
    assert params["required"] == [] or "required" not in params


# ──────────────────────────────────────────────────────────────────────────────
# ToolResult dataclass
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_result_default_fields():
    """ToolResult 默认 error=None, metadata={}。"""
    r = ToolResult(output="ok")
    assert r.output == "ok"
    assert r.error is None
    assert r.metadata == {}


def test_tool_result_with_error():
    """ToolResult 可承载 error 字段(工具执行失败时)。"""
    r = ToolResult(output="", error="timeout")
    assert r.error == "timeout"
