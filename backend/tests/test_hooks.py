"""阶段三批次 2(B-1, 调研 round2 §4.2.2) - Hooks 系统测试。

覆盖 AC-9(默认空零回归)/AC-10(deny 阻断)/AC-12(additionalContext)/
AC-13(三类实现)/AC-14(失败放行 + 超时)。
"""
import asyncio
import json
import sys

import pytest

from private_agent.core.hooks import (
    EXIT_BLOCK,
    HOOK_EVENTS,
    HOOK_TYPES,
    HookConfig,
    HookDecision,
    HookRunner,
)


class TestHookConfig:
    """配置解析与校验。"""

    def test_valid_events_and_types(self):
        assert len(HOOK_EVENTS) == 6
        assert HOOK_TYPES == ("command", "http", "mcp_tool")

    def test_invalid_event_rejected(self):
        with pytest.raises(ValueError):
            HookConfig(name="h1", event="bogus_event")

    def test_invalid_type_rejected(self):
        with pytest.raises(ValueError):
            HookConfig(name="h1", event="pre_tool_use", type="shell")

    def test_command_requires_command(self):
        with pytest.raises(ValueError):
            HookConfig(name="h1", event="pre_tool_use", type="command", command=None)

    def test_http_requires_url(self):
        with pytest.raises(ValueError):
            HookConfig(name="h1", event="pre_tool_use", type="http", url=None)

    def test_mcp_requires_server_and_tool(self):
        with pytest.raises(ValueError):
            HookConfig(
                name="h1", event="pre_tool_use", type="mcp_tool",
                mcp_server="s", mcp_tool=None,
            )

    def test_config_from_dict(self):
        h = HookRunner.config_from_dict(
            {"name": "h1", "event": "pre_tool_use", "type": "command", "command": "echo x"}
        )
        assert h.name == "h1"
        assert h.event == "pre_tool_use"
        assert h.timeout == 5.0
        assert h.enabled is True

    def test_configs_from_list_empty(self):
        assert HookRunner.configs_from_list(None) == []
        assert HookRunner.configs_from_list([]) == []

    def test_configs_from_list_parses(self):
        hooks = HookRunner.configs_from_list(
            [{"name": "h1", "event": "stop", "type": "command", "command": "echo x"}]
        )
        assert len(hooks) == 1
        assert hooks[0].event == "stop"


class TestDispatchEmpty:
    """AC-9: 默认空列表 → 空决策(行为不变)。"""

    def test_dispatch_no_hooks_returns_empty_decision(self):
        runner = HookRunner()
        decision = asyncio.run(runner.dispatch("pre_tool_use", {"tool_name": "x"}))
        assert decision.permission_decision is None
        assert decision.updated_input is None
        assert decision.additional_context is None
        assert decision.stop is False
        assert decision.results == []

    def test_disabled_hook_skipped(self):
        hook = HookConfig(
            name="h1", event="pre_tool_use", type="command",
            command=f"{sys.executable} -c \"import sys,json;print(json.dumps({{'permissionDecision':'deny'}}))\"",
            enabled=False,
        )
        runner = HookRunner(hooks=[hook])
        decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
        assert decision.permission_decision is None
        assert decision.results == []


class TestCommandHook:
    """command hook: 子进程 stdin/stdout JSON 协议(脚本文件方式, 规避引号/路径问题)。"""

    def _cmd_hook(self, code: str, **kw):
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkstemp(suffix=".py")[1])
        tmp.write_text(code, encoding="utf-8")
        hook = HookConfig(
            name="cmd1",
            event="pre_tool_use",
            type="command",
            command=f"{sys.executable} {tmp}",
            **kw,
        )
        # 记录脚本路径供清理
        hook._tmp_script = tmp  # type: ignore[attr-defined]
        return hook

    def _cleanup(self, hooks):
        for h in hooks:
            tmp = getattr(h, "_tmp_script", None)
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def test_success_returns_decision(self):
        code = "import sys,json;print(json.dumps({'permissionDecision':'allow'}))"
        hooks = [self._cmd_hook(code)]
        try:
            runner = HookRunner(hooks=hooks)
            decision = asyncio.run(
                runner.dispatch("pre_tool_use", {"tool_name": "code_execution"})
            )
            assert decision.permission_decision == "allow"
            assert decision.results[0]["exit_code"] == 0
        finally:
            self._cleanup(hooks)

    def test_deny_blocks(self):
        code = "import sys,json;print(json.dumps({'permissionDecision':'deny'}))"
        hooks = [self._cmd_hook(code)]
        try:
            runner = HookRunner(hooks=hooks)
            decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
            assert decision.permission_decision == "deny"
        finally:
            self._cleanup(hooks)

    def test_additional_context_returned(self):
        code = "import sys,json;print(json.dumps({'additionalContext':'extra info'}))"
        hooks = [self._cmd_hook(code)]
        try:
            runner = HookRunner(hooks=hooks)
            decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
            assert decision.additional_context == "extra info"
        finally:
            self._cleanup(hooks)

    def test_exit_2_maps_to_deny(self):
        code = "import sys;print('blocked', file=sys.stderr);sys.exit(2)"
        hooks = [self._cmd_hook(code)]
        try:
            runner = HookRunner(hooks=hooks)
            decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
            assert decision.permission_decision == "deny"
            assert "blocked" in decision.results[0].get("error", "")
        finally:
            self._cleanup(hooks)

    def test_nonzero_exit_passes_through(self):
        code = "import sys;sys.exit(1)"
        hooks = [self._cmd_hook(code)]
        try:
            runner = HookRunner(hooks=hooks)
            decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
            assert decision.permission_decision is None  # 失败放行
            assert decision.results[0]["exit_code"] == 1
        finally:
            self._cleanup(hooks)

    def test_timeout_passes_through(self):
        """AC-14: 超时(0.1s) → 放行, 不阻塞。"""
        code = "import time;time.sleep(5)"
        hooks = [self._cmd_hook(code, timeout=0.1)]
        try:
            runner = HookRunner(hooks=hooks)
            decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
            assert decision.permission_decision is None
            assert decision.results[0]["error"] == "timeout"
        finally:
            self._cleanup(hooks)


class TestHttpHook:
    """http hook: POST 回调(注入 mock)。"""

    async def _fake_post(self, url, input_json, timeout):
        return {
            "name": "http1",
            "status": 200,
            "permissionDecision": "deny",
        }

    def test_http_decision(self):
        hook = HookConfig(
            name="http1", event="pre_tool_use", type="http", url="https://policy.internal/check"
        )
        runner = HookRunner(hooks=[hook], http_post=self._fake_post)
        decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
        assert decision.permission_decision == "deny"

    def test_http_failure_passes_through(self):
        async def _fail(url, input_json, timeout):
            raise RuntimeError("network down")

        hook = HookConfig(
            name="http1", event="pre_tool_use", type="http", url="https://policy.internal/check"
        )
        runner = HookRunner(hooks=[hook], http_post=_fail)
        decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
        assert decision.permission_decision is None  # 失败放行
        assert "network down" in decision.results[0]["error"]


class TestMcpHook:
    """mcp_tool hook: MCP 工具调用(注入 mock)。"""

    async def _fake_mcp(self, server, tool, payload):
        assert server == "policy-server"
        assert tool == "approve"
        return {"permissionDecision": "ask"}

    def test_mcp_decision(self):
        hook = HookConfig(
            name="mcp1", event="pre_tool_use", type="mcp_tool",
            mcp_server="policy-server", mcp_tool="approve",
        )
        runner = HookRunner(hooks=[hook], mcp_call=self._fake_mcp)
        decision = asyncio.run(runner.dispatch("pre_tool_use", {"tool_name": "x"}))
        assert decision.permission_decision == "ask"

    def test_mcp_without_callback_passes(self):
        hook = HookConfig(
            name="mcp1", event="pre_tool_use", type="mcp_tool",
            mcp_server="s", mcp_tool="t",
        )
        runner = HookRunner(hooks=[hook])  # 无 mcp_call 注入
        decision = asyncio.run(runner.dispatch("pre_tool_use", {}))
        assert decision.permission_decision is None
        assert "not injected" in decision.results[0]["error"]


class TestDecisionMerge:
    """决策合并: deny 优先。"""

    def test_deny_beats_allow(self):
        d1 = HookDecision(permission_decision="allow")
        d2 = HookDecision(permission_decision="deny")
        d1.merge(d2)
        assert d1.permission_decision == "deny"

    def test_ask_beats_allow(self):
        d1 = HookDecision(permission_decision="allow")
        d2 = HookDecision(permission_decision="ask")
        d1.merge(d2)
        assert d1.permission_decision == "ask"

    def test_allow_keeps_first(self):
        d1 = HookDecision(permission_decision="allow")
        d2 = HookDecision(permission_decision="allow")
        d1.merge(d2)
        assert d1.permission_decision == "allow"

    def test_updated_input_overwrites(self):
        d1 = HookDecision(updated_input={"path": "/a"})
        d2 = HookDecision(updated_input={"path": "/b"})
        d1.merge(d2)
        assert d1.updated_input == {"path": "/b"}

    def test_stop_or(self):
        d1 = HookDecision(stop=False)
        d2 = HookDecision(stop=True)
        d1.merge(d2)
        assert d1.stop is True
