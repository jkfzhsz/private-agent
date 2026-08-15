"""权限确认卡片人性化描述测试(2026-08-15 蒋先生反馈: 英文+术语看不懂)。

覆盖:
- humanize_confirmation: 内置工具标题/要点提取、MCP server 中文名映射、
  联网标识、长值截断、空 args 兜底;
- PermissionManager 确认事件: 含 display 字段、message 中文化、
  reason 通俗化(不含 elevated/plan/cautious 术语)。
"""
from __future__ import annotations

import asyncio

from private_agent.tools.confirmation_display import humanize_confirmation
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.permission_manager import PermissionManager


# ── humanize_confirmation ────────────────────────────────────────────────


class TestHumanizeBuiltin:
    def test_code_execution_no_network(self) -> None:
        d = humanize_confirmation(
            "code_execution", {"code": "# 注释\nprint('hello')", "timeout": 60}
        )
        assert d["title"] == "运行一段 Python 代码(不联网)"
        assert d["tool_label"] == "运行代码"
        joined = "\n".join(d["summary"])
        assert "否" in joined  # 不联网
        assert "print" in joined  # 首行有效代码
        assert "60" in joined  # timeout

    def test_code_execution_network(self) -> None:
        d = humanize_confirmation("code_execution", {"code": "import urllib", "network": True})
        assert "(需要联网)" in d["title"]
        assert any("联网: 是" in s for s in d["summary"])

    def test_file_write_target(self) -> None:
        d = humanize_confirmation(
            "file_write", {"path": "D:\\docs\\报告.md", "content": "x" * 500}
        )
        joined = "\n".join(d["summary"])
        assert "D:\\docs\\报告.md" in joined
        assert "500" in joined  # 写入字数提示

    def test_unknown_builtin_falls_back_to_name(self) -> None:
        d = humanize_confirmation("some_new_tool", {"x": 1})
        assert d["title"] == "some_new_tool"
        assert d["tool_label"] == "some_new_tool"


class TestHumanizeMcp:
    def test_registered_server_label(self) -> None:
        d = humanize_confirmation("mcp__ifind__get_stock_quote", {"stock_code": "600519"})
        assert "同花顺iFinD" in d["title"]
        assert "get_stock_quote" in d["title"]
        assert any("600519" in s for s in d["summary"])

    def test_unregistered_server_keeps_original(self) -> None:
        d = humanize_confirmation("mcp__unknown__do_thing", {"query": "测试"})
        assert "unknown" in d["title"]
        assert "do_thing" in d["title"]

    def test_mempalace_label(self) -> None:
        d = humanize_confirmation("mcp__mempalace__mempalace_search", {"query": "信贷"})
        assert "记忆宫殿" in d["title"]


class TestHumanizeEdge:
    def test_empty_args(self) -> None:
        d = humanize_confirmation("code_execution", {})
        assert d["title"]  # 不抛异常, 有标题
        assert isinstance(d["summary"], list)

    def test_none_args(self) -> None:
        d = humanize_confirmation("code_execution", None)
        assert d["title"] == "运行一段 Python 代码(不联网)"

    def test_long_value_clipped(self) -> None:
        d = humanize_confirmation("mcp__qcc__search", {"query": "长" * 200})
        line = next(s for s in d["summary"] if "查询内容" in s)
        assert "…" in line and len(line) < 100

    def test_internal_args_skipped(self) -> None:
        d = humanize_confirmation("code_execution", {"code": "x", "_on_output": "cb"})
        assert not any("_on_output" in s for s in d["summary"])


# ── PermissionManager 事件字段 ────────────────────────────────────────────


def _emit_collector(emitted: list):
    async def _emit(ev: dict) -> None:
        emitted.append(ev)
    return _emit


def _tool(name: str, level: str = "elevated") -> ToolDef:
    async def _handler(args: dict) -> ToolResult:
        return ToolResult(output="ok")
    return ToolDef(
        name=name,
        description="test",
        parameters_schema={"type": "object", "properties": {}},
        handler=_handler,
        safety_level=level,
    )


class TestConfirmationEventDisplay:
    def _run_confirm(self, pm: PermissionManager, tool: ToolDef, args: dict) -> list[dict]:
        emitted: list[dict] = []

        async def _run() -> str:
            return await pm.check_and_confirm(
                session_id=1, tool_def=tool, args=args,
                emit_fn=_emit_collector(emitted),
            )

        async def _resolve() -> None:
            for _ in range(200):
                if emitted:
                    break
                await asyncio.sleep(0.01)
            pm.resolve(emitted[0]["confirmation_id"], True)

        async def _main() -> str:
            t = asyncio.create_task(_run())
            await asyncio.sleep(0)
            await _resolve()
            return await t

        outcome = asyncio.run(_main())
        assert outcome == "approved"
        return emitted

    def test_event_contains_display_and_chinese_message(self) -> None:
        pm = PermissionManager(timeout=2.0)
        emitted = self._run_confirm(
            pm, _tool("code_execution"),
            {"code": "print(1)"},
        )
        ev = emitted[0]
        assert ev["event_type"] == "tool_confirmation_required"
        # display 字段存在且结构正确
        assert ev["display"]["tool_label"] == "运行代码"
        assert ev["display"]["title"]
        assert isinstance(ev["display"]["summary"], list)
        # message 中文化
        assert ev["message"].startswith("AI 请求:")
        assert "Allow tool" not in ev["message"]
        # 原字段保留(兼容)
        assert ev["tool_name"] == "code_execution"
        assert "args_summary" in ev

    def test_reason_no_jargon(self) -> None:
        pm = PermissionManager(timeout=2.0)
        emitted = self._run_confirm(pm, _tool("code_execution"), {"code": "x"})
        reason = emitted[0]["reason"]
        assert "elevated" not in reason
        assert reason  # 非空中文说明
