"""蓝图 §2.15 tools 子包 - 工具定义与 M2 工具生命周期。

M1 导出:
- ToolDef / ToolResult: schema 与结果结构
- ECHO_TOOL / DATETIME_TOOL: M1 mock 工具

M2 导出:
- ToolRegistry: 工具注册管理层
- MCPClient / MCPClientConfig: MCP 协议客户端
- mcp_tool_to_tooldef / tooldef_to_mcp_tool: Schema 双向转换
- 7 类内置工具: calculator / datetime / file_read / file_write / http_request / web_search / read_artifact
"""
from private_agent.tools.defs import DATETIME_TOOL, ECHO_TOOL, ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry
from private_agent.tools.mcp_client import MCPClient, MCPClientConfig
from private_agent.tools.schema_adapter import mcp_tool_to_tooldef, tooldef_to_mcp_tool

__all__ = [
    "ToolDef", "ToolResult", "ECHO_TOOL", "DATETIME_TOOL",
    "ToolRegistry", "MCPClient", "MCPClientConfig",
    "mcp_tool_to_tooldef", "tooldef_to_mcp_tool",
]