"""V1.1-3.1 会话管理闭环测试。

覆盖:
- POST /admin/sessions 新建会话
- PUT /admin/sessions/{id} 重命名(title)
- PUT /admin/sessions/{id} 归档/取消归档(status + archived_at)
- PUT /admin/sessions/{id}/folder 设置/清除文件夹
- GET /admin/sessions?folder= 文件夹过滤(unfiled=未分组)
- 非法 status → 400; 不存在会话 → 404
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
async def schema(client):
    """建 schema + 返回连接辅助。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_create_session(client, schema):
    """POST /admin/sessions 新建 → 返回 id, 且空 body 可创建。"""
    resp = await client.post("/admin/sessions", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["id"] > 0

    resp2 = await client.post("/admin/sessions", json={"title": "我的会话"})
    assert resp2.status_code == 200
    assert resp2.json()["id"] > data["id"]


@pytest.mark.asyncio
async def test_rename_session(client, schema):
    """PUT 重命名 title。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}", json={"title": "新标题"})
    assert resp.status_code == 200

    conn = await asyncpg.connect(TEST_DSN)
    try:
        title = await conn.fetchval("SELECT title FROM sessions WHERE id = $1", sid)
        assert title == "新标题"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_archive_and_restore_session(client, schema):
    """归档 → status=archived + archived_at 非空; 恢复 → active + archived_at NULL。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}", json={"status": "archived"})
    assert resp.status_code == 200

    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow("SELECT status, archived_at FROM sessions WHERE id = $1", sid)
        assert row["status"] == "archived"
        assert row["archived_at"] is not None
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}", json={"status": "active"})
    assert resp.status_code == 200

    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow("SELECT status, archived_at FROM sessions WHERE id = $1", sid)
        assert row["status"] == "active"
        assert row["archived_at"] is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_invalid_status_rejected(client, schema):
    """非法 status → 400。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}", json={"status": "bogus"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_set_and_clear_folder(client, schema):
    """设置文件夹 → 清除文件夹(NULL)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}/folder", json={"folder": "工作"})
    assert resp.status_code == 200
    assert resp.json()["folder"] == "工作"

    conn = await asyncpg.connect(TEST_DSN)
    try:
        folder = await conn.fetchval("SELECT folder FROM sessions WHERE id = $1", sid)
        assert folder == "工作"
    finally:
        await conn.close()

    resp = await client.put(f"/admin/sessions/{sid}/folder", json={"folder": ""})
    assert resp.status_code == 200

    conn = await asyncpg.connect(TEST_DSN)
    try:
        folder = await conn.fetchval("SELECT folder FROM sessions WHERE id = $1", sid)
        assert folder is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_list_sessions_folder_filter(client, schema):
    """GET /admin/sessions?folder= 过滤; unfiled=未分组; 返回含 folder 字段。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid_a = await conn.fetchval("INSERT INTO sessions (status, folder) VALUES ('active', '工作') RETURNING id")
        sid_b = await conn.fetchval("INSERT INTO sessions (status, folder) VALUES ('active', '生活') RETURNING id")
        sid_c = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        # 各补一条 user 消息使 has_messages 过滤逻辑可对比
        for sid in (sid_a, sid_b, sid_c):
            await conn.execute(
                "INSERT INTO messages (session_id, role, content, turn, zone) "
                "VALUES ($1, 'user', 'hello', 0, 'active')",
                sid,
            )
    finally:
        await conn.close()

    resp = await client.get("/admin/sessions?folder=工作&has_messages=false")
    assert resp.status_code == 200
    items = resp.json()
    assert all(s["folder"] == "工作" for s in items)
    assert any(s["id"] == sid_a for s in items)

    resp = await client.get("/admin/sessions?folder=unfiled&has_messages=false")
    assert resp.status_code == 200
    items = resp.json()
    assert all(s["folder"] is None for s in items)
    assert any(s["id"] == sid_c for s in items)

    resp = await client.get("/admin/sessions?has_messages=false")
    assert resp.status_code == 200
    folders = {s["id"]: s["folder"] for s in resp.json()}
    assert folders[sid_a] == "工作"
    assert folders[sid_c] is None


@pytest.mark.asyncio
async def test_update_missing_session_404(client, schema):
    """更新不存在的会话 → 404。"""
    resp = await client.put("/admin/sessions/999999", json={"title": "x"})
    assert resp.status_code == 404

    resp = await client.put("/admin/sessions/999999/folder", json={"folder": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_sessions_by_content(client, schema):
    """搜索: 消息全文命中(含归档会话)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid_active = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
        sid_archived = await conn.fetchval("INSERT INTO sessions (status, archived_at) VALUES ('archived', now()) RETURNING id")
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', '帮我写一个量子纠缠的报告', 0, 'active')",
            sid_active,
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', '量子计算基础笔记', 0, 'active')",
            sid_archived,
        )
    finally:
        await conn.close()

    resp = await client.get("/admin/sessions/search", params={"q": "量子"})
    assert resp.status_code == 200
    hits = resp.json()
    ids = {h["id"] for h in hits}
    assert sid_active in ids
    assert sid_archived in ids  # 归档会话也命中
    assert all(h["hit_snippet"] for h in hits)


@pytest.mark.asyncio
async def test_search_sessions_by_title(client, schema):
    """搜索: 标题命中 + 空 q 返回空。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.fetchval(
            "INSERT INTO sessions (status, title) VALUES ('active', '周报 2026 复盘') RETURNING id"
        )
    finally:
        await conn.close()

    resp = await client.get("/admin/sessions/search", params={"q": "周报"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = await client.get("/admin/sessions/search", params={"q": "  "})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_export_session_md(client, schema):
    """导出 md: 含角色分节与内容。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval(
            "INSERT INTO sessions (status, title) VALUES ('active', '导出测试') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', '你好', 0, 'active'), "
            "($1, 'assistant', '你好!有什么可以帮你', 0, 'active')",
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get(f"/admin/sessions/{sid}/export", params={"format": "md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "md"
    assert "导出测试" in data["content"]
    assert "## 用户" in data["content"]
    assert "## 私人智能体" in data["content"]
    assert "你好" in data["content"]


@pytest.mark.asyncio
async def test_export_session_json(client, schema):
    """导出 json: 完整结构。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval(
            "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', 'hi', 0, 'active')",
            sid,
        )
    finally:
        await conn.close()

    resp = await client.get(f"/admin/sessions/{sid}/export", params={"format": "json"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "json"
    assert data["content"]["meta"]["id"] == sid
    assert len(data["content"]["messages"]) == 1
    assert data["content"]["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_export_invalid_format_and_missing(client, schema):
    """无效 format → 400; 不存在会话 → 404。"""
    resp = await client.get("/admin/sessions/999999/export", params={"format": "md"})
    assert resp.status_code == 404

    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval("INSERT INTO sessions (status) VALUES ('active') RETURNING id")
    finally:
        await conn.close()
    resp = await client.get(f"/admin/sessions/{sid}/export", params={"format": "pdf"})
    assert resp.status_code == 400
