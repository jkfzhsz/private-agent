"""蓝图 §5.x / spec m2-tools-lifecycle - ToolRegistry 工具注册管理层。

统一管理内置工具和 MCP 发现工具的注册、查询、合并。
"""
from __future__ import annotations

from private_agent.tools.defs import ToolDef

__all__ = ["ToolRegistry"]


class ToolRegistry:
    """工具注册管理层。

    维护内置工具和 MCP 发现工具的注册表，提供统一的查询接口。
    - 内置工具与 MCP 工具重名时，内置工具优先级更高。
    - 多 MCP 服务同名工具时，先注册者优先（对应配置声明顺序）。
    """

    def __init__(self) -> None:
        self._builtins: dict[str, ToolDef] = {}
        self._mcp_tools: dict[str, list[ToolDef]] = {}  # server_name → [ToolDef]

    def register_builtin(self, name: str, tool_def: ToolDef) -> None:
        """注册内置工具。

        Args:
            name: 工具名称(内置命名空间内不重复)。
            tool_def: 工具定义。
        """
        self._builtins[name] = tool_def

    def register_mcp(self, server_name: str, tool_def: ToolDef) -> None:
        """注册 MCP 发现工具。

        server_name = config.yaml mcp.servers[] 内唯一 id 标识，
        用于区分同名工具归属来源服务。

        Args:
            server_name: MCP 服务标识。
            tool_def: 工具定义。
        """
        self._mcp_tools.setdefault(server_name, []).append(tool_def)

    def list_tools(self) -> list[ToolDef]:
        """返回内置 + MCP 合并后的工具列表。

        内置工具始终排在 MCP 工具之前。
        MCP 同名工具按配置声明顺序(先注册者优先)。
        """
        result: list[ToolDef] = list(self._builtins.values())
        seen: set[str] = set(self._builtins.keys())
        for server_tools in self._mcp_tools.values():
            for td in server_tools:
                if td.name not in seen:
                    result.append(td)
                    seen.add(td.name)
        return result

    def list_tools_for_session(self, whitelist: list[str] | None) -> list[ToolDef]:
        """M3 §7.5: 按 Skill 工具白名单过滤(AC-3)。

        Args:
            whitelist: 允许的工具名列表;None 时返回全部(保 M1 行为)。

        Returns:
            过滤后的 ToolDef 列表(仅含白名单内且已注册的工具)。
        """
        all_tools = self.list_tools()
        if whitelist is None:
            return all_tools
        whitelist_set = set(whitelist)
        return [t for t in all_tools if t.name in whitelist_set]

    def get_tool(self, name: str) -> ToolDef | None:
        """按名查找工具。

        优先级: 内置工具 > MCP 工具(先注册者优先)。

        Args:
            name: 工具名称。

        Returns:
            匹配的 ToolDef，未找到时返回 None。
        """
        if name in self._builtins:
            return self._builtins[name]
        for server_tools in self._mcp_tools.values():
            for td in server_tools:
                if td.name == name:
                    return td
        return None