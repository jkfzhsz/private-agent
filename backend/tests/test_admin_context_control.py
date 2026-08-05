"""V1.1-3.5 上下文可控测试。

覆盖:
- POST /admin/sessions/{id}/truncate: soft-delete after_turn 之后消息(archive 副本 + compressed)
- PUT /admin/sessions/{id}/memory-enabled: 会话级记忆开关
- GET /admin/sessions/{id}/system-prompt: 组装后完整提示词
"""
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture
async def schema():
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
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_truncate_soft_deletes_after_turn(client, schema):
    """truncate: after_turn 之后消息入 archive + compressed 标记; 之前的保留。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', 'turn0-q', 0, 'active'), "
            "($1, 'assistant', 'turn0-a', 0, 'active'), "
            "($1, 'user', 'turn1-q', 1, 'active'), "
            "($1, 'assistant', 'turn1-a', 1, 'active')",
            sid,
        )
    finally:
        await conn.close()

    resp = await client.post(f"/admin/sessions/{sid}/truncate", json={"after_turn": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated_messages"] == 2  # turn=1 的两条

    conn = await asyncpg.connect(TEST_DSN)
    try:
        compressed = await conn.fetch(
            "SELECT content FROM messages WHERE session_id = $1 AND compressed = TRUE ORDER BY id",
            sid,
        )
        assert [r["content"] for r in compressed] == ["turn1-q", "turn1-a"]
        kept = await conn.fetch(
            "SELECT content FROM messages WHERE session_id = $1 AND compressed = FALSE ORDER BY id",
            sid,
        )
        assert [r["content"] for r in kept] == ["turn0-q", "turn0-a"]
        # archive 副本
        arch = await conn.fetch(
            "SELECT content FROM messages_archive WHERE session_id = $1 ORDER BY id",
            sid,
        )
        assert [r["content"] for r in arch] == ["turn1-q", "turn1-a"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_truncate_invalid_and_missing(client, schema):
    """负数 after_turn → 400; 不存在会话 → 404。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()

    resp = await client.post(f"/admin/sessions/{sid}/truncate", json={"after_turn": -1})
    assert resp.status_code == 400

    resp = await client.post("/admin/sessions/999999/truncate", json={"after_turn": 0})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_memory_enabled_toggle(client, schema):
    """记忆开关默认开; 关闭后 DB 记录 false; 不存在会话 404。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        default_val = await conn.fetchval(
            "SELECT memory_enabled FROM sessions WHERE id = $1", sid
        )
        assert default_val is True
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}/memory-enabled", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["memory_enabled"] is False

    conn = await asyncpg.connect(TEST_DSN)
    try:
        assert await conn.fetchval(
            "SELECT memory_enabled FROM sessions WHERE id = $1", sid
        ) is False
    finally:
        await conn.close()

    resp = await client.put("/admin/sessions/999999/memory-enabled", json={"enabled": True})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_system_prompt_endpoint(client, schema, monkeypatch):
    """GET system-prompt: 返回组装后提示词(未激活会话 → 默认提示词)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()

    # 延迟 import main 会依赖 MCP manager 等全局状态 —— monkeypatch 掉
    import private_agent.main as main_mod

    async def _fake_get_system_prompt(cfg, session_id, conn_):
        return "custom-assembled-prompt"

    monkeypatch.setattr(main_mod, "_get_system_prompt", _fake_get_system_prompt)

    resp = await client.get(f"/admin/sessions/{sid}/system-prompt")
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == "custom-assembled-prompt"

    resp = await client.get("/admin/sessions/999999/system-prompt")
    assert resp.status_code == 404
