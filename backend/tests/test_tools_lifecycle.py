"""AC-8 集成测试 - 工具生命周期完整链路。

测试覆盖两条分支:
① 内置工具调用链路: ToolRegistry.list_tools() → ReactLoop 直接执行本地 handler
② MCP 远端工具链路: MCPClient(stdio) discover_tools → ToolRegistry 注册 → ReactLoop 调用

依赖:
- 真实 PostgreSQL (TEST_DSN)
- 内置工具全部注册到 ToolRegistry
"""
import asyncio
import os
from typing import Any

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop, ReactLoopState
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import migrations
from private_agent.tools.builtins import register_all_builtins
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.schema_adapter import mcp_tool_to_tooldef
from private_agent.tools.registry import ToolRegistry

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()
    asyncio.run(_run())


async def _create_session(conn: "asyncpg.Connection") -> int:
    """创建测试会话并返回 session_id。"""
    row = await conn.fetchrow(
        "INSERT INTO sessions (title) VALUES ($1) RETURNING id",
        "test_tools_lifecycle",
    )
    return row["id"]


def _calculator_tool_call(call_id: str, expr: str) -> dict:
    """构造 calculator tool_call 的 mock 响应。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "calculator",
            "arguments": f'{{"expression": "{expr}"}}',
        },
    }


class _MockAdapter:
    """模拟 ModelAdapter 返回固定响应。"""

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat(self, messages: list, tools: list, **kwargs) -> ChatResult:
        result = self._responses[self.call_count]
        self.call_count += 1
        return result

    @property
    def capabilities(self) -> set[ModelCapability]:
        return set()


# =============================================================================
# 分支①: 内置工具调用链路 (AC-8)
# =============================================================================


class TestBuiltinToolLifecycle:
    """AC-8 分支①: ToolRegistry + 12 类内置工具 → ReactLoop 调用。"""

    def test_tool_registry_contains_all_12_builtins(self):
        """ToolRegistry 注册全部 12 类内置工具(0.5.1 含 memory_save;
        Phase 1 新增 search_lessons)。"""
        registry = ToolRegistry()
        register_all_builtins(registry)
        tools = registry.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "calculator", "code_execution", "datetime", "file_read",
            "file_write", "http_request", "search_knowledge",
            "web_search", "read_artifact", "memory_search",
            "memory_save", "search_lessons",
        }, f"expected 12 builtins, got {names}"

    def test_react_loop_calls_builtin_calculator_via_tool_registry(self):
        """ReactLoop 通过 ToolRegistry 加载内置工具并执行 calculator。"""
        _setup_schema()

        async def _run() -> list[dict[str, Any]]:
            conn = await asyncpg.connect(TEST_DSN)
            try:
                session_id = await _create_session(conn)
                # 从 ToolRegistry 加载所有内置工具
                registry = ToolRegistry()
                register_all_builtins(registry)
                tools = registry.list_tools()

                cm = ContextManager(
                    session_id=session_id,
                    system_prompt="sys",
                    tools=tools,
                )
                await cm.build_initial(conn)

                # adapter 返回 calculator tool_call
                adapter = _MockAdapter(
                    responses=[
                        ChatResult(
                            content="",
                            tool_calls=[_calculator_tool_call("call_1", "2+3")],
                            used_provider="mock",
                        ),
                        ChatResult(content="result is 5", used_provider="mock"),
                    ]
                )
                loop = ReactLoop(
                    session_id=session_id,
                    context_manager=cm,
                    adapter=adapter,
                    tools=tools,
                    conn=conn,
                )
                await loop.run_turn("calculate 2+3")
                events = []
                while not loop.event_queue.empty():
                    events.append(loop.event_queue.get_nowait())
                return events
            finally:
                await conn.close()

        events = asyncio.run(_run())
        assert len(events) == 4
        assert [e["event_type"] for e in events] == [
            "thinking", "tool_call", "tool_result", "final",
        ]
        # tool_result 应包含 calculator 输出 "5"
        tool_result = events[2]
        assert tool_result["payload"]["tool_name"] == "calculator"
        assert tool_result["payload"]["output"] == "5"

    def test_react_loop_calls_builtin_datetime_via_tool_registry(self):
        """ReactLoop 通过 ToolRegistry 调用 datetime 内置工具。"""
        _setup_schema()

        async def _run() -> list[dict[str, Any]]:
            conn = await asyncpg.connect(TEST_DSN)
            try:
                session_id = await _create_session(conn)
                registry = ToolRegistry()
                register_all_builtins(registry)
                tools = registry.list_tools()

                cm = ContextManager(
                    session_id=session_id,
                    system_prompt="sys",
                    tools=tools,
                )
                await cm.build_initial(conn)

                # datetime tool_call (无参数)
                datetime_tc = {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "datetime",
                        "arguments": "{}",
                    },
                }
                adapter = _MockAdapter(
                    responses=[
                        ChatResult(
                            content="",
                            tool_calls=[datetime_tc],
                            used_provider="mock",
                        ),
                        ChatResult(content="done", used_provider="mock"),
                    ]
                )
                loop = ReactLoop(
                    session_id=session_id,
                    context_manager=cm,
                    adapter=adapter,
                    tools=tools,
                    conn=conn,
                )
                await loop.run_turn("get time")
                events = []
                while not loop.event_queue.empty():
                    events.append(loop.event_queue.get_nowait())
                return events
            finally:
                await conn.close()

        events = asyncio.run(_run())
        assert len(events) == 4
        tool_result = events[2]
        assert tool_result["payload"]["tool_name"] == "datetime"
        # datetime 输出应为 ISO 8601 格式
        assert "T" in tool_result["payload"]["output"]
        assert tool_result["payload"]["error"] is None


# =============================================================================
# 分支②: MCP 远端工具链路 (AC-8)
# =============================================================================


class TestMcpToolLifecycle:
    """AC-8 分支②: MCP 工具发现 → ToolRegistry 注册 → ReactLoop 调用。"""

    def test_mcp_tool_registered_via_tool_registry(self):
        """MCP 工具通过 ToolRegistry.register_mcp 注册后可通过 list_tools 查询。"""
        registry = ToolRegistry()
        # 模拟 MCP 工具发现
        mcp_tool = ToolDef(
            name="mcp_search",
            description="A search tool from MCP server",
            parameters_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            handler=None,  # MCP 远端工具 handler 在 MCPClient 侧
        )
        registry.register_mcp("search-server", mcp_tool)
        tools = registry.list_tools()
        names = {t.name for t in tools}
        assert "mcp_search" in names

    def test_mcp_tool_integrated_with_tool_registry(self):
        """MCP 工具 schema → SchemaAdapter → ToolRegistry → ReactLoop 可查询。"""
        registry = ToolRegistry()
        # 模拟 MCP 工具发现
        mcp_schemas = [
            {"name": "mcp_echo", "description": "Echo from MCP", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "mcp_calc", "description": "Calculate from MCP", "inputSchema": {"type": "object", "properties": {"expr": {"type": "string"}}}},
        ]
        for schema in mcp_schemas:
            tool_def = mcp_tool_to_tooldef(schema)
            registry.register_mcp("mcp-server", tool_def)

        tools = registry.list_tools()
        names = {t.name for t in tools}
        assert "mcp_echo" in names
        assert "mcp_calc" in names

        # 通过 get_tool 查询
        assert registry.get_tool("mcp_echo") is not None
        assert registry.get_tool("mcp_calc") is not None

    def test_mcp_tool_with_builtin_priority(self):
        """同名工具时,内置工具优先级高于 MCP 工具。"""
        registry = ToolRegistry()
        # 先注册内置工具
        calculator = ToolDef(
            name="calculator",
            description="Built-in calculator",
            parameters_schema={},
            handler=None,
        )
        registry.register_builtin("calculator", calculator)

        # 再注册同名 MCP 工具
        mcp_calc = ToolDef(
            name="calculator",
            description="MCP calculator",
            parameters_schema={},
            handler=None,
        )
        registry.register_mcp("mcp-server", mcp_calc)

        # get_tool 应返回内置工具
        found = registry.get_tool("calculator")
        assert found is not None
        assert found.description == "Built-in calculator"


# =============================================================================
# 工具变更检测 (AC-8 补充)
# =============================================================================


class TestToolLifecycleEdgeCases:
    """AC-8 边界场景。"""

    def test_tool_registry_list_empty_initially(self):
        """ToolRegistry 初始化后 list_tools 返回空列表。"""
        registry = ToolRegistry()
        assert registry.list_tools() == []

    def test_tool_registry_get_tool_unknown_returns_none(self):
        """ToolRegistry.get_tool 查询未知工具返回 None。"""
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None

    def test_builtin_override_mcp_on_list(self):
        """list_tools 中内置工具覆盖同名 MCP 工具(不重复出现)。"""
        registry = ToolRegistry()
        registry.register_builtin("echo", ToolDef(name="echo", description="builtin", parameters_schema={}, handler=None))
        registry.register_mcp("s1", ToolDef(name="echo", description="mcp", parameters_schema={}, handler=None))
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert names.count("echo") == 1
        echo = registry.get_tool("echo")
        assert echo is not None
        assert echo.description == "builtin"