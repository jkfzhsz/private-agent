"""V1.5 项-5 流程级暂停 —— 暂停控制器 + ReactLoop 挂起/继续。

语义: 生成中用户"暂停" → 当前迭代完成后、下一迭代开始前挂起(不调用
模型/工具, 不消耗 token), 产出 turn_paused; "继续" → 解除挂起,
产出 turn_resumed, 循环继续直至 final。区别于 cancel(终止)。
"""
import asyncio
import os

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
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


def _echo_tool_call(call_id: str = "call_1", text: str = "hi") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "echo",
            "arguments": '{"text": "' + text + '"}',
        },
    }


class _MockAdapter:
    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult], on_chat=None) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.on_chat = on_chat

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        # 每次 chat 前触发回调(测试可在迭代间隙注入 pause)
        if self.on_chat is not None:
            self.on_chat(self._idx)
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


async def _drain_events(loop: ReactLoop) -> list[dict]:
    events = []
    while not loop.event_queue.empty():
        events.append(loop.event_queue.get_nowait())
    return events


async def _wait_event_type(
    loop: ReactLoop,
    event_type: str,
    collected: list[dict],
    timeout: float = 5.0,
) -> dict:
    """轮询并累积事件, 直到出现指定类型(不丢弃同批到达的其他事件)。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        collected.extend(await _drain_events(loop))
        for ev in collected:
            if ev.get("event_type") == event_type:
                return ev
        await asyncio.sleep(0.02)
    raise AssertionError(f"timeout waiting for event_type={event_type}")


def test_pause_controller_state_machine():
    """暂停控制器: 初始未暂停 → pause 挂起 → resume 恢复。"""
    from private_agent.main import _PauseController

    ctrl = _PauseController()
    assert ctrl.is_paused() is False
    # 未暂停时 wait() 立即返回
    asyncio.run(ctrl.wait())

    ctrl.pause()
    assert ctrl.is_paused() is True

    async def _resume_after_delay() -> None:
        await asyncio.sleep(0.05)
        ctrl.resume()

    async def _run() -> None:
        waiter = asyncio.create_task(ctrl.wait())  # 挂起等待
        await asyncio.sleep(0.02)
        assert not waiter.done()  # 暂停中 wait 不返回
        resumer = asyncio.create_task(_resume_after_delay())
        await asyncio.wait([waiter, resumer])
        assert ctrl.is_paused() is False
        assert waiter.done()

    asyncio.run(_run())


def test_react_loop_pause_halts_between_iterations():
    """暂停时: 迭代 2 开始前产出 turn_paused 并挂起; 继续后产出 turn_resumed 并完成。"""
    _setup_schema()

    async def _run() -> tuple[list[str], bool]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ('pause', 'mock') "
                "RETURNING id"
            )
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)

            from private_agent.main import _PauseController

            ctrl = _PauseController()
            pause_requested = False

            def _on_chat(idx: int) -> None:
                nonlocal pause_requested
                # 第一次 chat 返回前触发暂停 → 迭代 2 开始前生效
                if idx == 0 and not pause_requested:
                    pause_requested = True
                    ctrl.pause()

            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="", used_provider="mock",
                        tool_calls=[_echo_tool_call("call_1", "hi")],
                    ),
                    ChatResult(content="final after pause", used_provider="mock"),
                ],
                on_chat=_on_chat,
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
                pause_controller=ctrl,
            )
            task = asyncio.create_task(loop.run_turn("hi"))
            collected: list[dict] = []
            # 等待 turn_paused(迭代 2 挂起)
            paused_ev = await _wait_event_type(loop, "turn_paused", collected)
            assert paused_ev["payload"]["turn"] == 1
            assert ctrl.is_paused() is True
            assert not task.done()  # 挂起中 run_turn 未完成

            # 模型不应被调用(挂起期间不消耗): adapter 只被调用 1 次
            assert adapter._idx == 1

            # 继续 → turn_resumed → final
            ctrl.resume()
            resumed_ev = await _wait_event_type(loop, "turn_resumed", collected)
            assert resumed_ev["payload"]["turn"] == 1
            await asyncio.wait_for(task, timeout=10)
            collected.extend(await _drain_events(loop))
            event_types = [e["event_type"] for e in collected]
            assert "final" in event_types
            assert adapter._idx == 2  # 继续后模型被再次调用
            return event_types, True
        finally:
            await conn.close()

    event_types, ok = asyncio.run(_run())
    assert ok
    # 事件顺序: turn_paused 在第二次模型调用前(thinking 后)
    assert event_types.count("turn_paused") == 1
    assert event_types.count("turn_resumed") == 1


def test_react_loop_no_pause_controller_is_noop():
    """未注入暂停控制器时 run_turn 零回归(无 turn_paused 事件)。"""
    _setup_schema()

    async def _run() -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ('nopause', 'mock') "
                "RETURNING id"
            )
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="done", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                # pause_controller 缺省 None
            )
            await loop.run_turn("hi")
            events = await _drain_events(loop)
            return [e["event_type"] for e in events]
        finally:
            await conn.close()

    event_types = asyncio.run(_run())
    assert "turn_paused" not in event_types
    assert "final" in event_types
