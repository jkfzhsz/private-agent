"""测试 ToolRegistry 注册管理层(蓝图 §5.x / spec m2-tools-lifecycle AC-1/2/10)。"""
from __future__ import annotations

import pytest

from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def sample_tool() -> ToolDef:
    async def _handler(args: dict) -> ToolResult:
        return ToolResult(output="ok")
    return ToolDef(
        name="calculator",
        description="A test calculator",
        parameters_schema={"type": "object", "properties": {}},
        handler=_handler,
    )


class TestToolRegistry:
    """AC-1: ToolRegistry.register_builtin 注册与查询。"""

    async def test_register_builtin_adds_tool(self, registry: ToolRegistry, sample_tool: ToolDef) -> None:
        """register_builtin 后 list_tools 应包含该工具。"""
        registry.register_builtin("calculator", sample_tool)
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "calculator" in names

    async def test_get_tool_returns_registered_tool(
        self, registry: ToolRegistry, sample_tool: ToolDef
    ) -> None:
        """get_tool 应返回已注册的工具。"""
        registry.register_builtin("calculator", sample_tool)
        result = registry.get_tool("calculator")
        assert result is not None
        assert result.name == "calculator"

    async def test_get_tool_unknown_returns_none(self, registry: ToolRegistry) -> None:
        """get_tool 对未注册的名称应返回 None。"""
        result = registry.get_tool("nonexistent")
        assert result is None

    async def test_list_tools_empty_initially(self, registry: ToolRegistry) -> None:
        """新建的 ToolRegistry 的 list_tools 应返回空列表。"""
        assert registry.list_tools() == []

    async def test_register_mcp_adds_tool(self, registry: ToolRegistry, sample_tool: ToolDef) -> None:
        """AC-2: register_mcp 后 list_tools 应包含远端 MCP 工具条目。"""
        registry.register_mcp("filesystem", sample_tool)
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "calculator" in names

    async def test_builtin_overrides_mcp_on_name_conflict(
        self, registry: ToolRegistry, sample_tool: ToolDef
    ) -> None:
        """AC-10: 内置工具与 MCP 工具同名时，内置工具优先级更高。"""
        async def _mcp_handler(args: dict) -> ToolResult:
            return ToolResult(output="mcp")
        mcp_tool = ToolDef(
            name="calculator",
            description="MCP calculator",
            parameters_schema={"type": "object", "properties": {}},
            handler=_mcp_handler,
        )
        registry.register_mcp("filesystem", mcp_tool)
        registry.register_builtin("calculator", sample_tool)
        result = registry.get_tool("calculator")
        assert result is not None
        # 应返回内置工具（非 MCP 工具）
        assert result.description == "A test calculator"

    async def test_mcp_duplicate_name_uses_config_order(
        self, registry: ToolRegistry,
    ) -> None:
        """AC-10: 多 MCP 服务同名工具时，先注册者优先。"""
        async def _handler_a(args: dict) -> ToolResult:
            return ToolResult(output="a")
        async def _handler_b(args: dict) -> ToolResult:
            return ToolResult(output="b")
        tool_a = ToolDef(name="read", description="server A read", parameters_schema={"type": "object", "properties": {}}, handler=_handler_a)
        tool_b = ToolDef(name="read", description="server B read", parameters_schema={"type": "object", "properties": {}}, handler=_handler_b)
        registry.register_mcp("server_a", tool_a)
        registry.register_mcp("server_b", tool_b)
        result = registry.get_tool("read")
        assert result is not None
        assert result.description == "server A read"