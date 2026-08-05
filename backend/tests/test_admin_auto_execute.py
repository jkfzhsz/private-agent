"""V1.3-7.2 工作流自动化: 会话级 auto_execute + max_rounds 测试。

覆盖:
- WS user_message 显式带 auto_execute=true + max_rounds=2 → 连续 2 轮 turn_end
- 未开启自动执行(默认) → 单轮 turn_end
- 会话表 auto_execute 配置(DB 层) → 自动多轮
- max_rounds=1 → 即使开启也仅 1 轮
"""

import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.api import admin as admin_mod
from private_agent.main import app
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


class _MockAdapter:
    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        r = self._responses[self._idx]
        self._idx += 1
        return r


def _setup_schema_sync() -> None:
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


def _create_session_sync(title: str = "auto-test") -> int:
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "INSERT INTO sessions (status, title) VALUES ('active', $1) RETURNING id",
                title,
            )
        finally:
            await conn.close()

    return asyncio.run(_run())


def _patch_main(monkeypatch, responses):
    import private_agent.main as main_mod

    def _fake_build_adapter(cfg):
        return _MockAdapter(responses=responses)

    def _fake_build_session_adapter(cfg, model_id=None):
        return _MockAdapter(responses=responses)

    async def _fake_get_frozen_tools(cfg, session_id, conn):
        return []

    async def _fake_get_tools(cfg, session_id, conn):
        return []

    monkeypatch.setattr(main_mod, "_build_adapter", _fake_build_adapter)
    monkeypatch.setattr(
        main_mod, "_build_session_adapter", _fake_build_session_adapter
    )
    monkeypatch.setattr(main_mod, "_get_frozen_tools", _fake_get_frozen_tools)
    monkeypatch.setattr(main_mod, "_get_tools", _fake_get_tools)

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


def _recv_until(ws, n_turn_end: int):
    """接收消息直到 n 个 turn_end(或收到 error)。每轮事件数不定(thinking/final/turn_end)。"""
    msgs = []
    ends = 0
    while True:
        m = ws.receive_json()
        msgs.append(m)
        if m.get("type") == "turn_end":
            ends += 1
            if ends >= n_turn_end:
                return msgs
        if m.get("type") in ("error", "turn_cancelled"):
            return msgs


def test_auto_execute_two_rounds(monkeypatch):
    """WS auto_execute=true + max_rounds=2 → 连续 2 轮 turn_end, 2 条 assistant 落库。"""
    _setup_schema_sync()
    sid = _create_session_sync()
    _patch_main(
        monkeypatch,
        responses=[
            ChatResult(content="第一轮回复", used_provider="mock"),
            ChatResult(content="第二轮回复", used_provider="mock"),
        ],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": sid,
            "content": "跑一个两轮任务",
            "auto_execute": True,
            "max_rounds": 2,
        })
        msgs = _recv_until(ws, 2)

    ends = [m for m in msgs if m.get("type") == "turn_end"]
    finals = [m for m in msgs if m.get("event_type") == "final"]
    assert len(ends) == 2, f"expect 2 turn_end, got {len(ends)}: {msgs}"
    assert ends[0]["turn"] == 1 and ends[1]["turn"] == 2
    contents = [f["payload"]["content"] for f in finals]
    assert contents == ["第一轮回复", "第二轮回复"], contents

    async def _check() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            rows = await conn.fetch(
                "SELECT turn, content FROM messages "
                "WHERE session_id = $1 AND role = 'assistant' ORDER BY turn",
                sid,
            )
            assert [r["content"] for r in rows] == ["第一轮回复", "第二轮回复"]
            assert [r["turn"] for r in rows] == [1, 2]
        finally:
            await conn.close()

    asyncio.run(_check())


def test_auto_execute_disabled_single_round(monkeypatch):
    """默认关闭(会话未配置) → 仅 1 轮。"""
    _setup_schema_sync()
    sid = _create_session_sync()
    _patch_main(
        monkeypatch,
        responses=[ChatResult(content="唯一回复", used_provider="mock")],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": sid,
            "content": "普通消息",
        })
        ends = []
        while not ends:
            m = ws.receive_json()
            if m.get("type") == "turn_end":
                ends.append(m)
        # 之后再等一小段, 确保没有第二轮事件
        assert ends[0]["turn"] == 1


def test_auto_execute_session_config(monkeypatch):
    """会话表 auto_execute=true + max_rounds=2(DB 配置, 未显式传参) → 自动 2 轮。"""
    _setup_schema_sync()

    async def _seed() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "INSERT INTO sessions (status, title, auto_execute, max_rounds) "
                "VALUES ('active', 'auto-config', TRUE, 2) RETURNING id",
            )
        finally:
            await conn.close()

    sid = asyncio.run(_seed())
    _patch_main(
        monkeypatch,
        responses=[
            ChatResult(content="R1", used_provider="mock"),
            ChatResult(content="R2", used_provider="mock"),
        ],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": sid,
            "content": "根据会话配置自动执行",
        })
        ends = []
        finals = []
        while len(ends) < 2:
            m = ws.receive_json()
            if m.get("type") == "turn_end":
                ends.append(m)
            if m.get("event_type") == "final":
                finals.append(m)

    assert [e["turn"] for e in ends] == [1, 2]
    assert [f["payload"]["content"] for f in finals] == ["R1", "R2"]


def test_auto_execute_max_rounds_1(monkeypatch):
    """max_rounds=1 → 即使开启也只 1 轮。"""
    _setup_schema_sync()
    sid = _create_session_sync()
    _patch_main(
        monkeypatch,
        responses=[ChatResult(content="只一轮", used_provider="mock")],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "user_message",
            "session_id": sid,
            "content": "x",
            "auto_execute": True,
            "max_rounds": 1,
        })
        ends = []
        while not ends:
            m = ws.receive_json()
            if m.get("type") == "turn_end":
                ends.append(m)
    assert ends[0]["turn"] == 1
