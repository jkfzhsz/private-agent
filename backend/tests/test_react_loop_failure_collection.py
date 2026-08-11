"""Phase 3 Task 3.2 - ReactLoop 集成失败案例采集测试。

验证:
- 工具执行失败(handler 异常) → 采集 TOOL_ERROR
- 迭代用尽(max_iterations) → 采集 ITERATION_EXHAUSTED
- 模型调用全失败(AllProvidersFailedError) → 采集 PROVIDER_ERROR
- 正常完成不采集; 未注入 failure_collector 时零回归
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.eval.online_failure_collector import FailureType
from private_agent.models.base import AllProvidersFailedError, ChatResult, ModelCapability
from private_agent.storage import migrations
from private_agent.tools.defs import ToolDef, ToolResult

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
        "test-failure-collect",
        "mock-glm",
        locked_skill_name,
    )


class _MockAdapter:
    """mock 适配器: 按 responses 列表依次返回(模式同 test_react_loop_reflection)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list | None = None) -> None:
        self._responses = list(responses) if responses else None
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        r = self._responses[self._idx]
        self._idx += 1
        return r


class _FailingAdapter:
    """mock 适配器: chat 恒抛 AllProvidersFailedError(模拟模型全失败)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        raise AllProvidersFailedError("all providers failed: connection error")


async def _echo_handler(args: dict) -> ToolResult:
    return ToolResult(output=str(args.get("text", "")))


async def _boom_handler(args: dict) -> ToolResult:
    raise RuntimeError("boom: 工具内部错误")


def _make_tool(name: str, handler) -> ToolDef:
    return ToolDef(
        name=name,
        description=f"{name} 工具",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
        handler=handler,
    )


def _tool_call(tool_name: str) -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": '{"text": "hi"}',
        },
    }


def _extract_call(mock_collector: AsyncMock) -> dict:
    call = mock_collector.collect.await_args
    assert call is not None
    return call.kwargs if call.kwargs else {"args": call.args}


def test_tool_error_triggers_collection():
    """工具执行失败(handler 抛异常) → 采集 TOOL_ERROR。"""
    _setup_schema()
    tools = [_make_tool("boom_test", _boom_handler)]

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=tools)
            await cm.build_initial(conn)

            collector = AsyncMock()
            collector.collect = AsyncMock(return_value=1)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(responses=[
                    ChatResult(content="", tool_calls=[_tool_call("boom_test")], used_provider="mock"),
                    ChatResult(content="已处理", used_provider="mock"),
                ]),
                tools=tools,
                conn=conn,
                failure_collector=collector,
            )
            await loop.run_turn("执行会失败的工具")
            return {
                "collect_called": collector.collect.await_count,
                "call_kwargs": _extract_call(collector),
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["collect_called"] == 1
    assert info["call_kwargs"]["failure_type"] == FailureType.TOOL_ERROR
    assert "boom_test" in info["call_kwargs"]["failure_detail"]
    assert info["call_kwargs"]["scope"] == "office"
    assert info["call_kwargs"]["session_id"] == 1


def test_iteration_exhausted_triggers_collection():
    """迭代用尽(max_iterations) → 采集 ITERATION_EXHAUSTED。"""
    _setup_schema()
    tools = [_make_tool("echo_test", _echo_handler)]

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=tools)
            await cm.build_initial(conn)

            collector = AsyncMock()
            collector.collect = AsyncMock(return_value=2)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(responses=[
                    ChatResult(content="", tool_calls=[_tool_call("echo_test")], used_provider="mock"),
                    ChatResult(content="", tool_calls=[_tool_call("echo_test")], used_provider="mock"),
                    ChatResult(content="", tool_calls=[_tool_call("echo_test")], used_provider="mock"),
                ]),
                tools=tools,
                conn=conn,
                max_iterations=3,
                failure_collector=collector,
            )
            await loop.run_turn("复杂任务")
            return {
                "collect_called": collector.collect.await_count,
                "call_kwargs": _extract_call(collector),
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["collect_called"] == 1
    assert info["call_kwargs"]["failure_type"] == FailureType.ITERATION_EXHAUSTED
    assert "迭代" in info["call_kwargs"]["failure_detail"]


def test_provider_error_triggers_collection():
    """模型调用全失败(AllProvidersFailedError) → 采集 PROVIDER_ERROR。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)

            collector = AsyncMock()
            collector.collect = AsyncMock(return_value=3)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_FailingAdapter(),
                tools=[],
                conn=conn,
                failure_collector=collector,
            )
            await loop.run_turn("你好")
            return {
                "collect_called": collector.collect.await_count,
                "call_kwargs": _extract_call(collector),
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["collect_called"] == 1
    assert info["call_kwargs"]["failure_type"] == FailureType.PROVIDER_ERROR
    assert "all providers failed" in info["call_kwargs"]["failure_detail"]


def test_normal_final_skips_collection():
    """正常完成(final 分支)不采集; 未注入 failure_collector 时零回归。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)

            collector = AsyncMock()
            collector.collect = AsyncMock(return_value=4)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=_MockAdapter(responses=[
                    ChatResult(content="你好，我是助手", used_provider="mock"),
                ]),
                tools=[],
                conn=conn,
                failure_collector=collector,
            )
            await loop.run_turn("你好")
            return {"collect_called": collector.collect.await_count}
        finally:
            await conn.close()

    info = asyncio.run(_run())
    assert info["collect_called"] == 0
