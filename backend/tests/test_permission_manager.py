"""V2 P1 - PermissionManager 权限确认运行时链路(蓝图 §5.12)。

验证三级权限:safe 自动 / elevated 确认(缓存+超时) / dangerous 拦截。
- safe/none 自动放行,不触发确认
- elevated 首次 → emit tool_confirmation_required + 等待 resolve;同会话同参数缓存命中后自动放行
- 拒绝 → False;60s 超时 → False(测试用短超时)
- dangerous → 直接拦截
"""
from __future__ import annotations

import asyncio

import pytest

from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.permission_manager import PermissionManager

def async_emit(emitted: list) -> "callable":
    """构造异步 emit_fn(await 兼容): 记录事件。"""
    async def _emit(ev: dict) -> None:
        emitted.append(ev)
    return _emit



def _tool(name: str, level: str) -> ToolDef:
    async def _handler(args: dict) -> ToolResult:
        return ToolResult(output=f"ran:{name}")

    return ToolDef(
        name=name,
        description=f"test {name}",
        parameters_schema={"type": "object", "properties": {}},
        handler=_handler,
        safety_level=level,
    )


class TestPermissionLevels:
    def test_safe_auto_approves(self) -> None:
        """safety_level=safe 自动放行,不触发确认。"""
        pm = PermissionManager(timeout=0.5)
        emitted: list[dict] = []

        async def _run() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("safe_tool", "safe"),
                args={},
                emit_fn=async_emit(emitted),
            )

        assert asyncio.run(_run()) == "auto"
        assert emitted == []

    def test_none_default_auto_approves(self) -> None:
        """默认 safety_level=none 自动放行(不打断现有工具)。"""
        pm = PermissionManager(timeout=0.5)
        emitted: list[dict] = []

        async def _run() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("plain_tool", "none"),
                args={},
                emit_fn=async_emit(emitted),
            )

        assert asyncio.run(_run()) == "auto"
        assert emitted == []

    def test_dangerous_blocked(self) -> None:
        """dangerous 直接拦截,不入队。"""
        pm = PermissionManager(timeout=0.5)
        emitted: list[dict] = []

        async def _run() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("evil_tool", "dangerous"),
                args={},
                emit_fn=async_emit(emitted),
            )

        assert asyncio.run(_run()) == "blocked"
        assert emitted == []


class TestElevatedFlow:
    def test_elevated_first_time_emits_and_waits(self) -> None:
        """elevated 首次:emit tool_confirmation_required + 等待 resolve 后放行。"""
        pm = PermissionManager(timeout=2.0)
        emitted: list[dict] = []
        resolved: list[tuple[str, bool]] = []

        async def _run() -> bool:
            ok = await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("code_execution", "elevated"),
                args={"code": "print(1)"},
                emit_fn=async_emit(emitted),
            )
            return ok

        async def _resolve() -> None:
            # 等 emit 发生后再 resolve(模拟 WS 收到用户确认)
            for _ in range(100):
                if emitted:
                    break
                await asyncio.sleep(0.01)
            assert emitted, "must emit confirmation_required"
            ev = emitted[0]
            resolved.append((ev["confirmation_id"], True))
            pm.resolve(ev["confirmation_id"], True)

        async def _main() -> bool:
            task = asyncio.create_task(_run())
            await asyncio.sleep(0)  # 让 _run 跑到 emit
            await _resolve()
            return await task

        assert asyncio.run(_main()) == "approved"
        assert len(emitted) == 1
        assert emitted[0]["event_type"] == "tool_confirmation_required"
        assert emitted[0]["tool_name"] == "code_execution"
        assert "confirmation_id" in emitted[0]

    def test_elevated_cache_hit_second_time(self) -> None:
        """同会话同工具同参数:缓存命中,二次直接放行不 emit。"""
        pm = PermissionManager(timeout=2.0)
        emitted: list[dict] = []
        tool = _tool("code_execution", "elevated")
        args = {"code": "print(1)"}

        async def _first() -> bool:
            ok = await pm.check_and_confirm(
                session_id=1, tool_def=tool, args=args,
                emit_fn=async_emit(emitted),
            )
            return ok

        async def _resolve() -> None:
            for _ in range(100):
                if emitted:
                    break
                await asyncio.sleep(0.01)
            pm.resolve(emitted[0]["confirmation_id"], True)

        async def _main() -> None:
            t1 = asyncio.create_task(_first())
            await asyncio.sleep(0)
            await _resolve()
            assert await t1 == "approved"
            # 第二次: 缓存命中, 不 emit
            ok2 = await pm.check_and_confirm(
                session_id=1, tool_def=tool, args=args,
                emit_fn=async_emit(emitted),
            )
            assert ok2 == "approved"

        asyncio.run(_main())
        assert len(emitted) == 1  # 只 emit 一次

    def test_elevated_rejected(self) -> None:
        """用户拒绝 → False。"""
        pm = PermissionManager(timeout=2.0)
        emitted: list[dict] = []

        async def _run() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("code_execution", "elevated"),
                args={"code": "x"},
                emit_fn=async_emit(emitted),
            )

        async def _resolve() -> None:
            for _ in range(100):
                if emitted:
                    break
                await asyncio.sleep(0.01)
            pm.resolve(emitted[0]["confirmation_id"], False)

        async def _main() -> bool:
            t = asyncio.create_task(_run())
            await asyncio.sleep(0)
            await _resolve()
            return await t

        assert asyncio.run(_main()) == "denied"
        # 拒绝结果也缓存: 再次调用不 emit 且直接拒绝
        async def _again() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("code_execution", "elevated"),
                args={"code": "x"},
                emit_fn=async_emit(emitted),
            )

        assert asyncio.run(_again()) == "denied"
        assert len(emitted) == 1  # 未再 emit

    def test_elevated_timeout_rejected(self) -> None:
        """60s 超时(测试短超时)自动拒绝。"""
        pm = PermissionManager(timeout=0.05)
        emitted: list[dict] = []

        async def _run() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("code_execution", "elevated"),
                args={"code": "slow"},
                emit_fn=async_emit(emitted),
            )

        assert asyncio.run(_run()) == "timeout"
        assert len(emitted) == 1

    def test_cache_key_includes_args(self) -> None:
        """不同参数需重新确认(缓存 key 含 args)。"""
        pm = PermissionManager(timeout=2.0)
        emitted: list[dict] = []
        tool = _tool("code_execution", "elevated")

        async def _confirm(args: dict) -> bool:
            return await pm.check_and_confirm(
                session_id=1, tool_def=tool, args=args,
                emit_fn=async_emit(emitted),
            )

        async def _resolve_and_await(task) -> bool:
            for _ in range(100):
                if emitted:
                    break
                await asyncio.sleep(0.01)
            pm.resolve(emitted[-1]["confirmation_id"], True)
            return await task

        async def _main() -> None:
            # 参数 A
            t1 = asyncio.create_task(_confirm({"code": "a"}))
            await asyncio.sleep(0)
            await _resolve_and_await(t1)
            # 参数 B(不同) → 需重新确认
            t2 = asyncio.create_task(_confirm({"code": "b"}))
            await asyncio.sleep(0)
            await _resolve_and_await(t2)

        asyncio.run(_main())
        assert len(emitted) == 2


class TestResolve:
    def test_resolve_unknown_id_noop(self) -> None:
        """未知 confirmation_id → no-op 返回 False。"""
        pm = PermissionManager(timeout=0.5)
        assert pm.resolve("nonexistent", True) is False

    def test_resolve_twice_noop(self) -> None:
        """同一 confirmation_id resolve 两次 → 第二次 no-op。"""
        pm = PermissionManager(timeout=2.0)
        emitted: list[dict] = []

        async def _run() -> bool:
            return await pm.check_and_confirm(
                session_id=1,
                tool_def=_tool("code_execution", "elevated"),
                args={"code": "x"},
                emit_fn=async_emit(emitted),
            )

        async def _main() -> bool:
            t = asyncio.create_task(_run())
            await asyncio.sleep(0)
            for _ in range(100):
                if emitted:
                    break
                await asyncio.sleep(0.01)
            cid = emitted[0]["confirmation_id"]
            pm.resolve(cid, True)
            assert pm.resolve(cid, True) is False  # 第二次 no-op
            return await t

        assert asyncio.run(_main()) == "approved"
