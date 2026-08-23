"""画地为牢: 工作区选择功能测试(会话级工作区 API)。

覆盖:
- GET /admin/workspaces 返回候选目录 + 默认工作区
- PUT /sessions/{id}/workspace 设置会话工作区
- PUT 非法路径(不存在) → 400
- PUT 空/None → 清除工作区
"""
import os
from pathlib import Path

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
def client(tmp_path, monkeypatch):
    """ASGI 客户端 + db.connect 指向测试库。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def session_id(client):
    """建 schema + 一个测试会话。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
        sid = await conn.fetchval(
            "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
        )
        return sid
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_list_workspaces_returns_candidates(client):
    """GET /admin/workspaces 返回候选 + 默认。"""
    resp = await client.get("/admin/workspaces")
    assert resp.status_code == 200
    data = resp.json()
    assert "workspaces" in data
    assert "default" in data
    assert isinstance(data["workspaces"], list)


@pytest.mark.asyncio
async def test_set_session_workspace(client, session_id, tmp_path):
    """PUT 设置会话工作区 → 入库可查。"""
    ws = str(tmp_path)
    resp = await client.put(
        f"/admin/sessions/{session_id}/workspace",
        json={"workspace": ws},
    )
    assert resp.status_code == 200
    assert resp.json()["workspace"] == ws
    conn = await asyncpg.connect(TEST_DSN)
    try:
        stored = await conn.fetchval(
            "SELECT workspace FROM sessions WHERE id=$1", session_id
        )
    finally:
        await conn.close()
    assert stored == ws


@pytest.mark.asyncio
async def test_set_session_workspace_invalid_path(client, session_id):
    """PUT 不存在的路径 → 400。"""
    resp = await client.put(
        f"/admin/sessions/{session_id}/workspace",
        json={"workspace": "Z:/definitely/not/exists-xyz"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "workspace_invalid"


@pytest.mark.asyncio
async def test_set_session_workspace_clear(client, session_id, tmp_path):
    """PUT 空 → 清除工作区。"""
    await client.put(
        f"/admin/sessions/{session_id}/workspace",
        json={"workspace": str(tmp_path)},
    )
    resp = await client.put(
        f"/admin/sessions/{session_id}/workspace",
        json={"workspace": None},
    )
    assert resp.status_code == 200
    assert resp.json()["workspace"] is None
    conn = await asyncpg.connect(TEST_DSN)
    try:
        stored = await conn.fetchval(
            "SELECT workspace FROM sessions WHERE id=$1", session_id
        )
    finally:
        await conn.close()
    assert stored is None


@pytest.mark.asyncio
async def test_set_session_workspace_not_found(client):
    """PUT 不存在的会话 → 404。"""
    resp = await client.put(
        "/admin/sessions/999999/workspace",
        json={"workspace": "D:/"},
    )
    assert resp.status_code == 404
