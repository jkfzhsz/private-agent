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
from private_agent.tools.defs import ECHO_TOOL, DATETIME_TOOL, ToolDef, ToolResult

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


async def _create_session(conn: "asyncpg.Connection") -> int:
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
    # 2026-08-15 更新: 状态栏注入审计(context_injected)新增事件, 过滤后
    # 与原事件序列比对; 额外断言审计事件存在(新行为)。
    event_types = [r["event_type"] for r in rows]
    assert "context_injected" in event_types, (
        f"expect status_bar context_injected audit, got {event_types}"
    )
    core_events = [t for t in event_types if t != "context_injected"]
    assert core_events == ["thinking", "final", "checkpoint"], core_events
    assert rows[0]["event_type"] == "thinking" or rows[1]["event_type"] == "thinking"
    # turn 递增(同一轮内 thinking 和 final 共享 turn=1)
    thinking_row = next(r for r in rows if r["event_type"] == "thinking")
    final_row = next(r for r in rows if r["event_type"] == "final")
    assert thinking_row["turn"] == 1
    assert final_row["turn"] == 1


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


async def _create_session(conn: "asyncpg.Connection") -> int:
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

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
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

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
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
    # 2026-08-15 更新: 状态栏注入审计(context_injected)新增事件, 过滤后
    # 与原事件序列比对(thinking/tool_call/tool_result/final/checkpoint)。
    assert "context_injected" in types, (
        f"expect status_bar context_injected audit, got {types}"
    )
    core_events = [t for t in types if t != "context_injected"]
    assert core_events == ["thinking", "tool_call", "tool_result", "final", "checkpoint"], (
        core_events
    )


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
    """迭代达到 max_iterations 时询问用户, 30s 超时默认继续(不直接失败)。

    Source: spec Edge cases "max_iterations 防死循环"
    2026-08-16(阶段2 反馈): 超限改为询问继续 —— 测试传小确认超时(0.1s)
    模拟 30s 超时默认继续的行为; 响应耗尽后 adapter 无响应 → 模型报错收尾。
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
                limit_confirm_timeout=0.1,
            )
            await loop.run_turn("loop")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events], loop.state
        finally:
            await conn.close()

    types, final_state = asyncio.run(_run())
    # 2026-08-16: 超限不再直接 error —— 应发出 iteration_limit_reached 询问
    assert "iteration_limit_reached" in types
    # 0.1s 确认超时默认继续 → 不进入 ERROR(响应耗尽后按模型失败处理)
    assert final_state != ReactLoopState.ERROR or types[-1] != "error"


def test_run_turn_max_iterations_default_is_ten():
    """max_iterations 默认 10,死循环场景下 adapter 最多调用 10 次。

    注: 显式关闭 Doom Loop 检测(loop.enabled=false), 本测试只验证
    max_iterations 硬上限, 不被循环检测提前终止(循环检测有独立测试)。
    2026-08-16(阶段2): 超限询问 30s 超时默认继续 → 10 次后继续直到响应耗尽;
    本测试传 limit_confirm_timeout=0.1 快速通过询问, 验证上限本身仍生效
    (第一次询问发生在第 10 次迭代后)。
    """
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
                    *[
                        ChatResult(
                            content="",
                            tool_calls=[_echo_tool_call(f"call_{i}", "x")],
                            used_provider="mock",
                        )
                        for i in range(14)
                    ],
                    # 最后: 正常 content, 让循环自然结束
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
                cfg={"context": {"loop": {"enabled": False}}},
                limit_confirm_timeout=0.1,
            )
            await loop.run_turn("loop")
            return len(adapter.chat_calls)
        finally:
            await conn.close()

    calls = asyncio.run(_run())
    # 默认 max_iterations=10: 首次询问发生在第 10 次迭代后; 0.1s 超时默认
    # 继续(+10 步) → 继续消费到 content 结尾(15 次调用内自然结束)
    assert 10 <= calls <= 15, f"上限 10 应触发首次询问, 实际 {calls}"


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

                async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
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

                async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
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


# ──────────────────────────────────────────────────────────────────────────────
# 2026-08-08: MCP 工具参数注入回归 —— T-1 data_dir/workspace 只注入内置文件工具
# Source: mempalace/searchpin 调用"返回空"终极根因(注入未知参数 → server 校验失败)
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_injection_skips_mcp_tools_but_applies_to_file_tools():
    """ReactLoop 对 MCP 工具(mcp__ 前缀)不得注入 data_dir/workspace;
    对内置文件工具(file_read/file_write/read_artifact)必须注入。

    2026-08-08 根因: _exec_plan 原对所有工具无条件注入 data_dir/workspace,
    MCP server(inputSchema 严格校验)收到未知参数 → 参数校验失败 → isError
    → output 空 → LLM 看到"空结果"。
    """
    _setup_schema()

    captured: dict[str, dict] = {}

    async def _mcp_handler(args: dict):
        captured["mcp"] = dict(args)
        return ToolResult(output="mcp-ok")

    async def _file_handler(args: dict):
        captured["file"] = dict(args)
        return ToolResult(output="file-ok")

    mcp_tool = ToolDef(
        name="mcp__mempalace__mempalace_status",
        description="test mcp tool",
        parameters_schema={"type": "object", "properties": {}},
        handler=_mcp_handler,
    )
    file_tool = ToolDef(
        name="file_read",
        description="test file tool",
        parameters_schema={"type": "object", "properties": {}},
        handler=_file_handler,
    )

    async def _run() -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[mcp_tool, file_tool]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="",
                        tool_calls=[
                            {
                                "id": "call_mcp",
                                "type": "function",
                                "function": {
                                    "name": "mcp__mempalace__mempalace_status",
                                    "arguments": "{}",
                                },
                            },
                            {
                                "id": "call_file",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": '{"path": "x"}',
                                },
                            },
                        ],
                        used_provider="mock",
                    ),
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[mcp_tool, file_tool],
                conn=conn,
                cfg={"system": {"workspace_root": r"D:\work"}},
            )
            await loop.run_turn("test")
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events]
        finally:
            await conn.close()

    asyncio.run(_run())
    # MCP 工具: 不得注入 data_dir/workspace(否则 server 参数校验失败 → 空结果)
    assert "data_dir" not in captured.get("mcp", {}), (
        f"MCP 工具被注入 data_dir: {captured.get('mcp')}"
    )
    assert "workspace" not in captured.get("mcp", {}), (
        f"MCP 工具被注入 workspace: {captured.get('mcp')}"
    )
    # 内置文件工具: 2026-08-16 起不再强制注入 data_dir(全局读取, 免确认);
    # workspace 也不再注入(file_read 全局可读, 原 T-1 注入语义废止)
    assert "data_dir" not in captured.get("file", {}), (
        f"file_read 被注入 data_dir(全局读取语义应无限制): {captured.get('file')}"
    )
    assert "workspace" not in captured.get("file", {}), (
        f"file_read 被注入 workspace: {captured.get('file')}"
    )


class TestImageInjection:
    """0.5.1: 图片文本引用 → image_url(data URL)注入(多模态链路修复)。"""

    def _make_png(self, path) -> None:
        # 最小 1x1 PNG
        import base64
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
            "h6FO1AAAAABJRU5ErkJggg=="
        )
        with open(path, "wb") as f:
            f.write(base64.b64decode(png_b64))

    def test_injects_image_url_for_uploaded_image(self, tmp_path) -> None:
        from private_agent.core.react_loop import _inject_image_urls
        img = tmp_path / "photo.png"
        self._make_png(img)
        messages = [{
            "role": "user",
            "content": f"识别这张图 [已上传文件: photo.png 路径: {img}]",
        }]
        out, _skipped = _inject_image_urls(messages)
        content = out[0]["content"]
        assert isinstance(content, list)
        parts = {p["type"]: p for p in content}
        assert parts["image_url"]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "识别这张图" in parts["text"]["text"]

    def test_paste_image_reference(self, tmp_path) -> None:
        from private_agent.core.react_loop import _inject_image_urls
        img = tmp_path / "clip.jpg"
        self._make_png(img)
        messages = [{
            "role": "user",
            "content": f"[用户粘贴图片: clip.jpg 路径: {img}] 这是什么?",
        }]
        out, _skipped = _inject_image_urls(messages)
        content = out[0]["content"]
        assert isinstance(content, list)
        assert any(
            p.get("type") == "image_url"
            and p["image_url"]["url"].startswith("data:image/jpeg;base64,")
            for p in content
        )

    def test_no_image_reference_keeps_content(self) -> None:
        from private_agent.core.react_loop import _inject_image_urls
        messages = [{"role": "user", "content": "普通文本消息"}]
        out, _skipped = _inject_image_urls(messages)
        assert out[0]["content"] == "普通文本消息"

    def test_missing_file_keeps_text(self, tmp_path) -> None:
        from private_agent.core.react_loop import _inject_image_urls
        messages = [{
            "role": "user",
            "content": f"[已上传文件: gone.png 路径: {tmp_path}/gone.png] 描述",
        }]
        out, _skipped = _inject_image_urls(messages)
        # 文件不存在 → 原样保留(不注入也不崩溃)
        assert isinstance(out[0]["content"], str)
        assert "gone.png" in out[0]["content"]

    def test_non_image_extension_ignored(self, tmp_path) -> None:
        from private_agent.core.react_loop import _inject_image_urls
        f = tmp_path / "doc.zip"
        f.write_bytes(b"PK")
        messages = [{
            "role": "user",
            "content": f"[已上传文件: doc.zip 路径: {f}] 解压",
        }]
        out, _skipped = _inject_image_urls(messages)
        assert isinstance(out[0]["content"], str)

    def test_historical_image_ref_ignored_for_pure_text_turn(self) -> None:
        """2026-08-10 误判修复: 历史 user 含图引用 + 本轮纯文本 → 不触发 vision。"""
        from private_agent.core.react_loop import _messages_contain_image
        messages = [
            {"role": "user", "content": "[已上传文件: a.png 路径: /tmp/a.png] 识别"},
            {"role": "assistant", "content": "已识别"},
            {"role": "user", "content": "把这些持仓记录下来"},
        ]
        assert _messages_contain_image(messages) is False

    def test_last_user_image_ref_triggers(self) -> None:
        from private_agent.core.react_loop import _messages_contain_image
        messages = [
            {"role": "user", "content": "旧文本"},
            {"role": "user", "content": "[已上传文件: b.png 路径: /tmp/b.png]"},
        ]
        assert _messages_contain_image(messages) is True

    def test_injected_image_url_in_history_keeps_vision(self) -> None:
        """历史已注入 image_url(list) → 即使本轮纯文本仍保持 vision(上下文有图)。"""
        from private_agent.core.react_loop import _messages_contain_image
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "识别"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
            ]},
            {"role": "user", "content": "继续"},
        ]
        assert _messages_contain_image(messages) is True

    def test_skipped_oversize_image_reported(self, tmp_path) -> None:
        """2026-08-10 错误零静默: 超限图片返回 skipped 而非静默。"""
        from private_agent.core.react_loop import _inject_image_urls
        big = tmp_path / "big.png"
        big.write_bytes(b"\x89PNG" + b"\x00" * (9 * 1024 * 1024))  # ~9MB
        messages = [{
            "role": "user",
            "content": f"[已上传文件: big.png 路径: {big}]",
        }]
        _out, skipped = _inject_image_urls(messages)
        assert any("big.png" in s and "超上限" in s for s in skipped)
        assert isinstance(_out[0]["content"], str)  # 原文本保留

    def test_skipped_missing_file_reported(self, tmp_path) -> None:
        from private_agent.core.react_loop import _inject_image_urls
        messages = [{
            "role": "user",
            "content": f"[已上传文件: gone.png 路径: {tmp_path}/gone.png]",
        }]
        _out, skipped = _inject_image_urls(messages)
        assert any("gone.png" in s and "文件不存在" in s for s in skipped)


class TestToolLoopDetection:
    """0.5.1: 死循环检测 —— 批量记忆(同工具不同参数)不误判。"""

    def _detector(self):
        from private_agent.core.react_loop import ReactLoop
        loop = ReactLoop.__new__(ReactLoop)
        loop._tool_call_trace = []
        loop._loop_same_args_threshold = 3
        loop._loop_same_tool_threshold = 5
        return loop

    def test_batch_memory_different_args_not_loop(self):
        """批量记忆 8 只股票(同工具、参数各异) → 不判死循环。"""
        d = self._detector()
        result = None
        for i in range(8):
            args = {"content": f"持仓: 股票{i} 市值 {i * 1000}", "scope": "data_analysis"}
            result = d._detect_tool_loop("memory_save", args)
            import json as _json
            d._tool_call_trace.append(
                f"memory_save:{_json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]}"
            )
        assert result is None

    def test_same_args_repeated_is_loop(self):
        d = self._detector()
        args = {"content": "相同内容", "scope": "global"}
        result = None
        for _ in range(4):
            result = d._detect_tool_loop("memory_save", args)
            import json as _json
            d._tool_call_trace.append(
                f"memory_save:{_json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]}"
            )
        assert result == "same_args"

    def test_same_tool_few_arg_variants_is_loop(self):
        """同工具高频但参数只有 1-2 种 → 仍判 same_tool(真正无进展)。"""
        d = self._detector()
        args_list = [{"content": f"重试{i % 2}", "scope": "global"} for i in range(6)]
        result = None
        import json as _json
        for a in args_list:
            result = d._detect_tool_loop("web_search", a)
            d._tool_call_trace.append(
                f"web_search:{_json.dumps(a, sort_keys=True, ensure_ascii=False)[:200]}"
            )
        assert result is not None


class TestToolResultTrim:
    """0.5.1(P2-A): 超长工具结果 API 侧截断, 事实型保留。"""

    def _loop(self):
        from private_agent.core.react_loop import ReactLoop
        return ReactLoop.__new__(ReactLoop)

    def test_long_non_factual_tool_result_trimmed(self):
        loop = self._loop()
        long_text = "普通文本" * 3001  # >12000 chars
        messages = [{"role": "tool", "content": long_text}]
        out = loop._trim_tool_results(messages)
        assert "已截断" in out[0]["content"]
        assert len(out[0]["content"]) < len(long_text)

    def test_factual_tool_result_kept_verbatim(self):
        loop = self._loop()
        # 表格(事实型)超长不截断
        table = "股票|市值|盈亏\n" + "\n".join(f"股{i}|{i*1000}|+{i}" for i in range(400))
        messages = [{"role": "tool", "content": table}]
        out = loop._trim_tool_results(messages)
        assert "已截断" not in out[0]["content"]
        assert out[0]["content"] == table

    def test_non_tool_messages_untouched(self):
        loop = self._loop()
        messages = [{"role": "user", "content": "x" * 20000}]
        out = loop._trim_tool_results(messages)
        assert out[0]["content"] == "x" * 20000


# ──────────────────────────────────────────────────────────────────────────────
# 2026-08-16(阶段2 反馈): 迭代上限询问继续/停止
# ──────────────────────────────────────────────────────────────────────────────


def test_run_turn_limit_continue_via_method():
    """超限询问后调用 continue_iterations() → 扩展上限继续执行。

    模拟用户选择"继续": run_turn 挂起时并发调用 continue_iterations,
    循环应扩展 10 步继续, 直至响应耗尽。
    """
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
                    *[
                        ChatResult(
                            content="",
                            tool_calls=[_echo_tool_call(f"call_{i}", "x")],
                            used_provider="mock",
                        )
                        for i in range(14)
                    ],
                    # 最后: 正常 content, 让循环自然结束(避免 mock 耗尽异常)
                    ChatResult(content="done", used_provider="mock"),
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[ECHO_TOOL],
                conn=conn,
                max_iterations=5,
                # 挂起等待用户决定(不自动超时继续) —— 由下面并发调用决定
                limit_confirm_timeout=30,
                # 关闭 Doom Loop 检测(echo 同参工具会误触发, 干扰上限询问验证)
                cfg={"context": {"loop": {"enabled": False}}},
            )
            task = asyncio.create_task(loop.run_turn("loop"))
            # 等待 loop 挂起在询问点(轮询 _limit_pending)
            for _ in range(200):
                if loop._limit_pending:
                    break
                await asyncio.sleep(0.02)
            assert loop._limit_pending, "loop 应挂起在迭代上限询问"
            loop.continue_iterations()  # 模拟用户点"继续"
            await task
            return len(adapter.chat_calls)
        finally:
            await conn.close()

    calls = asyncio.run(_run())
    # max=5 到达后继续扩展 → 调用数应超过 5(上限已扩展继续执行)
    assert calls > 5, f"continue 后应继续执行, 实际 {calls}"


def test_run_turn_limit_stop_via_method():
    """超限询问后调用 stop_iteration() → 正常收尾(不当作失败)。

    模拟用户选择"停止": loop 应退出, 不产出 ERROR 事件。
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
                max_iterations=3,
                limit_confirm_timeout=30,
                # 关闭 Doom Loop 检测(echo 同参工具会误触发, 干扰上限询问验证)
                cfg={"context": {"loop": {"enabled": False}}},
            )
            task = asyncio.create_task(loop.run_turn("loop"))
            for _ in range(200):
                if loop._limit_pending:
                    break
                await asyncio.sleep(0.02)
            assert loop._limit_pending, "loop 应挂起在迭代上限询问"
            loop.stop_iteration()  # 模拟用户点"停止"
            await task
            events = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return [e["event_type"] for e in events], loop.state
        finally:
            await conn.close()

    types, final_state = asyncio.run(_run())
    assert "iteration_limit_reached" in types
    # 停止 → 正常收尾(IDLE), 不进入 ERROR
    assert final_state != ReactLoopState.ERROR
