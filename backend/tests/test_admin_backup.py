"""V1.4-8.1 导入导出与备份体系测试。

覆盖:
- GET /admin/backup: 生成备份 zip(含 backup.json/config_runtime/db 表/skills)
- POST /admin/backup/restore: 事务还原(数据恢复; 非法 zip → 400)
- POST /admin/sessions/export_batch: 会话批量导出(md 合并)
"""

import io
import json
import os
import zipfile

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
def client(monkeypatch, tmp_path):
    """ASGI 客户端 + db.connect 指向测试库 + skills 源目录指向 tmp。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    dev_dir = tmp_path / "skills"
    dev_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(admin, "_skill_dev_dir", lambda: dev_dir)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), dev_dir


async def _seed_data() -> None:
    """造测试数据: 会话 + 消息 + 记忆 + kb 文档 + config_runtime。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval(
            "INSERT INTO sessions (status, title) VALUES ('active', '备份测试') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', '你好', 1, 'active'), ($1, 'assistant', '回复', 1, 'active')",
            sid,
        )
        await conn.execute(
            "INSERT INTO user_memories (user_id, type, content, importance) "
            "VALUES (1, 'fact', '备份记忆', 0.9)"
        )
        await conn.execute(
            "INSERT INTO kb_documents (source, content, scenario, hash) "
            "VALUES ('backup.md', '备份文档内容', 'office', 'hash123')"
        )
        await conn.execute(
            "INSERT INTO config_runtime (key, value) VALUES ('system.test_key', '{\"v\": 1}')"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_backup_zip_structure(client, schema, tmp_path):
    """备份 zip 含全部数据文件。"""
    client, dev_dir = client
    (dev_dir / "office").mkdir(exist_ok=True)
    (dev_dir / "office" / "skill.yaml").write_text(
        "name: office\nversion: \"1.0.0\"\n", encoding="utf-8"
    )
    await _seed_data()

    resp = await client.get("/admin/backup")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "backup.json" in names
        assert "config_runtime.json" in names
        for t in ("sessions", "messages", "user_memories", "kb_documents"):
            assert f"db/{t}.json" in names
        assert any(n.startswith("skills/") for n in names)
        meta = json.loads(zf.read("backup.json"))
        assert meta["app"] == "private-agent"
        msgs = json.loads(zf.read("db/messages.json"))
        assert len(msgs) == 2
        cr = json.loads(zf.read("config_runtime.json"))
        assert cr.get("system.test_key") == {"v": 1}


@pytest.mark.asyncio
async def test_backup_restore_roundtrip(client, schema):
    """备份 → 清库 → 还原 → 数据恢复(含会话/消息/记忆/config_runtime)。"""
    client, dev_dir = client
    await _seed_data()
    resp = await client.get("/admin/backup")
    assert resp.status_code == 200
    backup_bytes = resp.content

    # 清库
    conn = await asyncpg.connect(TEST_DSN)
    try:
        for t in ("messages", "user_memories", "kb_documents", "sessions"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.execute("DELETE FROM config_runtime")
    finally:
        await conn.close()

    # 还原
    resp = await client.post(
        "/admin/backup/restore",
        files={"file": ("backup.zip", backup_bytes, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["restored"]["sessions"] == 1
    assert data["restored"]["messages"] == 2
    assert data["restored"]["user_memories"] == 1
    assert data["chunks_rebuild_pending"] is True

    # 验证
    conn = await asyncpg.connect(TEST_DSN)
    try:
        assert await conn.fetchval("SELECT COUNT(*) FROM sessions") == 1
        assert await conn.fetchval("SELECT COUNT(*) FROM messages") == 2
        title = await conn.fetchval("SELECT title FROM sessions LIMIT 1")
        assert title == "备份测试"
        mem = await conn.fetchval("SELECT content FROM user_memories LIMIT 1")
        assert mem == "备份记忆"
        cr = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = 'system.test_key'"
        )
        assert json.loads(cr) == {"v": 1}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_restore_invalid(client, schema):
    """非 zip → 400; 无 backup.json 的 zip → 400。"""
    client, _ = client
    resp = await client.post(
        "/admin/backup/restore",
        files={"file": ("x.txt", b"not a zip", "text/plain")},
    )
    assert resp.status_code == 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.txt", "hello")
    resp = await client.post(
        "/admin/backup/restore",
        files={"file": ("x.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 400
    assert "backup.json missing" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_export_batch(client, schema):
    """批量导出: 多会话 md 合并。"""
    client, _ = client
    conn = await asyncpg.connect(TEST_DSN)
    try:
        s1 = await conn.fetchval(
            "INSERT INTO sessions (status, title) VALUES ('active', '会话A') RETURNING id"
        )
        s2 = await conn.fetchval(
            "INSERT INTO sessions (status, title) VALUES ('active', '会话B') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, turn, zone) "
            "VALUES ($1, 'user', '问题一', 1, 'active'), ($2, 'user', '问题二', 1, 'active')",
            s1, s2,
        )
    finally:
        await conn.close()

    resp = await client.post("/admin/sessions/export_batch", json={
        "session_ids": [s1, s2], "format": "md",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert "# 会话A" in data["content"]
    assert "# 会话B" in data["content"]
    assert "问题一" in data["content"]
    assert "问题二" in data["content"]

    resp = await client.post("/admin/sessions/export_batch", json={
        "session_ids": [], "format": "md",
    })
    assert resp.status_code == 400
