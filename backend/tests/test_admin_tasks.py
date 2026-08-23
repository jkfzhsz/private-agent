"""V1.1-3.8 任务状态反馈测试。

覆盖:
- GET /admin/tasks?session_id= 从 react_events 聚合轮次状态
- 404 不存在会话
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
async def test_tasks_aggregation(client, schema):
    """按轮聚合 thinking/tool_call/tool_result/error。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        await conn.execute(
            """
            INSERT INTO react_events (session_id, turn, event_type, payload) VALUES
            ($1, 1, 'thinking', '{}'::jsonb),
            ($1, 1, 'tool_call', '{}'::jsonb),
            ($1, 1, 'tool_result', '{}'::jsonb),
            ($1, 2, 'thinking', '{}'::jsonb),
            ($1, 2, 'error', '{"message": "boom"}'::jsonb)
            """,
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get("/admin/tasks", params={"session_id": sid})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert data["total_turns"] == 2
    turns = {t["turn"]: t for t in data["turns"]}
    assert turns[1]["events"]["tool_call"] == 1
    assert turns[1]["events"]["tool_result"] == 1
    assert turns[2]["events"]["error"] == 1
    assert "boom" in turns[2]["error"]


@pytest.mark.asyncio
async def test_tasks_missing_session_404(client, schema):
    """不存在会话 → 404。"""
    resp = await client.get("/admin/tasks", params={"session_id": 999999})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_session_events_log(client, schema):
    """GET /admin/events: 事件时间线(倒序 + 摘要) + 404。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        await conn.execute(
            """
            INSERT INTO react_events (session_id, turn, event_type, payload) VALUES
            ($1, 1, 'thinking', '{"content": "分析中"}'::jsonb),
            ($1, 1, 'tool_call', '{"tool_name": "file_read", "arguments": "{}"}'::jsonb),
            ($1, 1, 'tool_result', '{"output": "ok"}'::jsonb)
            """,
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get("/admin/events", params={"session_id": sid})
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 3
    # 倒序: 最新在前
    assert events[0]["event_type"] == "tool_result"
    assert "ok" in events[0]["summary"]
    assert all("ts" in e for e in events)

    resp = await client.get("/admin/events", params={"session_id": 999999})
    assert resp.status_code == 404
