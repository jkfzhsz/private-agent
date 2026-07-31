"""测试 SchemaAdapter 双向转换与降级策略(蓝图 §5.x / spec m2-tools-lifecycle AC-9)。

MCP 2025-11-25 协议工具 schema 与内部 ToolDef 的双向转换:
- mcp_tool_to_tooldef: 从 MCP 工具发现 JSON 转为 ToolDef(handler=None)
- tooldef_to_mcp_tool: 从 ToolDef 转为 MCP 工具 schema 格式
"""
from __future__ import annotations

import pytest

from private_agent.tools.defs import ToolDef
from private_agent.tools.schema_adapter import (
    mcp_tool_to_tooldef,
    tooldef_to_mcp_tool,
)


def _mcp_schema(
    name: str | None = "test_tool",
    description: str | None = "A test tool",
    input_schema: dict | None = None,
) -> dict:
    """构造 MCP 工具发现 JSON(支持字段缺失场景)。"""
    d: dict = {}
    if name is not None:
        d["name"] = name
    if description is not None:
        d["description"] = description
    if input_schema is not None:
        d["inputSchema"] = input_schema
    return d


class TestMcpToolToTooldef:
    """AC-9: MCP 工具 schema → ToolDef 转换。"""

    def test_normal_conversion(self) -> None:
        """正常 MCP schema 应正确转换。"""
        mcp = _mcp_schema(
            name="calculator",
            description="Perform arithmetic",
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "number"}},
                "required": ["a"],
            },
        )
        td = mcp_tool_to_tooldef(mcp)
        assert td.name == "calculator"
        assert td.description == "Perform arithmetic"
        assert td.parameters_schema["type"] == "object"
        assert "a" in td.parameters_schema["properties"]
        assert td.handler is None

    def test_normal_conversion_returns_tooldef_instance(self) -> None:
        """返回值类型应为 ToolDef。"""
        mcp = _mcp_schema(name="echo", description="Echo", input_schema={"type": "object"})
        td = mcp_tool_to_tooldef(mcp)
        assert isinstance(td, ToolDef)

    def test_description_truncated_to_1024_chars(self) -> None:
        """description 超过 1024 字符时应截断并保留前 1024 字符。"""
        long_desc = "x" * 2000
        mcp = _mcp_schema(description=long_desc)
        td = mcp_tool_to_tooldef(mcp)
        assert len(td.description) == 1024
        assert td.description == "x" * 1024

    def test_name_missing_uses_unknown_fallback(self) -> None:
        """name 缺失时应使用 'unknown_tool' 作为降级名称。"""
        mcp = _mcp_schema(name=None, description="no name", input_schema={"type": "object"})
        td = mcp_tool_to_tooldef(mcp)
        assert td.name == "unknown_tool"

    def test_description_missing_uses_empty_string(self) -> None:
        """description 缺失时应使用空字符串。"""
        mcp = _mcp_schema(description=None, input_schema={"type": "object"})
        td = mcp_tool_to_tooldef(mcp)
        assert td.description == ""

    def test_input_schema_missing_uses_default(self) -> None:
        """inputSchema 缺失时应使用默认空 schema。"""
        mcp = _mcp_schema(input_schema=None)
        td = mcp_tool_to_tooldef(mcp)
        assert td.parameters_schema == {"type": "object", "properties": {}}

    def test_input_schema_without_type_uses_default(self) -> None:
        """inputSchema 缺少 type 字段时应使用默认空 schema。"""
        mcp = _mcp_schema(input_schema={"properties": {"x": {"type": "string"}}})
        td = mcp_tool_to_tooldef(mcp)
        assert td.parameters_schema == {"type": "object", "properties": {}}

    def test_input_schema_without_properties_uses_default(self) -> None:
        """inputSchema 缺少 properties 字段时应使用默认空 schema。"""
        mcp = _mcp_schema(input_schema={"type": "object"})
        td = mcp_tool_to_tooldef(mcp)
        assert td.parameters_schema == {"type": "object", "properties": {}}


class TestTooldefToMcpTool:
    """AC-9: ToolDef → MCP 工具 schema 转换。"""

    def test_normal_conversion(self) -> None:
        """正常 ToolDef 应正确转为 MCP schema。"""
        td = ToolDef(
            name="calculator",
            description="Calc",
            parameters_schema={"type": "object", "properties": {"x": {"type": "number"}}},
            handler=None,
        )
        mcp = tooldef_to_mcp_tool(td)
        assert mcp["name"] == "calculator"
        assert mcp["description"] == "Calc"
        assert mcp["inputSchema"] == {"type": "object", "properties": {"x": {"type": "number"}}}

    def test_returns_dict_with_required_keys(self) -> None:
        """返回值应包含 name/description/inputSchema 三个键。"""
        td = ToolDef(
            name="a", description="b", parameters_schema={"type": "object", "properties": {}}, handler=None
        )
        mcp = tooldef_to_mcp_tool(td)
        assert set(mcp.keys()) == {"name", "description", "inputSchema"}