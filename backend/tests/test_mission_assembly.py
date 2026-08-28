"""0.6.0 F1-6: 工具白名单过滤 + MissionRegistry 并发注册表单测。

验收(设计文档 C 阶段②): search 子代理调用 file_write → 工具不存在;
并发第 3 个 mission → 拒绝(注册表 acquire 超时)。
"""
import asyncio

import pytest

from private_agent.core.mission_assembly import (
    MISSION_EXCLUDED_ALL,
    MISSION_MCP_ALLOWED_PREFIXES,
    MissionRegistry,
    filter_tools_for_task_type,
)
from private_agent.tools.defs import ToolDef


def _tool(name: str) -> ToolDef:
    return ToolDef(name=name, description=name, parameters_schema={}, handler=None)


FULL_TOOLS = [
    _tool(n)
    for n in [
        "web_search", "http_request", "code_execution", "file_read", "file_write",
        "calculator", "datetime", "read_artifact", "pytest_run", "memory_search",
        "delegate_subtask", "mission_create", "mission_status", "mission_control",
        "system_metrics_query", "subagent_status", "session_events",
        "mcp__Searchpin__web_search", "mcp__hexin-ifind-ds-stock-mcp__get_stock_info",
        "mcp__qcc-company__search", "mcp__mempalace__mempalace_search",
    ]
]


# ── 白名单过滤 ──────────────────────────────────────────────────────────

def test_search_subagent_cannot_write_files():
    """V 验收③: search 子代理无 file_write(装配层剔除)。"""
    names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, "search")}
    assert "file_write" not in names
    assert "code_execution" not in names


def test_analysis_subagent_cannot_search_web():
    """analysis 子代理无 web_search/http_request(防漂移到网上乱搜)。"""
    names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, "analysis")}
    assert "web_search" not in names
    assert "http_request" not in names
    assert "code_execution" in names       # analysis 核心工具保留
    assert "mcp__hexin-ifind-ds-stock-mcp__get_stock_info" in names  # 数据 MCP 保留


def test_code_subagent_cannot_access_web():
    names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, "code")}
    assert "web_search" not in names
    assert "http_request" not in names
    assert "file_write" in names           # code 类允许写(限 workspace 由沙箱保证)


def test_orchestration_tools_excluded_for_all_types():
    """编排/监控工具全类型禁入(嵌套深度恒 1 的装配层保障)。"""
    for typ in ("search", "analysis", "code", "other"):
        names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, typ)}
        assert not (names & MISSION_EXCLUDED_ALL), f"{typ}: {names & MISSION_EXCLUDED_ALL}"


def test_mcp_allowlist_conservative():
    """MCP 保守允许: search→Searchpin, analysis→hexin/qcc, code→无, 未知→禁。"""
    search_names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, "search")}
    assert "mcp__Searchpin__web_search" in search_names
    assert "mcp__hexin-ifind-ds-stock-mcp__get_stock_info" not in search_names
    assert "mcp__mempalace__mempalace_search" not in search_names
    code_names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, "code")}
    assert not any(n.startswith("mcp__") for n in code_names)
    # 前缀清单契约(D 批接线依赖)
    assert MISSION_MCP_ALLOWED_PREFIXES["code"] == ()


def test_other_type_only_excludes_forbidden_all():
    """other 类型: 只排除全类型禁区, 保留基础工具面。"""
    names = {t.name for t in filter_tools_for_task_type(FULL_TOOLS, "other")}
    assert "file_read" in names and "web_search" in names
    assert "delegate_subtask" not in names


def test_filter_returns_new_list_input_untouched():
    """过滤返回新列表, 不动入参(调用方可复用原工具集)。"""
    original = list(FULL_TOOLS)
    filter_tools_for_task_type(FULL_TOOLS, "search")
    assert len(FULL_TOOLS) == len(original) == 21


# ── MissionRegistry ─────────────────────────────────────────────────────

def test_registry_acquire_release_cycle():
    async def _run() -> tuple[bool, int, int, int]:
        reg = MissionRegistry(max_running=2)
        ok1 = await reg.acquire(timeout_sec=1)
        ok2 = await reg.acquire(timeout_sec=1)
        count_full = reg.running()
        reg.release()
        reg.release()
        reg.release()  # 幂等: 不为负
        return ok1, ok2, count_full, reg.running()
    ok1, ok2, count_full, count_after = asyncio.run(_run())
    assert ok1 and ok2
    assert count_full == 2
    assert count_after == 0


def test_registry_acquire_times_out_when_full():
    """并发满 → acquire 等待超时返回 False(V 验收: 第 3 个 mission 拒绝)。"""
    async def _run() -> bool:
        reg = MissionRegistry(max_running=2)
        await reg.acquire(timeout_sec=0.1)
        await reg.acquire(timeout_sec=0.1)
        return await reg.acquire(timeout_sec=0.2)  # 应超时
    assert asyncio.run(_run()) is False


def test_registry_release_wakes_waiter():
    """release 后等待者获名额(轮询语义: 不取消、不丢失; done_callback 兼容)。"""
    async def _run() -> bool:
        reg = MissionRegistry(max_running=1)
        await reg.acquire(timeout_sec=0.1)

        async def _releaser():
            await asyncio.sleep(0.05)
            reg.release()  # 同步(与 task done_callback 同一路径)

        task = asyncio.create_task(_releaser())
        got = await reg.acquire(timeout_sec=2)
        await task
        return got
    assert asyncio.run(_run()) is True


def test_registry_set_max():
    reg = MissionRegistry(max_running=1)
    asyncio.run(reg.acquire(timeout_sec=0.1))
    reg.set_max(3)
    assert asyncio.run(reg.acquire(timeout_sec=0.1)) is True
