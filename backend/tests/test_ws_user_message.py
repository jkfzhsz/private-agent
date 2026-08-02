"""M1 Phase 4 Behavior 4-3 - WS user_message 消息处理 (AC-1)。

Source: plan/m1-react-loop Phase 4 (蓝图 §2.4/§2.6 + §9.4 AC-1)

客户端发 {type:"user_message", session_id, content}:
- 服务端创建 ContextManager + ReactLoop(用 mock adapter)
- 执行 run_turn(content)
- 从 event_queue 取出所有 react_event,通过 ws.send_json 推送
- 推送完成后发送 {type:"turn_end", session_id, turn}
- 异常时返回 {type:"error", message:"..."}

测试用真实 DB + mock adapter(main._build_adapter / main._get_tools 可 monkeypatch)。
"""
import asyncio
import os

import asyncpg
from fastapi.testclient import TestClient

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.main import app
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations
from private_agent.tools.defs import ECHO_TOOL

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


def _create_session() -> int:
    """创建一个 session,返回 id。"""
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
                "ws-user-msg-test", "mock-glm",
            )
        finally:
            await conn.close()

    return asyncio.run(_run())


def _patch_db_connect(monkeypatch) -> None:
    """让 main 中使用的 db.connect 返回指向 TEST_DSN 的真实连接。"""
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


class _MockAdapter:
    """测试用 mock 适配器,返回预设 ChatResult。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        r = self._responses[self._idx]
        self._idx += 1
        return r


def _patch_adapter_and_tools(monkeypatch, responses, tools):
    """注入 mock adapter + tools 到 main 模块。

    注1: 会话级模型选择(1e0af49)后 user_message 走 _build_session_adapter,
    需同时 patch 它(否则 model_id=mock-glm 解析到真实 adapter → 空 base_url 报错)。
    注2: user_message 路径实际调用 _get_frozen_tools(c49ca95 起), 测试工具
    (如 ECHO_TOOL)不在内置白名单, 必须 patch _get_frozen_tools 返回测试工具,
    否则 ReactLoop _find_tool 找不到 → 误报 unknown tool。
    """
    import private_agent.main as main_mod

    def _fake_build_adapter(cfg):
        return _MockAdapter(responses=responses)

    def _fake_build_session_adapter(cfg, model_id=None):
        return _MockAdapter(responses=responses)

    async def _fake_get_frozen_tools(cfg, session_id, conn):
        return list(tools)

    async def _fake_get_tools(cfg, session_id, conn):
        return list(tools)

    monkeypatch.setattr(main_mod, "_build_adapter", _fake_build_adapter)
    monkeypatch.setattr(
        main_mod, "_build_session_adapter", _fake_build_session_adapter
    )
    monkeypatch.setattr(
        main_mod, "_get_frozen_tools", _fake_get_frozen_tools
    )
    monkeypatch.setattr(main_mod, "_get_tools", _fake_get_tools)


def _recv_until_turn_end(ws) -> list[dict]:
    """从 WS 接收消息直到收到 turn_end,返回所有消息(含 turn_end)。"""
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg.get("type") == "turn_end":
            break
        if msg.get("type") == "error":
            break
    return messages


# ──────────────────────────────────────────────────────────────────────────────
# user_message 无 tool_calls:收到 thinking + final + turn_end
# ──────────────────────────────────────────────────────────────────────────────


def test_user_message_no_tool_calls_produces_thinking_and_final(monkeypatch):
    """user_message 触发 ReactLoop,客户端收到 thinking + final + turn_end(无 tool_calls)。"""
    _setup_schema()
    session_id = _create_session()
    _patch_db_connect(monkeypatch)
    _patch_adapter_and_tools(
        monkeypatch,
        responses=[ChatResult(content="hello world", used_provider="mock")],
        tools=[],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": session_id,
            "content": "hi",
        })
        messages = _recv_until_turn_end(ws)

    event_types = [m["event_type"] for m in messages if m["type"] == "react_event"]
    assert event_types == ["thinking", "final"], (
        f"expected [thinking, final], got {event_types}"
    )
    # turn_end 收尾
    assert messages[-1]["type"] == "turn_end"
    assert messages[-1]["session_id"] == session_id
    assert messages[-1]["turn"] == 1
    # thinking event 携带 LLM 输出
    thinking = next(m for m in messages if m.get("event_type") == "thinking")
    assert thinking["payload"]["content"] == "hello world"
    # final event 携带最终回复
    final = next(m for m in messages if m.get("event_type") == "final")
    assert final["payload"]["content"] == "hello world"


# ──────────────────────────────────────────────────────────────────────────────
# user_message 有 tool_calls:收到 thinking + tool_call + tool_result + final + turn_end
# ──────────────────────────────────────────────────────────────────────────────


def test_user_message_with_tool_calls_produces_four_events(monkeypatch):
    """user_message 触发 ReactLoop,客户端收到 thinking→tool_call→tool_result→final + turn_end。"""
    _setup_schema()
    session_id = _create_session()
    _patch_db_connect(monkeypatch)

    echo_tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "echo",
            "arguments": '{"text": "hi"}',
        },
    }
    _patch_adapter_and_tools(
        monkeypatch,
        responses=[
            ChatResult(
                content="",
                tool_calls=[echo_tool_call],
                used_provider="mock",
            ),
            ChatResult(content="echo said: hi", used_provider="mock"),
        ],
        tools=[ECHO_TOOL],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": session_id,
            "content": "please echo hi",
        })
        messages = _recv_until_turn_end(ws)

    event_types = [m["event_type"] for m in messages if m["type"] == "react_event"]
    assert event_types == ["thinking", "tool_call", "tool_result", "final"], (
        f"expected 4 event types in order, got {event_types}"
    )
    assert messages[-1]["type"] == "turn_end"
    assert messages[-1]["session_id"] == session_id
    assert messages[-1]["turn"] == 1
    # tool_call event 含工具名
    tool_call_evt = next(m for m in messages if m.get("event_type") == "tool_call")
    assert tool_call_evt["payload"]["tool_name"] == "echo"
    # tool_result event 含工具输出(echo 回显 "hi")
    tool_result_evt = next(m for m in messages if m.get("event_type") == "tool_result")
    assert tool_result_evt["payload"]["output"] == "hi"


# ──────────────────────────────────────────────────────────────────────────────
# user_message session_id 缺失 → error
# ──────────────────────────────────────────────────────────────────────────────


def test_user_message_missing_session_id_returns_error(monkeypatch):
    """user_message 消息缺 session_id → 返回 error,不断开连接。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)
    _patch_adapter_and_tools(monkeypatch, responses=[], tools=[])

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        msg = ws.receive_json()
        # 连接仍可用
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
    assert msg["type"] == "error"
    assert "session_id" in msg["message"]
    assert pong == {"type": "pong"}


def test_user_message_non_int_session_id_returns_error(monkeypatch):
    """user_message 消息 session_id 非整数 → 返回 error。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)
    _patch_adapter_and_tools(monkeypatch, responses=[], tools=[])

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": "abc",
            "content": "hi",
        })
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "session_id" in msg["message"]


# ──────────────────────────────────────────────────────────────────────────────
# V2 P1: 权限确认端到端(user_message → confirmation_required → tool_confirmation → tool_result)
# ──────────────────────────────────────────────────────────────────────────────


def _elevated_stub_tool(calls: list) -> "ToolDef":
    """elevated 沙箱 stub 工具: 记录调用并返回结果(不真跑子进程)。"""
    from private_agent.tools.defs import ToolDef, ToolResult

    async def _handler(args: dict) -> ToolResult:
        calls.append(args)
        return ToolResult(output="sandbox-ran:" + args.get("code", ""))

    return ToolDef(
        name="code_execution",
        description="sandbox exec",
        parameters_schema={"type": "object", "properties": {"code": {"type": "string"}}},
        handler=_handler,
        safety_level="elevated",
    )


def test_user_message_confirmation_flow(monkeypatch):
    """V2 P1: elevated 工具 → WS 推 confirmation_required → 客户端批准 → 执行 → tool_result。

    验证 B2 修复: create_task 非阻塞, run_turn 等待确认期间 WS 主循环
    仍能接收 tool_confirmation 消息。
    """
    _setup_schema()
    session_id = _create_session()
    _patch_db_connect(monkeypatch)
    calls: list = []
    elevated_tool = _elevated_stub_tool(calls)

    tool_call = {
        "id": "call_c1",
        "type": "function",
        "function": {
            "name": "code_execution",
            "arguments": '{"code": "print(1)"}',
        },
    }
    _patch_adapter_and_tools(
        monkeypatch,
        responses=[
            ChatResult(content="", tool_calls=[tool_call], used_provider="mock"),
            ChatResult(content="done after exec", used_provider="mock"),
        ],
        tools=[elevated_tool],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": session_id,
            "content": "run code",
        })
        messages = []
        confirmed = False
        while True:
            msg = ws.receive_json()
            messages.append(msg)
            if (
                msg.get("type") == "react_event"
                and msg.get("event_type") == "tool_confirmation_required"
            ):
                # 客户端批准确认(核心: 主循环未被 run_turn 阻塞)
                ws.send_json({
                    "type": "tool_confirmation",
                    "session_id": session_id,
                    "confirmation_id": msg["payload"]["confirmation_id"],
                    "approved": True,
                })
                confirmed = True
            if msg.get("type") in ("turn_end", "error"):
                break

    assert confirmed, "must receive tool_confirmation_required"
    event_types = [m["event_type"] for m in messages if m["type"] == "react_event"]
    assert "tool_confirmation_required" in event_types
    assert "tool_result" in event_types
    tr = next(m for m in messages if m.get("event_type") == "tool_result")
    assert tr["payload"]["error"] is None
    # handler 已执行(权限通过后调用)
    assert calls and calls[0]["code"] == "print(1)"
    assert messages[-1]["type"] == "turn_end"
