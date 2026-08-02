"""M1 Phase 3 - ReactLoop 状态机 + 流式事件产出。

Source: spec/m1-react-loop AC-1 + Solution `core/react_loop.py`
- 蓝图 §2.4: ReAct 状态机 IDLE/THINKING/ACTING/OBSERVING/ERROR
- 蓝图 §2.6: asyncio 协程模型 + 流式输出
- spec AC-1: ReAct 循环产出 thinking→tool_call→tool_result→final 四类 event(顺序正确,turn 递增)
- spec Core entities: ReactLoop(state, session_id, context_manager, adapter) 1:1 Session/ContextManager/ModelAdapter
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
from private_agent.storage.react_events import insert_react_event
from private_agent.tools.defs import ECHO_TOOL, DATETIME_TOOL, ToolDef

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
        "test-react",
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
        self.chat_calls: list[tuple[list[dict], list[dict] | None]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.chat_calls.append((list(messages), list(tools) if tools else None))
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


# ──────────────────────────────────────────────────────────────────────────────
# ReactLoopState 枚举
# ──────────────────────────────────────────────────────────────────────────────


def test_react_loop_state_enum_has_five_states():
    """ReactLoopState 含 IDLE/THINKING/ACTING/OBSERVING/ERROR 五态。"""
    assert ReactLoopState.IDLE.value == "idle"
    assert ReactLoopState.THINKING.value == "thinking"
    assert ReactLoopState.ACTING.value == "acting"
    assert ReactLoopState.OBSERVING.value == "observing"
    assert ReactLoopState.ERROR.value == "error"


# ──────────────────────────────────────────────────────────────────────────────
# ReactLoop 实例化
# ──────────────────────────────────────────────────────────────────────────────


def test_react_loop_initial_state_is_idle():
    """ReactLoop 实例化后 state=IDLE。"""
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
    assert loop.state == ReactLoopState.IDLE


def test_react_loop_has_event_queue():
    """ReactLoop 含 event_queue(asyncio.Queue)用于流式事件产出。"""
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
    assert hasattr(loop, "event_queue")
    assert isinstance(loop.event_queue, asyncio.Queue)


def test_react_loop_default_max_iterations_is_ten():
    """ReactLoop 默认 max_iterations=10(spec Edge cases 防死循环)。"""
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
    assert loop.max_iterations == 10


# ──────────────────────────────────────────────────────────────────────────────
# run_turn 简单路径(无 tool_calls):IDLE→THINKING→产出 thinking+final→IDLE
# Source: spec AC-1
# ──────────────────────────────────────────────────────────────────────────────


def test_run_turn_no_tool_calls_produces_thinking_and_final_events():
    """无 tool_calls 时 run_turn 产出 thinking + final 两类 event。

    Source: spec AC-1 "收到 thinking→...→final 四类 react_event"(无工具时仅 thinking+final)
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="hello world", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("hi")
            # 收集 event_queue 中的所有事件
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, loop.state
        finally:
            await conn.close()

    events, final_state = asyncio.run(_run())
    assert len(events) == 2
    assert events[0]["type"] == "react_event"
    assert events[0]["event_type"] == "thinking"
    assert events[1]["type"] == "react_event"
    assert events[1]["event_type"] == "final"
    assert final_state == ReactLoopState.IDLE


def test_run_turn_persists_react_events_to_db_with_incrementing_turn():
    """run_turn 将 thinking + final 事件持久化到 react_events 表,turn 递增。

    Source: spec AC-1 "turn 递增"
    """
    _setup_schema()

    async def _run() -> list[dict]:
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
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("question")
            rows = await conn.fetch(
                "SELECT turn, event_type, payload FROM react_events WHERE session_id=$1 ORDER BY id",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 3
    assert rows[0]["event_type"] == "thinking"
    assert rows[1]["event_type"] == "final"
    assert rows[2]["event_type"] == "checkpoint"
    # turn 递增(同一轮内 thinking 和 final 共享 turn=1)
    assert rows[0]["turn"] == 1
    assert rows[1]["turn"] == 1


def test_run_turn_calls_adapter_chat_with_built_messages():
    """run_turn 调用 adapter.chat 时传入 context_manager.build_per_turn 的消息列表。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys-prompt", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="ok", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("user-input")
            return adapter.chat_calls[0][0] if adapter.chat_calls else []
        finally:
            await conn.close()

    messages = asyncio.run(_run())
    # messages 应含 system(frozen) + user(active)
    roles = [m["role"] for m in messages]
    assert "system" in roles
    assert "user" in roles
    user_msg = next(m for m in messages if m["role"] == "user")
    assert user_msg["content"] == "user-input"


def test_run_turn_appends_assistant_message_to_active_zone():
    """run_turn 完成后,助手回复被追加到 Active Zone(messages 表 + 内存)。

    Source: spec AC-3 "每轮结束 Active Zone 追加用户/助手消息"
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], ContextManager]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="assistant-reply", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("q")
            rows = await conn.fetch(
                "SELECT role, content FROM messages WHERE session_id=$1 AND role='assistant'",
                session_id,
            )
            return [dict(r) for r in rows], cm
        finally:
            await conn.close()

    rows, cm = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["content"] == "assistant-reply"
    # 内存 Active Zone 应含 user + assistant
    active_roles = [m["role"] for m in cm.active_zone.messages]
    assert "user" in active_roles
    assert "assistant" in active_roles


def test_run_turn_thinking_event_payload_contains_assistant_content():
    """thinking event 的 payload 含助手回复内容。

    Source: spec AC-1 react_event(thinking) 携带 LLM 输出
    """
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="thinking text", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("q")
            event = await loop.event_queue.get()
            return event
        finally:
            await conn.close()

    event = asyncio.run(_run())
    assert event["event_type"] == "thinking"
    assert event["payload"]["content"] == "thinking text"


def test_run_turn_final_event_payload_contains_assistant_content():
    """final event 的 payload 含最终回复内容。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="final answer", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("q")
            # 跳过 thinking
            await loop.event_queue.get()
            event = await loop.event_queue.get()
            return event
        finally:
            await conn.close()

    event = asyncio.run(_run())
    assert event["event_type"] == "final"
    assert event["payload"]["content"] == "final answer"


def test_run_turn_increments_turn_per_user_message():
    """多次 run_turn 时 turn 递增(每次用户消息 turn+1)。"""
    _setup_schema()

    async def _run() -> list[int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(content="r1", used_provider="mock"),
                    ChatResult(content="r2", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("q1")
            await loop.run_turn("q2")
            rows = await conn.fetch(
                "SELECT DISTINCT turn FROM react_events WHERE session_id=$1 ORDER BY turn",
                session_id,
            )
            return [r["turn"] for r in rows]
        finally:
            await conn.close()

    turns = asyncio.run(_run())
    assert turns == [1, 2]
"""Behavior 5+6: ReactLoop tool_call 路径 + max_iterations + ERROR 状态。

Source: spec/m1-react-loop AC-1 + Edge cases
- spec AC-1: thinking→tool_call→tool_result→final 四类 event 顺序正确
- spec Edge cases: max_iterations 防死循环(默认 10);ERROR 态仅记录 react_events
- spec Failure modes: 三家全 fail → 返回 error 事件给客户端
"""
import asyncio
import os
from typing import Any

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop, ReactLoopState
from private_agent.models.base import (
    AllProvidersFailedError,
    ChatResult,
    ModelCapability,
    ProviderError,
)
from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL, DATETIME_TOOL

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


async def _create_session(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-react-tool",
        "mock-glm",
    )


class _MockAdapter:
    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.chat_calls: list[tuple[list[dict], list[dict] | None]] = []

    async def chat(self, messages, tools=None, max_tokens=None):
        self.chat_calls.append((list(messages), list(tools) if tools else None))
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock exhausted: idx={self._idx}")
        r = self._responses[self._idx]
        self._idx += 1
        return r


class _FailingAdapter:
    """始终抛 ProviderError 的适配器(模拟 fallback 全失败)。"""

    provider_name = "failing"
    capability = ModelCapability(
        streaming=False, function_calling=False, vision=False, json_mode=False
    )

    async def chat(self, messages, tools=None, max_tokens=None):
        raise ProviderError("failing", "always fails")


def _echo_tool_call(call_id: str = "call_1", text: str = "hi") -> dict:
    """构造 OpenAI tool_call dict。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "echo",
            "arguments": '{"text": "' + text + '"}',
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# tool_call 完整路径:thinking→tool_call→tool_result→final
# Source: spec AC-1
# ──────────────────────────────────────────────────────────────────────────────


def test_run_turn_with_tool_calls_produces_four_events_in_order():
    """有 tool_calls 时产出 thinking→tool_call→tool_result→final 四类 event。

    Source: spec AC-1 "收到 thinking→tool_call→tool_result→final 四类 react_event(顺序正确)"
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call("call_1", "hi")],
                        used_provider="mock",
                    ),
                    ChatResult(content="echo said: hi", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("please echo hi")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, loop.state
        finally:
            await conn.close()

    events, final_state = asyncio.run(_run())
    assert len(events) == 4, f"expected 4 events, got {len(events)}: {events}"
    assert [e["event_type"] for e in events] == [
        "thinking",
        "tool_call",
        "tool_result",
        "final",
    ]
    assert final_state == ReactLoopState.IDLE


def test_run_turn_with_tool_calls_persists_all_events_to_db():
    """tool_call 路径的 4 类事件全部持久化到 react_events 表。"""
    _setup_schema()

    async def _run() -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call("call_1", "hi")],
                        used_provider="mock",
                    ),
                    ChatResult(content="final answer", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("echo hi")
            rows = await conn.fetch(
                "SELECT event_type FROM react_events WHERE session_id=$1 ORDER BY id",
                session_id,
            )
            return [r["event_type"] for r in rows]
        finally:
            await conn.close()

    types = asyncio.run(_run())
    assert types == ["thinking", "tool_call", "tool_result", "final", "checkpoint"]


def test_run_turn_persists_assistant_message_with_tool_calls():
    """tool_call 路径中,助手消息(含 tool_calls)持久化到 messages 表。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call("call_1", "hi")],
                        used_provider="mock",
                    ),
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("echo")
            rows = await conn.fetch(
                "SELECT role, tool_calls FROM messages WHERE session_id=$1 AND role='assistant'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    # 应有 2 条 assistant 消息:第一条含 tool_calls,第二条为 final
    assert len(rows) == 2
    # 第一条含 tool_calls
    import json as _json
    tc = rows[0]["tool_calls"]
    if isinstance(tc, str):
        tc = _json.loads(tc)
    assert tc is not None
    assert tc[0]["function"]["name"] == "echo"


def test_run_turn_persists_tool_message_with_result():
    """tool_call 路径中,工具结果消息(role='tool')持久化到 messages 表。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call("call_1", "hello")],
                        used_provider="mock",
                    ),
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("echo hello")
            rows = await conn.fetch(
                "SELECT role, tool_call_id, content, name FROM messages WHERE session_id=$1 AND role='tool'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["tool_call_id"] == "call_1"
    assert rows[0]["content"] == "hello"  # echo 工具回显
    assert rows[0]["name"] == "echo"


def test_run_turn_tool_call_event_payload_contains_tool_name_and_args():
    """tool_call event 的 payload 含工具名和参数。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call("call_1", "hi")],
                        used_provider="mock",
                    ),
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("echo")
            # 跳过 thinking
            await loop.event_queue.get()
            event = await loop.event_queue.get()
            return event
        finally:
            await conn.close()

    event = asyncio.run(_run())
    assert event["event_type"] == "tool_call"
    assert event["payload"]["tool_name"] == "echo"
    assert event["payload"]["tool_call_id"] == "call_1"
    assert event["payload"]["arguments"] == {"text": "hi"}


def test_run_turn_tool_result_event_payload_contains_output():
    """tool_result event 的 payload 含工具输出。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call("call_1", "result-text")],
                        used_provider="mock",
                    ),
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("echo")
            # 跳过 thinking + tool_call
            await loop.event_queue.get()
            await loop.event_queue.get()
            event = await loop.event_queue.get()
            return event
        finally:
            await conn.close()

    event = asyncio.run(_run())
    assert event["event_type"] == "tool_result"
    assert event["payload"]["tool_call_id"] == "call_1"
    assert event["payload"]["output"] == "result-text"


def test_run_turn_multiple_tool_calls_in_one_response():
    """单次 adapter 返回多个 tool_calls 时,全部执行并产出对应 tool_call/tool_result events。"""
    _setup_schema()

    async def _run() -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[
                            _echo_tool_call("call_1", "first"),
                            _echo_tool_call("call_2", "second"),
                        ],
                        used_provider="mock",
                    ),
                    ChatResult(content="both done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("echo twice")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events]
        finally:
            await conn.close()

    types = asyncio.run(_run())
    # V2 P2 并行语义: 同轮 tool_call 事件先全部产出(Phase A), 再并行执行
    # 后按原始顺序产出 tool_result(Phase C)
    assert types == [
        "thinking",
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "final",
    ]


# ──────────────────────────────────────────────────────────────────────────────
# max_iterations 防死循环
# Source: spec Edge cases "ReAct 循环 max_iterations 防死循环(默认 10)"
# ──────────────────────────────────────────────────────────────────────────────


def test_run_turn_max_iterations_stops_with_error_event():
    """迭代达到 max_iterations 时停止并产出 error event。

    Source: spec Edge cases "max_iterations 防死循环"
    """
    _setup_schema()

    async def _run() -> tuple[list[str], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            # 始终返回 tool_calls(死循环)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call(f"call_{i}", "loop")],
                        used_provider="mock",
                    )
                    for i in range(20)
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
                max_iterations=3,
            )
            await loop.run_turn("loop")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events], loop.state
        finally:
            await conn.close()

    types, final_state = asyncio.run(_run())
    # 最后一个事件应为 error
    assert types[-1] == "error"
    assert final_state == ReactLoopState.ERROR


def test_run_turn_max_iterations_default_is_ten():
    """max_iterations 默认 10,死循环场景下 adapter 最多调用 10 次。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[_echo_tool_call(f"call_{i}", "x")],
                        used_provider="mock",
                    )
                    for i in range(30)
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("loop")
            return len(adapter.chat_calls)
        finally:
            await conn.close()

    calls = asyncio.run(_run())
    # 默认 max_iterations=10,adapter 应被调用 10 次
    assert calls == 10


# ──────────────────────────────────────────────────────────────────────────────
# ERROR 状态:模型全失败 + 未知工具
# Source: spec Failure modes "三家全 fail → 返回 error 事件给客户端"
# ──────────────────────────────────────────────────────────────────────────────


def test_run_turn_all_providers_failed_produces_error_event():
    """adapter 抛 AllProvidersFailedError 时产出 error event,state=ERROR。

    Source: spec Failure modes "三家全 fail → 返回 error 事件给客户端"
    """
    _setup_schema()

    async def _run() -> tuple[list[str], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)

            class _AllFailAdapter:
                provider_name = "all-fail"
                capability = ModelCapability(
                    streaming=False, function_calling=False, vision=False, json_mode=False
                )

                async def chat(self, messages, tools=None, max_tokens=None):
                    raise AllProvidersFailedError("all 3 providers failed")

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_AllFailAdapter(),
                tools=[],
                conn=conn,
            )
            await loop.run_turn("q")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events], loop.state
        finally:
            await conn.close()

    types, final_state = asyncio.run(_run())
    assert "error" in types
    assert final_state == ReactLoopState.ERROR


def test_run_turn_unknown_tool_produces_error_event():
    """adapter 返回未知工具名时产出 error event。

    Source: spec Failure modes
    """
    _setup_schema()

    async def _run() -> tuple[list[str], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "nonexistent_tool",
                                    "arguments": "{}",
                                },
                            }
                        ],
                        used_provider="mock",
                    ),
                    ChatResult(content="recovered", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop.run_turn("call unknown")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events], loop.state
        finally:
            await conn.close()

    types, final_state = asyncio.run(_run())
    # V2 P2 语义: 未知工具 → 单工具 error 回传(tool_result.error), 不中断整轮,
    # 下一轮模型据此收尾 → final, 状态 IDLE
    assert "tool_call" in types
    assert "tool_result" in types
    assert "final" in types
    assert "error" not in types  # 不再产出整轮 error event
    assert final_state == ReactLoopState.IDLE


def test_run_turn_error_event_payload_contains_message():
    """error event 的 payload 含错误信息。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)

            class _FailAdapter:
                provider_name = "fail"
                capability = ModelCapability(
                    streaming=False, function_calling=False, vision=False, json_mode=False
                )

                async def chat(self, messages, tools=None, max_tokens=None):
                    raise AllProvidersFailedError("simulated failure")

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_FailAdapter(),
                tools=[],
                conn=conn,
            )
            await loop.run_turn("q")
            event = await loop.event_queue.get()
            return event
        finally:
            await conn.close()

    event = asyncio.run(_run())
    assert event["event_type"] == "error"
    assert "message" in event["payload"]
    assert "simulated failure" in event["payload"]["message"]
