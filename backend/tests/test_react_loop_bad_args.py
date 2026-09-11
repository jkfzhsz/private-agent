"""ReactLoop 工具参数 JSON 解析容错(session-78067 中断修复)。

背景: 2026-09-10 session-78067, LLM 偶发输出非法 JSON 工具参数
(缺逗号, JSONDecodeError char 120), react_loop.py 原实现 json.loads
无容错 → 异常沿 run_turn 冒泡 → main.py 外层 except → user_message_failed
整轮崩溃, 对话中断需用户重发消息才恢复。

修复: Phase A 解析参数时 try/except, 解析失败降级为单工具 error 回传
模型(复用 unknown tool / 权限拒绝的 early_tool_msgs 模式), 不中断整轮。

本测试验证:
- 非法 JSON arguments → 单工具 error 降级, 整轮不崩
- 同轮兄弟工具不受牵连, 照常执行
- 轮次正常走完(final 事件产出)
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
        "test-bad-args",
        "mock-glm",
    )


class _MockAdapter:
    """mock 适配器: 第一轮返回预设 tool_calls(可注入非法 JSON 参数)→ 最终回复。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, tool_calls: list[dict]) -> None:
        self._tcs = tool_calls
        self.chat_calls = 0

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        self.chat_calls += 1
        if self.chat_calls == 1:
            return ChatResult(
                content="", tool_calls=list(self._tcs), used_provider="mock"
            )
        return ChatResult(content="all done", used_provider="mock")


def _echo_tool(name: str) -> ToolDef:
    """回显参数的工具。"""

    async def _handler(args: dict) -> ToolResult:
        return ToolResult(output=f"{name}:{args.get('text', '')}")

    return ToolDef(
        name=name,
        description=f"echo {name}",
        parameters_schema={
            "type": "object", "properties": {"text": {"type": "string"}},
        },
        handler=_handler,
    )


def _run(adapter, tools, cfg=None) -> tuple[list[dict], ReactLoopState, int]:
    async def _run_async() -> tuple[list[dict], ReactLoopState, int]:
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
            await loop.run_turn("call tools please")
            events: list[dict] = []
            while not loop.event_queue.empty():
                events.append(loop.event_queue.get_nowait())
            return events, loop.state, adapter.chat_calls
        finally:
            await conn.close()

    return asyncio.run(_run_async())


def _tool_results(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event_type"] == "tool_result"]


def _tc(name: str, arguments_raw: str, call_id: str = "call_0") -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": arguments_raw},
    }


def test_bad_json_args_isolated_not_breaking_turn():
    """非法 JSON 参数 → 单工具 error 降级, 兄弟工具照常, 整轮走完。"""
    _setup_schema()
    tools = [_echo_tool("t_bad"), _echo_tool("t_good")]
    # t_bad 参数非法(缺逗号), t_good 参数合法
    adapter = _MockAdapter([
        _tc("t_bad", '{"text": "oops" "missing_comma": 1}'),
        _tc("t_good", json.dumps({"text": "fine"}), call_id="call_1"),
    ])

    events, state, chat_calls = _run(adapter, tools)

    # ① 整轮不崩, 状态回到 IDLE
    assert state == ReactLoopState.IDLE
    # ② 模型得到 error 后可重试(至少 2 次 chat: 首轮 tool_calls + 重试完成)
    assert chat_calls >= 2
    # ③ t_bad 被降级为 error, t_good 正常执行
    results = _tool_results(events)
    by_name = {r["payload"]["tool_name"]: r for r in results}
    assert "t_bad" in by_name
    assert by_name["t_bad"]["payload"]["error"] is not None
    assert "JSON parse failed" in by_name["t_bad"]["payload"]["error"]
    assert "t_good" in by_name
    assert by_name["t_good"]["payload"]["error"] is None
    assert by_name["t_good"]["payload"]["output"] == "t_good:fine"
    # ④ final 事件产出
    assert [e["event_type"] for e in events].count("final") >= 1


def test_all_bad_json_args_turn_still_completes():
    """全部参数非法 → 全部降级 error, 轮次仍走完(不崩溃)。"""
    _setup_schema()
    tools = [_echo_tool("t_a"), _echo_tool("t_b")]
    adapter = _MockAdapter([
        _tc("t_a", '{"text": }', call_id="call_0"),
        _tc("t_b", 'not json at all', call_id="call_1"),
    ])

    events, state, chat_calls = _run(adapter, tools)

    assert state == ReactLoopState.IDLE
    assert chat_calls >= 2
    results = _tool_results(events)
    assert len(results) == 2
    for r in results:
        assert r["payload"]["error"] is not None
        assert "JSON parse failed" in r["payload"]["error"]
    assert [e["event_type"] for e in events].count("final") >= 1
