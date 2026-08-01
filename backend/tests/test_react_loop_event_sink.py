"""M4 m4-eval-runner-replay AC-6 - ReactLoop event_sink 扩展测试。

Source: spec/m4-eval-runner-replay AC-6 + plan step 1, step 10
- event_sink=None 时静默(不调回调),event_queue 仍入队,现有调用方行为不变
- event_sink 非 None 时,每个 event 都回调一次
- 现有调用方(不注入 event_sink)行为完全不变(回归保护)
"""
import asyncio
import os
from typing import Any

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop, ReactLoopState
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL, ToolDef

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
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


async def _create_session(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-event-sink",
        "mock-glm",
    )


class _MockAdapter:
    """测试用 mock 适配器,返回预设 ChatResult。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResult:
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


def test_react_loop_accepts_event_sink_param_default_none():
    """ReactLoop __init__ 接受 event_sink 参数,默认 None(AC-6)。"""
    _setup_schema()

    async def _run() -> ReactLoop:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            adapter = _MockAdapter(responses=[])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            return loop
        finally:
            await conn.close()

    loop = asyncio.run(_run())
    # event_sink 默认 None,内部存储为 _event_sink
    assert loop._event_sink is None


def test_react_loop_event_sink_invoked_on_each_event():
    """event_sink 非 None 时,每个 event 都回调一次(AC-6)。

    构造无 tool_calls 的简单 run_turn,产出 thinking + final 两类 event,
    event_sink 应被调用 2 次,event_queue 仍入队 2 条(两路并存)。
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], list[dict]]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="hello", used_provider="mock")]
            )

            sink_events: list[dict] = []

            async def _sink(evt: dict) -> None:
                sink_events.append(evt)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                event_sink=_sink,
            )
            await loop.run_turn("hi")

            queue_events: list[dict] = []
            while not loop.event_queue.empty():
                queue_events.append(loop.event_queue.get_nowait())

            return sink_events, queue_events
        finally:
            await conn.close()

    sink_events, queue_events = asyncio.run(_run())
    # event_sink 回调被调用 2 次(thinking + final)
    assert len(sink_events) == 2
    assert sink_events[0]["event_type"] == "thinking"
    assert sink_events[1]["event_type"] == "final"
    # event_queue 仍入队 2 条(两路并存,不破坏现有消费者)
    assert len(queue_events) == 2
    assert queue_events[0]["event_type"] == "thinking"
    assert queue_events[1]["event_type"] == "final"
    # sink 与 queue 收到的事件内容一致
    assert sink_events[0] == queue_events[0]
    assert sink_events[1] == queue_events[1]


def test_react_loop_event_sink_none_default_preserves_existing_behavior():
    """event_sink=None(默认)时,run_turn 行为与现有调用方完全一致(AC-6 回归保护)。

    不注入 event_sink,run_turn 仍正常产出 thinking + final,
    event_queue 正常入队,loop.state 回到 IDLE。
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="answer", used_provider="mock")]
            )
            # 不注入 event_sink(现有调用方写法)
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("ping")

            events: list[dict] = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())

            return events, loop.state
        finally:
            await conn.close()

    events, final_state = asyncio.run(_run())
    # 现有行为:2 个 event(thinking + final),state=IDLE
    assert len(events) == 2
    assert events[0]["event_type"] == "thinking"
    assert events[1]["event_type"] == "final"
    assert final_state == ReactLoopState.IDLE
