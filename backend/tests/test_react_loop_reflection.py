"""Phase 1 Task 1.3 - ReactLoop 集成反思环节(REFLECTION)测试。

验证:
- 正常 final 分支: 有场景(locked_skill_name)时反思并沉淀经验
- reflection_engine 未注入(None)时零回归(不调用)
- 会话无场景(locked_skill_name IS NULL)时不反思
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop, ReactLoopState
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import migrations

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


async def _create_session(
    conn: "asyncpg.Connection", *, locked_skill_name: str | None = "office"
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO sessions (title, model_id, locked_skill_name)
        VALUES ($1, $2, $3) RETURNING id
        """,
        "test-reflection",
        "mock-glm",
        locked_skill_name,
    )


class _MockAdapter:
    """mock 适配器: 直接返回 final 文本(单轮无工具)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, content: str = "已完成任务", responses: list | None = None) -> None:
        self._content = content
        self._responses = list(responses) if responses else None
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        if self._responses is not None:
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return ChatResult(content=self._content, used_provider="mock")


@pytest.fixture
def mock_reflection() -> AsyncMock:
    engine = AsyncMock()
    engine.reflect = AsyncMock(return_value=None)
    return engine


def test_final_branch_triggers_reflection_with_scope():
    """正常 final 分支: 有场景时反思并调用 evolution_repo.add。"""
    _setup_schema()
    result_payload = {
        "scope": "office",
        "lesson_category": "domain_skill",
        "task_summary": "清洗销售数据",
        "lesson_type": "success",
        "lesson_content": "先检查dtype",
        "tool_chain": ["file_read"],
        "importance": 0.8,
    }

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn, locked_skill_name="office")
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)

            reflection = AsyncMock()
            reflection.reflect = AsyncMock(return_value=_ReflectionResultStub(**result_payload))
            evolution_repo = AsyncMock()
            evolution_repo.add = AsyncMock(return_value=1)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(),
                tools=[],
                conn=conn,
                reflection_engine=reflection,
                evolution_repo=evolution_repo,
            )
            await loop.run_turn("帮我清洗销售数据")
            return {
                "state": loop.state,
                "reflect_called": reflection.reflect.called,
                "add_called": evolution_repo.add.called,
                "reflect_scope": (
                    reflection.reflect.call_args.kwargs.get("scope")
                    if reflection.reflect.called else None
                ),
                "reflect_had_error": (
                    reflection.reflect.call_args.kwargs.get("had_error")
                    if reflection.reflect.called else None
                ),
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["state"] == ReactLoopState.IDLE
    assert info["reflect_called"] is True
    assert info["add_called"] is True
    assert info["reflect_scope"] == "office"
    assert info["reflect_had_error"] is False


def test_reflection_receives_production_tool_chain():
    """回归(2026-08-11 dev-code-review P0): 真实 ReactLoop 的 tool_call 事件
    payload 使用生产键 tool_name, 反思引擎据此才能提取 tool_chain。"""
    _setup_schema()
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry

    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "calculator",
            "arguments": '{"expression": "2+3"}',
        },
    }

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn, locked_skill_name="office")
            registry = ToolRegistry()
            register_all_builtins(registry)
            tools = registry.list_tools()

            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=tools)
            await cm.build_initial(conn)

            reflection = AsyncMock()
            reflection.reflect = AsyncMock(return_value=None)
            evolution_repo = AsyncMock()

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(responses=[
                    ChatResult(content="", tool_calls=[tool_call], used_provider="mock"),
                    ChatResult(content="5", used_provider="mock"),
                ]),
                tools=tools,
                conn=conn,
                reflection_engine=reflection,
                evolution_repo=evolution_repo,
            )
            await loop.run_turn("calculate 2+3")
            return {
                "reflect_called": reflection.reflect.called,
                "react_events": (
                    reflection.reflect.call_args.kwargs.get("react_events")
                    if reflection.reflect.called else []
                ),
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["reflect_called"] is True
    tool_calls = [
        ev for ev in info["react_events"]
        if ev.get("event_type") == "tool_call"
    ]
    assert tool_calls, "反思应收到真实 tool_call 事件"
    assert tool_calls[0]["payload"]["tool_name"] == "calculator"


def test_no_reflection_when_engine_not_injected():
    """reflection_engine 未注入(None)时零回归: 不调用反思。"""
    _setup_schema()

    async def _run() -> ReactLoopState:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(),
                tools=[],
                conn=conn,
            )
            await loop.run_turn("你好")
            return loop.state
        finally:
            await conn.close()

    state = asyncio.run(_run())
    assert state == ReactLoopState.IDLE


def test_no_reflection_without_scope():
    """会话无场景(locked_skill_name IS NULL)时跳过反思。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn, locked_skill_name=None)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)

            reflection = AsyncMock()
            reflection.reflect = AsyncMock(return_value=None)
            evolution_repo = AsyncMock()

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(),
                tools=[],
                conn=conn,
                reflection_engine=reflection,
                evolution_repo=evolution_repo,
            )
            await loop.run_turn("帮我清洗数据")
            return {
                "reflect_called": reflection.reflect.called,
                "add_called": evolution_repo.add.called,
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["reflect_called"] is False
    assert info["add_called"] is False


class _ReflectionResultStub:
    """模拟 ReflectionResult 的简单对象(避免 import 环)。"""

    def __init__(self, **kwargs) -> None:
        self.scope = kwargs.get("scope", "office")
        self.lesson_category = kwargs.get("lesson_category", "domain_skill")
        self.task_summary = kwargs.get("task_summary", "")
        self.lesson_type = kwargs.get("lesson_type", "success")
        self.lesson_content = kwargs.get("lesson_content", "")
        self.tool_chain = kwargs.get("tool_chain", [])
        self.importance = kwargs.get("importance", 0.5)
