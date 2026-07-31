"""蓝图 §5.x / spec m2-tools-lifecycle - SchemaAdapter 双向转换。

MCP 2025-11-25 协议工具 schema 与内部 ToolDef 的双向转换，
包含字段缺失、描述截断等降级策略。
"""
from __future__ import annotations

import logging

from private_agent.tools.defs import ToolDef

logger = logging.getLogger(__name__)

__all__ = ["mcp_tool_to_tooldef", "tooldef_to_mcp_tool"]

_DEFAULT_INPUT_SCHEMA: dict = {"type": "object", "properties": {}}
_DESC_MAX_LENGTH = 1024


def mcp_tool_to_tooldef(mcp_tool: dict) -> ToolDef:
    """MCP 工具发现 JSON → ToolDef(handler=None)。

    降级策略:
    - name 缺失 → WARN + "unknown_tool"
    - description 缺失 → 空字符串
    - description 超 1024 → 截断
    - inputSchema 缺失/格式异常 → 默认空 schema

    Args:
        mcp_tool: MCP 工具发现返回的 JSON dict。

    Returns:
        转换后的 ToolDef(handler 为 None)。
    """
    name = mcp_tool.get("name")
    if not name:
        logger.warning("MCP tool missing 'name', falling back to 'unknown_tool'")
        name = "unknown_tool"

    description = mcp_tool.get("description", "")
    if description is None:
        description = ""
    if len(description) > _DESC_MAX_LENGTH:
        description = description[:_DESC_MAX_LENGTH]

    input_schema = mcp_tool.get("inputSchema")
    if not _is_valid_input_schema(input_schema):
        input_schema = _DEFAULT_INPUT_SCHEMA

    return ToolDef(
        name=name,
        description=description,
        parameters_schema=input_schema,
        handler=None,
    )


def tooldef_to_mcp_tool(tool_def: ToolDef) -> dict:
    """ToolDef → MCP 工具 schema 格式。

    Args:
        tool_def: 内部工具定义。

    Returns:
        MCP 工具发现格式的 dict。
    """
    return {
        "name": tool_def.name,
        "description": tool_def.description,
        "inputSchema": tool_def.parameters_schema,
    }


def _is_valid_input_schema(schema: dict | None) -> bool:
    """检查 inputSchema 是否包含必要的结构字段。

    Args:
        schema: 待检查的 schema dict。

    Returns:
        True 当且仅当 schema 包含 type 和 properties 字段。
    """
    if not isinstance(schema, dict):
        return False
    if not schema.get("type"):
        return False
    if "properties" not in schema:
        return False
    return True