"""mcp_browse 工具单测(2026-08-27, skill_binding 语义重构 §2.2-B 验收)。

覆盖四动作:
- list: 返回全部 enabled server 工具索引(渐进披露第一级) —— monkeypatch
  get_mcp_manager.get_all_server_summary, 不连真实 MCP server
- exec: 按 server_id + tool 中转执行(工具不进上下文) —— monkeypatch exec_tool
- assemble/remove: 返回 __mcp_assembly 标记, 由 react_loop 识别后 elevated
  确认并入 —— 纯逻辑, 直接断言标记 JSON
- action 非法 → 明确错误

全部为无 DB 依赖的纯单测(_wrap_merged_mcp_cfg 被 monkeypatch)。
"""
import asyncio
import json

from private_agent.tools.builtins import mcp_config_manager as mcm
from private_agent.tools.defs import ToolResult


async def _fake_mcp_cfg() -> dict:
    """monkeypatch _wrap_merged_mcp_cfg: 返回最小 tools.mcp(不连 DB)。"""
    return {"servers": []}


# ── assemble / remove: 装配标记(纯逻辑) ──────────────────────────────────


def test_assemble_returns_assembly_marker():
    """assemble 返回 __mcp_assembly 标记(action=assemble + server_ids)。"""
    res = asyncio.run(
        mcm._mcp_browse_assemble_handler(
            {"server_ids": ["codegraph", "Searchpin"]}
        )
    )
    assert not res.error
    payload = json.loads(res.output)
    assert mcm._ASSEMBLY_MARKER in payload
    marker = payload[mcm._ASSEMBLY_MARKER]
    assert marker["action"] == "assemble"
    assert marker["server_ids"] == ["codegraph", "Searchpin"]


def test_assemble_accepts_comma_separated_string():
    """assemble 的 server_ids 支持逗号分隔字符串。"""
    res = asyncio.run(
        mcm._mcp_browse_assemble_handler({"server_ids": "codegraph, Searchpin"})
    )
    payload = json.loads(res.output)
    assert payload[mcm._ASSEMBLY_MARKER]["server_ids"] == ["codegraph", "Searchpin"]


def test_assemble_requires_server_ids():
    """缺 server_ids → 明确错误(不产生装配标记)。"""
    res = asyncio.run(mcm._mcp_browse_assemble_handler({}))
    assert res.error and "server_ids" in res.error


def test_remove_returns_removal_marker():
    """remove 返回 __mcp_assembly 标记(action=remove)。"""
    res = asyncio.run(
        mcm._mcp_browse_remove_handler({"server_ids": ["codegraph"]})
    )
    assert not res.error
    payload = json.loads(res.output)
    assert payload[mcm._ASSEMBLY_MARKER]["action"] == "remove"
    assert payload[mcm._ASSEMBLY_MARKER]["server_ids"] == ["codegraph"]


def test_remove_requires_server_ids():
    """remove 缺 server_ids → 明确错误。"""
    res = asyncio.run(mcm._mcp_browse_remove_handler({}))
    assert res.error and "server_ids" in res.error


# ── list: 全部 enabled server 工具索引 ────────────────────────────────────


def test_list_returns_tool_index(monkeypatch):
    """list 返回全部 enabled server 的工具索引(名称级, 不全 schema)。"""

    class _FakeMgr:
        async def get_all_server_summary(self, cfg):
            return [
                {"id": "codegraph", "type": "stdio", "tools": ["codegraph_explore"]},
                {
                    "id": "Searchpin", "type": "stdio",
                    "tools": ["web_search", "web_fetch"],
                },
                {"id": "never-connected", "type": "stdio", "tools": []},
            ]

    monkeypatch.setattr(
        "private_agent.tools.mcp_tools.get_mcp_manager", lambda: _FakeMgr()
    )
    monkeypatch.setattr(mcm, "_wrap_merged_mcp_cfg", _fake_mcp_cfg)

    res = asyncio.run(mcm._mcp_browse_handler({"action": "list"}))
    assert not res.error
    out = res.output
    # 已连接 server: 列出工具名
    assert "codegraph" in out and "codegraph_explore" in out
    assert "Searchpin" in out and "web_search" in out
    # 未连接 server: 提示可用 exec/assemble
    assert "never-connected" in out
    assert "exec" in out and "assemble" in out


def test_list_empty_summary(monkeypatch):
    """无 enabled server → 明确空提示。"""

    class _FakeMgr:
        async def get_all_server_summary(self, cfg):
            return []

    monkeypatch.setattr(
        "private_agent.tools.mcp_tools.get_mcp_manager", lambda: _FakeMgr()
    )
    monkeypatch.setattr(mcm, "_wrap_merged_mcp_cfg", _fake_mcp_cfg)

    res = asyncio.run(mcm._mcp_browse_handler({"action": "list"}))
    assert not res.error
    assert "无已启用" in res.output


# ── exec: 中转执行(工具不进上下文) ────────────────────────────────────────


def test_exec_passes_through_to_manager(monkeypatch):
    """exec 透传 server_id/tool/args 到 exec_tool, 返回其结果。"""

    class _FakeMgr:
        async def exec_tool(self, cfg, sid, tool, args):
            assert sid == "Searchpin"
            assert tool == "web_search"
            assert args == {"q": "金融"}
            return ToolResult(output="exec ok: 2 results")

    monkeypatch.setattr(
        "private_agent.tools.mcp_tools.get_mcp_manager", lambda: _FakeMgr()
    )
    monkeypatch.setattr(mcm, "_wrap_merged_mcp_cfg", _fake_mcp_cfg)

    res = asyncio.run(
        mcm._mcp_browse_handler(
            {
                "action": "exec",
                "server_id": "Searchpin",
                "tool": "web_search",
                "args": {"q": "金融"},
            }
        )
    )
    assert res.output == "exec ok: 2 results"


def test_exec_requires_server_id_and_tool(monkeypatch):
    """exec 缺 server_id 或 tool → 明确错误(不触发 manager 调用)。"""

    class _FakeMgr:
        async def exec_tool(self, cfg, sid, tool, args):  # pragma: no cover
            raise AssertionError("不应调用 exec_tool")

    monkeypatch.setattr(
        "private_agent.tools.mcp_tools.get_mcp_manager", lambda: _FakeMgr()
    )
    monkeypatch.setattr(mcm, "_wrap_merged_mcp_cfg", _fake_mcp_cfg)

    res = asyncio.run(mcm._mcp_browse_handler({"action": "exec", "server_id": "x"}))
    assert res.error and "server_id + tool" in res.error


# ── 总入口: action 分发 ───────────────────────────────────────────────────


def test_browse_default_action_is_list(monkeypatch):
    """action 缺省 → list(渐进披露入口默认只读)。"""

    class _FakeMgr:
        async def get_all_server_summary(self, cfg):
            return []

    monkeypatch.setattr(
        "private_agent.tools.mcp_tools.get_mcp_manager", lambda: _FakeMgr()
    )
    monkeypatch.setattr(mcm, "_wrap_merged_mcp_cfg", _fake_mcp_cfg)

    res = asyncio.run(mcm._mcp_browse_handler({}))
    assert not res.error


def test_browse_invalid_action_returns_error():
    """非法 action → 明确错误并列出可选动作。"""
    res = asyncio.run(mcm._mcp_browse_handler({"action": "delete"}))
    assert res.error
    assert "list/exec/assemble/remove" in res.error
