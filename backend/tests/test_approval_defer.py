"""阶段三批次4(B-14, 调研 round2 §4.2.4) - 审批挂起(defer)测试。

覆盖 AC-21/22/23:
- 60s 超时无 defer → timeout(fail-closed 不变)
- defer 后 60s 过期不立即拒绝, 继续等待 → resolve 生效
- defer 后仍超时(defer_timeout 到期) → timeout
- defer 未知 confirmation_id → False
"""
import asyncio

from private_agent.tools.defs import ToolDef
from private_agent.tools.permission_manager import PermissionManager


async def _echo_handler(args: dict):
    from private_agent.tools.defs import ToolResult

    return ToolResult(output="ok")


def _tool(name: str = "code_execution") -> ToolDef:
    return ToolDef(
        name=name,
        description=f"{name} tool",
        parameters_schema={"type": "object", "properties": {}},
        handler=_echo_handler,
        safety_level="elevated",
    )


class _Collector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)


class TestDefer:
    def test_timeout_without_defer_denies(self):
        """AC-23: 无 defer → 60s(此处 0.05s)超时拒绝(fail-closed 不变)。"""
        pm = PermissionManager(timeout=0.05)
        collector = _Collector()
        outcome = asyncio.run(
            pm.check_and_confirm(1, _tool(), {}, collector.emit)
        )
        assert outcome == "timeout"

    def test_defer_extends_wait_then_resolve(self):
        """AC-21/22: defer 后超时过期不拒绝, resolve(approved) → approved。"""
        pm = PermissionManager(timeout=0.05, defer_timeout=5.0)
        collector = _Collector()

        async def run():
            task = asyncio.create_task(
                pm.check_and_confirm(1, _tool(), {}, collector.emit)
            )
            # 等确认事件推送, 拿到 confirmation_id
            for _ in range(100):
                if collector.events:
                    break
                await asyncio.sleep(0.01)
            confirmation_id = collector.events[0]["confirmation_id"]
            # 用户 defer
            assert pm.defer(confirmation_id) is True
            # 等 60s 超时(0.05s)过期 → 应继续等待而非拒绝
            await asyncio.sleep(0.15)
            assert not task.done(), "defer 后不应在 60s 超时点拒绝"
            # 用户稍后同意 → 唤醒
            assert pm.resolve(confirmation_id, True) is True
            outcome = await task
            assert outcome == "approved"

        asyncio.run(run())

    def test_defer_then_deny(self):
        """defer 后用户拒绝 → denied。"""
        pm = PermissionManager(timeout=0.05, defer_timeout=5.0)
        collector = _Collector()

        async def run():
            task = asyncio.create_task(
                pm.check_and_confirm(1, _tool(), {}, collector.emit)
            )
            for _ in range(100):
                if collector.events:
                    break
                await asyncio.sleep(0.01)
            confirmation_id = collector.events[0]["confirmation_id"]
            assert pm.defer(confirmation_id) is True
            await asyncio.sleep(0.15)
            assert not task.done()
            pm.resolve(confirmation_id, False)
            assert await task == "denied"

        asyncio.run(run())

    def test_defer_still_times_out_eventually(self):
        """defer 后 defer_timeout(0.1s)到期 → timeout。"""
        pm = PermissionManager(timeout=0.05, defer_timeout=0.1)
        collector = _Collector()

        async def run():
            task = asyncio.create_task(
                pm.check_and_confirm(1, _tool(), {}, collector.emit)
            )
            for _ in range(100):
                if collector.events:
                    break
                await asyncio.sleep(0.01)
            confirmation_id = collector.events[0]["confirmation_id"]
            assert pm.defer(confirmation_id) is True
            outcome = await asyncio.wait_for(task, timeout=1.0)
            assert outcome == "timeout"

        asyncio.run(run())

    def test_defer_unknown_id_returns_false(self):
        pm = PermissionManager()
        assert pm.defer("no-such-id") is False

    def test_defer_after_resolve_returns_false(self):
        pm = PermissionManager(timeout=0.05, defer_timeout=5.0)
        collector = _Collector()

        async def run():
            task = asyncio.create_task(
                pm.check_and_confirm(1, _tool(), {}, collector.emit)
            )
            for _ in range(100):
                if collector.events:
                    break
                await asyncio.sleep(0.01)
            confirmation_id = collector.events[0]["confirmation_id"]
            pm.resolve(confirmation_id, True)
            await task
            # 已结束的确认不可再 defer
            assert pm.defer(confirmation_id) is False

        asyncio.run(run())
