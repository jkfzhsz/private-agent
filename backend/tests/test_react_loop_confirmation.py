"""V2 P1 - ReactLoop 集成 PermissionManager(确认流程接通对话链)。

验证:
- elevated 工具: 确认通过 → 执行 handler → tool_result 正常产出
- 确认拒绝 → ToolResult(error=...) 回传模型, 循环继续(不中断)
- 确认超时 → 同样以 error 回传, 循环继续
- 缓存命中: 同轮内再次调用同参数不重复确认
"""
from __future__ import annotations

import asyncio
import json
import os

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop, ReactLoopState
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import migrations
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.permission_manager import PermissionManager

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
        "test-confirmation",
        "mock-glm",
    )


class _MockAdapter:
    """mock 适配器: 返回预设 tool_calls → 最终回复。

    tool_call_args 每项为参数字典(真实 API 中 arguments 是 JSON 字符串,
    这里用 json.dumps 序列化以贴近真实格式)。
    """

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, tool_call_args: list[dict]) -> None:
        self._tool_call_args = list(tool_call_args)
        self.chat_calls: list[list[dict]] = []

    async def chat(self, messages, tools=None, max_tokens=None) -> ChatResult:
        self.chat_calls.append(list(messages))
        if self._tool_call_args:
            args = self._tool_call_args.pop(0)
            tc = {
                "id": f"call_{len(self.chat_calls)}",
                "function": {"name": "code_execution", "arguments": json.dumps(args)},
            }
            return ChatResult(
                content=None,
                tool_calls=[tc],
                used_provider="mock",
            )
        return ChatResult(content="done", used_provider="mock")


def _elevated_tool(calls: list) -> ToolDef:
    """elevated 沙箱工具: 记录调用并返回结果。"""
    async def _handler(args: dict) -> ToolResult:
        calls.append(args)
        return ToolResult(output="executed:" + args.get("code", ""))

    return ToolDef(
        name="code_execution",
        description="sandbox exec",
        parameters_schema={"type": "object", "properties": {"code": {"type": "string"}}},
        handler=_handler,
        safety_level="elevated",
    )


def test_confirmed_tool_executes_and_emits_result():
    """确认通过 → handler 执行 → tool_result 产出。"""
    _setup_schema()
    calls: list[dict] = []
    pm = PermissionManager(timeout=2.0)

    # 手动驱动: 在 ReactLoop 等待时自动批准 —— 用后台 task 探测 pending
    async def _driver() -> None:
        await asyncio.sleep(0.05)
        for cid in list(pm._pending):
            pm.resolve(cid, True)

    async def _run() -> tuple[list[dict], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys",
                tools=[_elevated_tool(calls)],
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter([{"code": "print(1)", "session_id": "s1"}])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[_elevated_tool(calls)],
                conn=conn,
                permission_manager=pm,
            )
            driver = asyncio.create_task(_driver())
            await loop.run_turn("run code")
            driver.cancel()
            events: list[dict] = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, loop.state
        finally:
            await conn.close()

    events, state = asyncio.run(_run())
    # handler 收到 args(含 react_loop 注入的 _on_output 流式回调)
    assert calls == [{"code": "print(1)", "session_id": "s1", "_on_output": calls[0]["_on_output"]}]
    assert "_on_output" in calls[0]  # 流式回调已注入
    event_types = [e["event_type"] for e in events]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    # tool_result 是成功输出
    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["output"] == "executed:print(1)"
    assert tr["payload"]["error"] is None
    assert state == ReactLoopState.IDLE


def test_denied_tool_returns_error_and_loop_continues():
    """确认拒绝 → error 回传模型 → 循环继续(下一轮模型直接 final)。"""
    _setup_schema()
    calls: list[dict] = []
    pm = PermissionManager(timeout=2.0)

    async def _driver() -> None:
        await asyncio.sleep(0.05)
        for cid in list(pm._pending):
            pm.resolve(cid, False)

    async def _run() -> tuple[list[dict], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            tools = [_elevated_tool(calls)]
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=tools
            )
            await cm.build_initial(conn)
            # 第一轮 tool_calls → 拒绝 → 第二轮无 tool_calls 直接 final
            adapter = _MockAdapter([{"code": "print(1)"}])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=tools,
                conn=conn,
                permission_manager=pm,
            )
            driver = asyncio.create_task(_driver())
            await loop.run_turn("run code")
            driver.cancel()
            events: list[dict] = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, loop.state
        finally:
            await conn.close()

    events, state = asyncio.run(_run())
    assert calls == []  # handler 未执行
    event_types = [e["event_type"] for e in events]
    assert "tool_confirmation_result" in event_types
    # 拒绝结果作为 tool_result(error) 回传 → 模型据此收尾
    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["error"] is not None
    assert "denied" in tr["payload"]["error"].lower() or "拒绝" in tr["payload"]["error"]
    # 循环继续 → 最终 final
    assert event_types.count("final") >= 1
    assert state == ReactLoopState.IDLE


def test_confirmation_timeout_returns_error():
    """确认 60s 超时(测试短超时) → error 回传 → 循环继续。"""
    _setup_schema()
    calls: list[dict] = []
    pm = PermissionManager(timeout=0.05)  # 短超时模拟 60s

    async def _run() -> tuple[list[dict], ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            tools = [_elevated_tool(calls)]
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=tools
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter([{"code": "print(1)"}])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=tools,
                conn=conn,
                permission_manager=pm,
            )
            await loop.run_turn("run code")  # 无人响应 → 自动超时
            events: list[dict] = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, loop.state
        finally:
            await conn.close()

    events, state = asyncio.run(_run())
    assert calls == []
    tr = next(e for e in events if e["event_type"] == "tool_result")
    assert tr["payload"]["error"] is not None
    assert "timeout" in tr["payload"]["error"].lower() or "超时" in tr["payload"]["error"]
    assert state == ReactLoopState.IDLE
