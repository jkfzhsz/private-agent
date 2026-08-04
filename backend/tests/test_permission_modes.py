"""阶段三批次 1 - PermissionManager 规则接入 + 权限模式测试(调研 round2 §4.2.1)。

覆盖 AC-2(deny 优先)/AC-4(模式)/AC-5(矩阵预置档)/AC-7(风险分级+来源解释)。
"""
import asyncio
import pytest

from private_agent.tools.defs import ToolDef, assess_risk
from private_agent.tools.permission import parse_rule
from private_agent.tools.permission_manager import (
    PERMISSION_MODE_DEFAULTS,
    PERMISSION_MODES,
    PermissionManager,
)


async def _echo_handler(args: dict):
    from private_agent.tools.defs import ToolResult

    return ToolResult(output="ok")


def _tool(name: str, safety_level: str = "elevated", risk_level: str = "medium") -> ToolDef:
    return ToolDef(
        name=name,
        description=f"{name} tool",
        parameters_schema={"type": "object", "properties": {}},
        handler=_echo_handler,
        safety_level=safety_level,
        risk_level=risk_level,
    )


class _Collector:
    """捕获 emit_fn 推送的事件。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)


class TestModeBasics:
    """权限模式基础行为。"""

    def test_modes_enum(self):
        assert PERMISSION_MODES == (
            "default", "plan", "acceptEdits", "cautious", "deny_all",
        )

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            PermissionManager(mode="hacker")

    def test_set_mode_clears_cache(self):
        pm = PermissionManager(timeout=0.1)
        collector = _Collector()
        tool = _tool("code_execution")

        async def run():
            # 首次确认: 走确认流程 → 超时拒绝
            outcome = await pm.check_and_confirm(1, tool, {}, collector.emit)
            assert outcome == "timeout"
            # 缓存了拒绝 → 直接 denied
            outcome2 = await pm.check_and_confirm(1, tool, {}, collector.emit)
            assert outcome2 == "denied"
            # 切模式清缓存 → 再次走确认流程
            pm.set_mode("plan")
            assert pm.mode == "plan"

        asyncio.run(run())


class TestDenyAllMode:
    """deny_all: 全部拦截。"""

    def test_deny_all_blocks_everything(self):
        pm = PermissionManager(mode="deny_all")
        collector = _Collector()

        async def run():
            for name, level in (
                ("file_read", "safe"),
                ("code_execution", "elevated"),
                ("file_write", "elevated"),
            ):
                tool = _tool(name, level)
                outcome = await pm.check_and_confirm(1, tool, {}, collector.emit)
                assert outcome == "blocked", name

        asyncio.run(run())


class TestPlanMode:
    """plan: 只读放行, 写工具每次确认(不缓存)。"""

    def test_plan_safe_auto_elevated_always_ask(self):
        pm = PermissionManager(mode="plan", timeout=0.05)
        collector = _Collector()

        async def run():
            safe_tool = _tool("file_read", "safe")
            assert (
                await pm.check_and_confirm(1, safe_tool, {}, collector.emit) == "auto"
            )
            elevated_tool = _tool("code_execution", "elevated")
            # 每次都走确认(不缓存) → 超时拒绝两次, 两次都推送事件
            assert (
                await pm.check_and_confirm(1, elevated_tool, {}, collector.emit)
                == "timeout"
            )
            assert (
                await pm.check_and_confirm(1, elevated_tool, {}, collector.emit)
                == "timeout"
            )
            assert len(collector.events) == 2

        asyncio.run(run())


class TestAcceptEditsMode:
    """acceptEdits: 文件类自动批准, 其余 elevated 走确认。"""

    def test_file_tools_auto_approved(self):
        pm = PermissionManager(mode="acceptEdits", timeout=0.05)
        collector = _Collector()

        async def run():
            file_tool = _tool("file_write", "elevated")
            assert (
                await pm.check_and_confirm(1, file_tool, {"path": "/a.txt"}, collector.emit)
                == "auto"
            )
            other_tool = _tool("code_execution", "elevated")
            assert (
                await pm.check_and_confirm(1, other_tool, {}, collector.emit) == "timeout"
            )

        asyncio.run(run())


class TestCautiousMode:
    """cautious: 确认结果不缓存(每次都询问)。"""

    def test_cautious_no_cache(self):
        pm = PermissionManager(mode="cautious", timeout=0.05)
        collector = _Collector()

        async def run():
            tool = _tool("code_execution", "elevated")
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "timeout"
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "timeout"
            assert len(collector.events) == 2  # 无缓存 → 两次都确认

        asyncio.run(run())


class TestRuleIntegration:
    """规则求值层接入: deny 优先 / allow / ask / 来源解释。"""

    def test_rule_deny_blocks(self):
        rules = [parse_rule("deny:code_execution")]
        pm = PermissionManager(rules=rules)
        collector = _Collector()

        async def run():
            tool = _tool("code_execution", "elevated")
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "blocked"

        asyncio.run(run())

    def test_rule_allow_autos(self):
        rules = [parse_rule("allow:code_execution")]
        pm = PermissionManager(rules=rules)
        collector = _Collector()

        async def run():
            tool = _tool("code_execution", "elevated")
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "auto"

        asyncio.run(run())

    def test_rule_ask_forces_confirm_no_cache(self):
        rules = [parse_rule("ask:code_execution")]
        pm = PermissionManager(rules=rules, timeout=0.05)
        collector = _Collector()

        async def run():
            tool = _tool("code_execution", "elevated")
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "timeout"
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "timeout"
            assert len(collector.events) == 2  # ask 规则 → 每次都确认

        asyncio.run(run())

    def test_rule_specifier_narrowing(self):
        """specifier 收窄: 仅特定参数 deny, 其余回退 safety_level。"""
        rules = [parse_rule("deny:file_write(//**/.env)")]
        pm = PermissionManager(rules=rules, timeout=0.05)
        collector = _Collector()

        async def run():
            tool = _tool("file_write", "elevated")
            # 写 .env → 规则 deny
            assert (
                await pm.check_and_confirm(1, tool, {"path": "//x/.env"}, collector.emit)
                == "blocked"
            )
            # 写普通文件 → 规则未命中 → 回退 elevated 确认 → 超时拒绝
            assert (
                await pm.check_and_confirm(1, tool, {"path": "//x/a.txt"}, collector.emit)
                == "timeout"
            )

        asyncio.run(run())

    def test_rules_empty_keeps_default_behavior(self):
        """rules 为空(None)时行为与阶段二一致(AC-1 零回归)。"""
        pm = PermissionManager(timeout=0.05)
        collector = _Collector()

        async def run():
            tool = _tool("code_execution", "elevated")
            assert await pm.check_and_confirm(1, tool, {}, collector.emit) == "timeout"

        asyncio.run(run())


class TestConfirmationEventEnrichment:
    """AC-7: 确认事件携带 risk_level + reason(可解释决策)。"""

    def test_event_contains_risk_and_reason(self):
        pm = PermissionManager(timeout=0.05)
        collector = _Collector()

        async def run():
            tool = _tool("file_write", "elevated")
            await pm.check_and_confirm(1, tool, {"path": "/a/.env"}, collector.emit)
            assert len(collector.events) == 1
            ev = collector.events[0]
            assert ev["risk_level"] == "high"  # .env 启发式升 high
            assert ev["mode"] == "default"
            assert "系统默认" in ev["reason"]

        asyncio.run(run())

    def test_event_reason_from_rule(self):
        rules = [parse_rule("ask:http_request", source="skill")]
        pm = PermissionManager(rules=rules, timeout=0.05)
        collector = _Collector()

        async def run():
            tool = _tool("http_request", "elevated")
            await pm.check_and_confirm(1, tool, {"url": "https://x.com"}, collector.emit)
            ev = collector.events[0]
            assert "规则 ask:http_request" in ev["reason"]
            assert "skill" in ev["reason"]

        asyncio.run(run())


class TestAssessRisk:
    """B-8 风险分级启发式(纯函数)。"""

    def test_default_medium(self):
        assert assess_risk(_tool("datetime", "safe")) == "medium"

    def test_explicit_low(self):
        assert assess_risk(_tool("datetime", "safe", risk_level="low")) == "low"

    def test_explicit_high_wins(self):
        assert (
            assess_risk(_tool("http_request", "elevated", risk_level="high"), {"url": "https://x.com"})
            == "high"
        )

    def test_env_path_hint_high(self):
        assert assess_risk(_tool("file_write"), {"path": "/a/.env"}) == "high"
        assert assess_risk(_tool("file_write"), {"path": "/a/b.txt"}) == "medium"

    def test_internal_url_hint_high(self):
        assert assess_risk(_tool("http_request"), {"url": "http://127.0.0.1:8765"}) == "high"
        assert assess_risk(_tool("http_request"), {"url": "https://api.public.com"}) == "medium"

    def test_code_hint_high(self):
        assert assess_risk(_tool("code_execution"), {"code": "subprocess rm -rf /"}) == "high"
        assert assess_risk(_tool("code_execution"), {"code": "x = 1"}) == "medium"
