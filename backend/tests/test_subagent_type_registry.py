"""2026-08-13 类型感知限流: SubagentTypeRegistry 进程级并发计数测试。

覆盖(方案 §6): 计数增减 / 超限等待 / 超时返回 False / release 唤醒 /
并发 acquire 正确。
"""
import asyncio

from private_agent.core.subagent import SubagentTypeRegistry


def test_acquire_release_basic():
    """acquire 递增, release 递减, 回到 0。"""
    reg = SubagentTypeRegistry()

    async def _run():
        assert await reg.acquire("search", max_conc=1, timeout_sec=0.5) is True
        assert reg.current("search") == 1
        await reg.release("search")
        assert reg.current("search") == 0

    asyncio.run(_run())


def test_acquire_over_limit_waits_then_timeout():
    """超过 max_conc 后 acquire 等待, 超时返回 False。"""
    reg = SubagentTypeRegistry()

    async def _run():
        assert await reg.acquire("search", max_conc=1, timeout_sec=0.5) is True
        # 第二个同类型 acquire → 超限等待 → 0.2s 超时返回 False
        ok = await reg.acquire("search", max_conc=1, timeout_sec=0.2)
        assert ok is False
        assert reg.current("search") == 1  # 未占用新配额

    asyncio.run(_run())


def test_release_wakes_waiter():
    """release 后等待中的 acquire 获得配额。"""
    reg = SubagentTypeRegistry()

    async def _run():
        assert await reg.acquire("search", max_conc=1, timeout_sec=1.0) is True
        waiter = asyncio.create_task(
            reg.acquire("search", max_conc=1, timeout_sec=1.0)
        )
        await asyncio.sleep(0.05)  # 确保 waiter 已进入等待
        assert not waiter.done()
        await reg.release("search")
        assert await asyncio.wait_for(waiter, timeout=1.0) is True
        assert reg.current("search") == 1

    asyncio.run(_run())


def test_different_types_independent():
    """不同类型互不影响。"""
    reg = SubagentTypeRegistry()

    async def _run():
        assert await reg.acquire("search", max_conc=1, timeout_sec=0.5) is True
        assert await reg.acquire("analysis", max_conc=1, timeout_sec=0.5) is True
        assert await reg.acquire("code", max_conc=1, timeout_sec=0.5) is True
        assert reg.current("search") == 1
        assert reg.current("analysis") == 1
        assert reg.current("code") == 1

    asyncio.run(_run())


def test_concurrent_acquire_respects_limit():
    """并发 acquire 总数不超过 max_conc。"""
    reg = SubagentTypeRegistry()
    N = 8
    MAX = 2

    async def _worker():
        return await reg.acquire("search", max_conc=MAX, timeout_sec=1.0)

    async def _run():
        results = await asyncio.gather(*(_worker() for _ in range(N)))
        assert sum(1 for r in results if r) == MAX
        assert reg.current("search") == MAX

    asyncio.run(_run())


def test_max_conc_zero_always_fails():
    """max_conc<=0 直接拒绝(防御性)。"""
    reg = SubagentTypeRegistry()

    async def _run():
        assert await reg.acquire("search", max_conc=0, timeout_sec=0.1) is False

    asyncio.run(_run())
