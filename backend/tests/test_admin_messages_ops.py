"""V1.1-3.3 消息精细化操作测试。

覆盖:
- PUT /admin/messages/{id}/starred 收藏/取消收藏
- DELETE /admin/messages/{id} 软删除(archive 副本 + compressed 标记)
- WS regenerate: 按 message_id 找到同 turn user 消息重放, 产生新 turn

注意: pyproject asyncio_mode=auto —— async 测试禁 asyncio.run;
同步 WS 测试(TestClient 阻塞式)才允许 asyncio.run。
"""
import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.main import app
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等: 每次测试独立)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
def client(monkeypatch):
    """ASGI 客户端 + db.connect 指向测试库(HTTP 端点用)。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _insert_message(role: str, content: str, turn: int = 0, session_id: int | None = None) -> tuple[int, int]:
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = session_id
        if sid is None:
            sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        mid = await conn.fetchval(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, $2, $3, $4, 'active') RETURNING id",
            sid, role, content, turn,
        )
        return sid, mid
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# starred 收藏
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_starred_toggle(client, schema):
    """收藏/取消收藏。"""
    _, mid = await _insert_message("user", "hi")
    resp = await client.put(f"/admin/messages/{mid}/starred", json={"starred": True})
    assert resp.status_code == 200
    assert resp.json()["starred"] is True

    conn = await asyncpg.connect(TEST_DSN)
    try:
        assert await conn.fetchval("SELECT starred FROM messages WHERE id = $1", mid) is True
    finally:
        await conn.close()

    resp = await client.put(f"/admin/messages/{mid}/starred", json={"starred": False})
    assert resp.status_code == 200
    assert resp.json()["starred"] is False


@pytest.mark.asyncio
async def test_starred_missing_404(client, schema):
    """starred 不存在消息 → 404。"""
    resp = await client.put("/admin/messages/999999/starred", json={"starred": True})
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# soft-delete 软删除
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_message_soft(client, schema):
    """软删除: archive 副本 + 原消息 compressed 标记(排除出上下文)。"""
    _, mid = await _insert_message("assistant", "待删除回复")

    resp = await client.delete(f"/admin/messages/{mid}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT compressed, compressed_from FROM messages WHERE id = $1", mid
        )
        assert row["compressed"] is True
        assert "deleted_at" in (row["compressed_from"] or {})
        arch = await conn.fetchrow(
            "SELECT * FROM messages_archive WHERE original_msg_id = $1", mid
        )
        assert arch is not None
        assert arch["content"] == "待删除回复"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_delete_missing_404(client, schema):
    """删除不存在消息 → 404。"""
    resp = await client.delete("/admin/messages/999999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_turn_messages(client, schema):
    """按 turn 查询消息(供前端收藏/删除定位 msg_id)。"""
    sid, _ = await _insert_message("user", "q")
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone, starred) "
            "VALUES ($1, 'assistant', 'a1', 0, 'active', TRUE)",
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get(f"/admin/sessions/{sid}/turn/0/messages")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert {m["role"] for m in items} == {"user", "assistant"}
    assistant = next(m for m in items if m["role"] == "assistant")
    assert assistant["starred"] is True

    resp = await client.get("/admin/sessions/999999/turn/0/messages")
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# WS regenerate 重生成(同步 TestClient, 允许 asyncio.run)
# ──────────────────────────────────────────────────────────────────────────────

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


def _seed_messages(titles: list[str]) -> tuple[int, int, int]:
    """预置会话 + user/assistant 消息, 返回 (sid, uid, aid)。"""

    async def _run() -> tuple[int, int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (status, title) VALUES ('active', $1) RETURNING id",
                titles[0],
            )
            uid = await conn.fetchval(
                "INSERT INTO messages (session_id, role, content, turn, zone) "
                "VALUES ($1, 'user', $2, 0, 'active') RETURNING id",
                sid, titles[1],
            )
            aid = await conn.fetchval(
                "INSERT INTO messages (session_id, role, content, turn, zone) "
                "VALUES ($1, 'assistant', '旧回复', 0, 'active') RETURNING id",
                sid,
            )
            return sid, uid, aid
        finally:
            await conn.close()

    return asyncio.run(_run())


def _patch_main(monkeypatch, responses, tools):
    """注入 mock adapter + tools 到 main(照 test_ws_user_message 模式)。"""
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
    monkeypatch.setattr(main_mod, "_build_session_adapter", _fake_build_session_adapter)
    monkeypatch.setattr(main_mod, "_get_frozen_tools", _fake_get_frozen_tools)
    monkeypatch.setattr(main_mod, "_get_tools", _fake_get_tools)

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


def _recv_until_turn_end(ws):
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg.get("type") in ("turn_end", "error"):
            break
    return messages


def test_regenerate_replays_user_message(monkeypatch):
    """WS regenerate: 按 turn 找到 user 消息重放 → 新 turn。"""
    _setup_schema_sync()
    sid, uid, aid = _seed_messages(["regenerate-test", "原始问题"])
    _patch_main(
        monkeypatch,
        responses=[ChatResult(content="重新生成的回复", used_provider="mock")],
        tools=[],
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "regenerate",
            "session_id": sid,
            "turn": 0,  # 预置消息在 turn 0
        })
        messages = _recv_until_turn_end(ws)

    assert messages[-1]["type"] == "turn_end", f"last={messages[-1]}"
    assert messages[-1]["turn"] == 1
    finals = [m for m in messages if m.get("event_type") == "final"]
    assert finals and finals[0]["payload"]["content"] == "重新生成的回复"

    # 新 turn 落库: user 消息内容 = 原始问题, turn=1
    async def _check() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            new_user = await conn.fetchval(
                "SELECT content FROM messages WHERE session_id = $1 AND turn = 1 AND role = 'user'",
                sid,
            )
            assert new_user == "原始问题"
        finally:
            await conn.close()

    asyncio.run(_check())


def test_regenerate_missing_turn_returns_error(monkeypatch):
    """regenerate 目标 turn 无 user 消息 → error。"""
    _setup_schema_sync()
    sid = _create_session_sync("regenerate-test3")
    _patch_main(monkeypatch, responses=[ChatResult(content="x", used_provider="mock")], tools=[])

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({
            "type": "regenerate",
            "session_id": sid,
            "turn": 9,
        })
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "regenerate_failed" in msg["message"]


def _create_session_sync(title: str) -> int:
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
