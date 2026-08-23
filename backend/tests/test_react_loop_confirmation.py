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


async def _create_session(conn: "asyncpg.Connection") -> int:
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

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
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

    async def _run() -> tuple[list[dict], ReactLoopState, int]:
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
            return events, loop.state, session_id
        finally:
            await conn.close()

    events, state, session_id = asyncio.run(_run())
    # handler 收到 args(含 react_loop 注入的 _on_output 流式回调)
    # 2026-08-16: ReactLoop 对 code_execution 强制注入 session_id(会话真实
    # id, 沙箱按会话隔离目录) —— 覆盖测试传入的 "s1", 断言以注入值为准。
    assert calls == [{"code": "print(1)", "session_id": str(session_id), "_on_output": calls[0]["_on_output"]}]
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


def test_timeout_tool_message_written_after_assistant_in_db():
    """权限超时后 tool 消息必须在 assistant 之后写入(DB id 顺序)。

    回归测试 2026-08-08: Phase A 中权限超时/拒绝曾直接 append_tool_message,
    导致 tool 消息 id < assistant 消息 id → get_messages 按 (turn, id) 排序后
    tool 出现在 assistant 之前 → DeepSeek API 400 配对错误。
    修复: Phase A 收集 early_tool_msgs, Phase C 事务中 assistant 之后统一写入。
    """
    _setup_schema()
    calls: list[dict] = []
    pm = PermissionManager(timeout=0.05)

    async def _run() -> dict:
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
            await loop.run_turn("run code")
            # 查 DB: 同轮 assistant 和 tool 消息的 id 顺序
            rows = await conn.fetch(
                "SELECT id, role, tool_call_id FROM messages "
                "WHERE session_id=$1 AND role IN ('assistant','tool') "
                "ORDER BY id",
                session_id,
            )
            return {"rows": [dict(r) for r in rows]}
        finally:
            await conn.close()

    result = asyncio.run(_run())
    rows = result["rows"]
    # 应有: assistant(tool_calls) → tool(timeout error)
    assert len(rows) >= 2
    roles = [r["role"] for r in rows]
    # 第一个必须是 assistant(在 tool 之前)
    assert roles[0] == "assistant", (
        f"assistant must be written before tool, got order: {roles}"
    )
    # 至少有一个 tool 在 assistant 之后
    assert "tool" in roles[1:], (
        f"tool must appear after assistant, got: {roles}"
    )


def test_repair_tool_pairing_drops_orphan_tool():
    """_repair_tool_pairing 应移除缺少前置 assistant.tool_calls 的孤儿 tool 消息。

    场景: tool 消息的 tool_call_id 不在已遍历的 assistant 消息中
    (Phase A 时序 bug / 压缩打破配对 / DB 恢复残留)。

    孤儿 tool 被移除后, 其对应的 assistant(后置)缺 tool 响应 → 补占位。
    最终: assistant(call_X) 后面跟着占位 tool(call_X), 而非孤儿 tool 在前。
    """
    from private_agent.core.react_loop import ReactLoop

    class _MockLoop:
        _logger = type("_L", (), {"warning": staticmethod(lambda *a, **k: None)})()
        _repair_tool_pairing = ReactLoop._repair_tool_pairing

    # 构造孤儿序列: tool(call_X) 在 assistant(call_X) 之前
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_A", "type": "function", "function": {"name": "foo", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_A", "content": "result A"},
        # 孤儿: call_X 的 assistant 在后面(时序 bug 场景)
        {"role": "tool", "tool_call_id": "call_X", "content": "orphan"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_X", "type": "function", "function": {"name": "bar", "arguments": "{}"}}
        ]},
    ]
    fixed = _MockLoop()._repair_tool_pairing(messages)
    # 找 assistant(call_X) 的位置
    asst_x_idx = None
    for i, m in enumerate(fixed):
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("id") == "call_X":
                    asst_x_idx = i
                    break
    assert asst_x_idx is not None, "assistant(call_X) should be in fixed list"
    # 孤儿 tool(call_X) 应被移除(不在 assistant 之前)
    for i, m in enumerate(fixed):
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_X":
            assert i > asst_x_idx, (
                f"tool(call_X) must appear AFTER assistant(call_X) "
                f"(idx {i} > {asst_x_idx}), got: {[(m.get('role'), m.get('tool_call_id')) for m in fixed]}"
            )
    # call_A 的 tool 应保留(有前置 assistant)
    tool_ids = [m.get("tool_call_id") for m in fixed if m.get("role") == "tool"]
    assert "call_A" in tool_ids, f"valid tool call_A should be kept, got: {tool_ids}"


def test_repair_tool_pairing_adds_missing_tool_response():
    """_repair_tool_pairing 应为缺少 tool 响应的 assistant 补占位 tool 消息。"""
    from private_agent.core.react_loop import ReactLoop

    class _MockLoop:
        _logger = type("_L", (), {"warning": staticmethod(lambda *a, **k: None)})()
        _repair_tool_pairing = ReactLoop._repair_tool_pairing

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_A", "type": "function", "function": {"name": "foo", "arguments": "{}"}}
        ]},
        # 没有 tool 响应!
    ]
    fixed = _MockLoop()._repair_tool_pairing(messages)
    # 末尾应补一条占位 tool 消息
    assert fixed[-1]["role"] == "tool"
    assert fixed[-1]["tool_call_id"] == "call_A"
