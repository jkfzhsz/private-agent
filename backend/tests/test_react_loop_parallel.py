"""V2 P2 - ReactLoop 同轮多 tool_call 并行执行。

验证:
- 同轮多个独立工具调用并行执行(总耗时 < 串行累加)
- 单工具 handler 异常不中断整轮(其余工具照常执行, 错误以 error 回传)
- tool_result 事件按模型原始 tool_calls 顺序产出
- 全局信号量限流(config tools.mcp.concurrent_limit)下全部完成
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop, ReactLoopState
from private_agent.models.base import ChatResult, ModelCapability
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


async def _create_session(conn: "asyncpg.Connection") -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-parallel",
        "mock-glm",
    )


class _MockAdapter:
    """mock 适配器: 返回预设 tool_calls → 最终回复。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, tool_call_names: list[str], extra_args: dict | None = None) -> None:
        self._names = list(tool_call_names)
        self._extra = extra_args or {}
        self.chat_calls = 0

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        self.chat_calls += 1
        if self.chat_calls == 1:
            tcs = [
                {
                    "id": f"call_{i}",
                    "function": {
                        "name": name,
                        "arguments": json.dumps({"text": name}),
                    },
                }
                for i, name in enumerate(self._names)
            ]
            return ChatResult(content="", tool_calls=tcs, used_provider="mock")
        return ChatResult(content="all done", used_provider="mock")


def _sleep_tool(name: str, delay: float) -> ToolDef:
    """sleep delay 秒后回显的工具(模拟耗时 MCP 调用)。"""
    async def _handler(args: dict) -> ToolResult:
        await asyncio.sleep(delay)
        return ToolResult(output=f"{name}:ok")

    return ToolDef(
        name=name,
        description=f"sleep {delay}s tool",
        parameters_schema={
            "type": "object", "properties": {"text": {"type": "string"}},
        },
        handler=_handler,
    )


def _fail_tool(name: str) -> ToolDef:
    """直接抛异常的失败工具。"""
    async def _handler(args: dict) -> ToolResult:
        raise RuntimeError(f"{name} exploded")

    return ToolDef(
        name=name,
        description="always fails",
        parameters_schema={
            "type": "object", "properties": {"text": {"type": "string"}},
        },
        handler=_handler,
    )


def _run(adapter, tools, cfg=None) -> tuple[list[dict], float, ReactLoopState]:
    async def _run_async() -> tuple[list[dict], float, ReactLoopState]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=tools
            )
            await cm.build_initial(conn)
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=tools,
                conn=conn,
                cfg=cfg,
            )
            start = time.monotonic()
            await loop.run_turn("parallel please")
            elapsed = time.monotonic() - start
            events: list[dict] = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, elapsed, loop.state
        finally:
            await conn.close()

    return asyncio.run(_run_async())


def _tool_results(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event_type"] == "tool_result"]


def test_parallel_tool_calls_faster_than_serial():
    """同轮 2 个 0.25s 工具并行执行, 总耗时 < 0.45s(串行需 0.5s+)。"""
    _setup_schema()
    tools = [_sleep_tool("t_slow_a", 0.25), _sleep_tool("t_slow_b", 0.25)]
    adapter = _MockAdapter(["t_slow_a", "t_slow_b"])

    events, elapsed, state = _run(adapter, tools)

    assert state == ReactLoopState.IDLE
    assert elapsed < 0.45, f"并行执行耗时 {elapsed:.2f}s, 疑似串行"
    results = _tool_results(events)
    assert len(results) == 2
    outputs = [r["payload"]["output"] for r in results]
    assert "t_slow_a:ok" in outputs and "t_slow_b:ok" in outputs
    # 事件顺序: 原始 tool_calls 顺序(a 先于 b)
    assert [r["payload"]["tool_name"] for r in results] == ["t_slow_a", "t_slow_b"]


def test_single_tool_failure_does_not_break_turn():
    """单工具 handler 异常 → 该工具 error 回传, 其余照常, 循环继续到 final。"""
    _setup_schema()
    tools = [
        _fail_tool("t_bad"),
        _sleep_tool("t_good", 0.05),
    ]
    adapter = _MockAdapter(["t_bad", "t_good"])

    events, _, state = _run(adapter, tools)

    assert state == ReactLoopState.IDLE
    results = _tool_results(events)
    assert len(results) == 2
    by_name = {r["payload"]["tool_name"]: r for r in results}
    assert by_name["t_bad"]["payload"]["error"] is not None
    assert by_name["t_good"]["payload"]["error"] is None
    assert by_name["t_good"]["payload"]["output"] == "t_good:ok"
    event_types = [e["event_type"] for e in events]
    assert event_types.count("final") >= 1


def test_concurrent_limit_all_complete():
    """信号量并发上限(默认 5)下, 5 个工具全部执行完成。"""
    _setup_schema()
    tools = [_sleep_tool(f"t_c{i}", 0.05) for i in range(5)]
    adapter = _MockAdapter([t.name for t in tools])

    events, _, state = _run(adapter, tools)

    assert state == ReactLoopState.IDLE
    assert len(_tool_results(events)) == 5
