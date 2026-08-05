"""V1.2-6.3 任务链路监控测试。

覆盖:
- GET /admin/usage: token_usage 事件聚合(总 token/成本/按会话)
- GET /admin/errors/summary: 错误事件聚合(top + samples)
- GET /admin/logs: 日志尾部
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
async def test_usage_aggregation(client, schema):
    """usage: 聚合 token_usage 事件(总量 + 按会话)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        await conn.execute(
            """
            INSERT INTO react_events (session_id, turn, event_type, payload) VALUES
            ($1, 1, 'token_usage', '{"model_id":"m1","total_tokens":100,"input_tokens":60,"output_tokens":40,"cost":0.01,"currency":"CNY"}'::jsonb),
            ($1, 2, 'token_usage', '{"model_id":"m1","total_tokens":200,"input_tokens":120,"output_tokens":80,"cost":0.02,"currency":"CNY"}'::jsonb)
            """,
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get("/admin/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_calls"] == 2
    assert data["total_tokens"] == 300
    assert data["input_tokens"] == 180
    assert data["output_tokens"] == 120
    assert data["total_cost"] == pytest.approx(0.03, abs=1e-6)
    assert data["currency"] == "CNY"
    assert len(data["by_session"]) == 1
    assert data["by_session"][0]["session_id"] == sid

    # 按会话过滤
    resp = await client.get("/admin/usage", params={"session_id": sid})
    assert resp.status_code == 200
    assert resp.json()["total_calls"] == 2

    resp = await client.get("/admin/usage", params={"session_id": 999999})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_error_summary(client, schema):
    """errors/summary: 聚合 + top + samples。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        await conn.execute(
            """
            INSERT INTO react_events (session_id, turn, event_type, payload) VALUES
            ($1, 1, 'error', '{"message":"boom"}'::jsonb),
            ($1, 2, 'error', '{"message":"boom"}'::jsonb),
            ($1, 3, 'tool_error', '{"error":"tool failed"}'::jsonb)
            """,
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get("/admin/errors/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_errors"] == 3
    assert data["distinct_errors"] == 2
    top = {t["message"]: t["count"] for t in data["top"]}
    assert "boom" in top and top["boom"] == 2
    assert len(data["samples"]) > 0


@pytest.mark.asyncio
async def test_logs_tail(client, schema, tmp_path):
    """logs: 返回日志尾部(不存在时空列表)。"""
    resp = await client.get("/admin/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert "lines" in data
    assert isinstance(data["lines"], list)
