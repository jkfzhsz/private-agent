"""阶段三批次3(T3.3) - 内置工具下沉(is_kernel)测试(调研 round2 §4.3.1)。

覆盖:
- builtins 注册后 8 内核 / 4 下沉标记正确
- ToolSelector 内核工具作为隐含锚点始终注入
- 非内核工具靠 top-N 评分竞争
- Skill 白名单语义不变(registry 强过滤)
"""
import asyncio

from private_agent.tools.builtins import register_all_builtins
from private_agent.tools.defs import ToolDef
from private_agent.tools.registry import ToolRegistry
from private_agent.tools.selector import ToolSelector


async def _echo_handler(args: dict):
    from private_agent.tools.defs import ToolResult

    return ToolResult(output="ok")


def _mk_tool(name: str, is_kernel: bool = False) -> ToolDef:
    return ToolDef(
        name=name,
        description=f"{name} does things",
        parameters_schema={"type": "object", "properties": {}},
        handler=_echo_handler,
        is_kernel=is_kernel,
    )


class TestBuiltinKernelMarkers:
    def test_nine_kernel_tools(self):
        """内置 9 个内核工具 is_kernel=True(0.5.1 新增 memory_save;
        2026-08-13 新增 system_capabilities 自我认知入口)。"""
        registry = ToolRegistry()
        register_all_builtins(registry)
        kernel = {t.name for t in registry.list_tools() if t.is_kernel}
        assert kernel == {
            "calculator", "code_execution", "datetime", "file_read",
            "file_write", "http_request", "web_search", "memory_save",
            "system_capabilities",
        }

    def test_three_downgraded_tools(self):
        """search_knowledge/read_artifact/memory_search/search_lessons 下沉为
        is_kernel=False (0.5.0 M1: memory_search 为记忆按需检索工具, 非内核;
        Phase 1: search_lessons 经验检索同模式)。"""
        registry = ToolRegistry()
        register_all_builtins(registry)
        non_kernel = {t.name for t in registry.list_tools() if not t.is_kernel}
        assert non_kernel == {
            "search_knowledge", "read_artifact", "memory_search", "search_lessons",
        }


class TestSelectorKernelAnchors:
    """AC-18/19: 内核工具始终注入, 非内核靠评分。"""

    def _pool(self) -> list[ToolDef]:
        return [
            _mk_tool("file_read", is_kernel=True),
            _mk_tool("calculator", is_kernel=True),
            _mk_tool("search_knowledge", is_kernel=False),
            _mk_tool("read_artifact", is_kernel=False),
            # 中文描述含"股票行情" → 查询"股票"时高相关
            ToolDef(
                name="mcp_stock",
                description="股票行情查询工具, 提供 A股实时行情",
                parameters_schema={"type": "object", "properties": {}},
                handler=_echo_handler,
                is_kernel=False,
            ),
            _mk_tool("mcp_news", is_kernel=False),
            _mk_tool("mcp_fund", is_kernel=False),
            _mk_tool("mcp_bond", is_kernel=False),
            _mk_tool("mcp_index", is_kernel=False),
            _mk_tool("mcp_edb", is_kernel=False),
            _mk_tool("mcp_other1", is_kernel=False),
            _mk_tool("mcp_other2", is_kernel=False),
            _mk_tool("mcp_other3", is_kernel=False),
            _mk_tool("mcp_other4", is_kernel=False),
        ]

    def test_kernel_always_injected(self):
        """池 14 > min_pool 8: 内核工具(file_read/calculator)始终在结果中。"""
        sel = ToolSelector({})  # 默认 top_n=15
        pool = self._pool()
        result = sel.select(pool, "查询 A股行情")
        names = {t.name for t in result}
        assert "file_read" in names
        assert "calculator" in names

    def test_non_kernel_compete_by_relevance(self):
        """非内核工具中, 高相关(mcp_stock)入选, 低相关(mcp_other4)被裁剪。"""
        sel = ToolSelector({"tools": {"tool_selection": {"top_n": 6}}})
        pool = self._pool()
        result = sel.select(pool, "查询 A股行情 股票")
        names = {t.name for t in result}
        # 高相关工具必入选(分数明显领先)
        assert "mcp_stock" in names
        # 零相关 + 短描述 → 排最后被裁剪
        assert "mcp_other4" not in names
        # 总注入 ≤ top_n
        assert len(result) <= 6

    def test_non_kernel_not_forced_in_unrelated_query(self):
        """无关查询: 下沉工具不因锚点被强制注入(靠评分竞争)。"""
        sel = ToolSelector({"tools": {"tool_selection": {"top_n": 6}}})
        pool = self._pool()
        result = sel.select(pool, "现在几点")
        # 非内核注入数量 = 总数 - 锚点(2) ≤ remaining
        non_kernel = [t for t in result if not t.is_kernel]
        assert len(non_kernel) <= sel.top_n - 2


class TestWhitelistUnchanged:
    """AC-18 回归: Skill 白名单仍是强约束(不豁免内核工具)。"""

    def test_whitelist_filters_all(self):
        registry = ToolRegistry()
        register_all_builtins(registry)
        filtered = registry.list_tools_for_session(["file_read", "calculator"])
        names = {t.name for t in filtered}
        assert names == {"file_read", "calculator"}  # http_request 等被过滤

    def test_whitelist_none_returns_all(self):
        registry = ToolRegistry()
        register_all_builtins(registry)
        all_tools = registry.list_tools_for_session(None)
        # 0.5.1: 12 类内置(8 内核 + 4 非内核); Phase 1: 新增 search_lessons;
        # 2026-08-13: 新增 system_capabilities(内核) → 13
        assert len(all_tools) == 13
