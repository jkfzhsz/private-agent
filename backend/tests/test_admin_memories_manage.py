"""V1.3-7.1 长期记忆系统完善: 记忆 CRUD + 检索 + 注入配置测试。

覆盖:
- POST /admin/memories 手动新增(含校验/类型/importance 钳制)
- DELETE /admin/memories/{id} 软删除(is_active=FALSE, 列表不再出现)
- GET /admin/memories?q= 内容检索
- GET/PUT /admin/settings/memory 注入配置读写(config_runtime 覆盖)
"""

import pytest
import asyncpg
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from private_agent.api import admin
from private_agent.api.admin import router

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture
def client(monkeypatch, schema):
    """ASGI 客户端 + db.connect 指向测试库。"""
    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等迁移)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_create_memory(client, schema):
    """手动新增记忆 → 返回 id, 列表可见。"""
    async with client as c:
        resp = await c.post("/admin/memories", json={
            "content": "用户偏好: 代码注释用中文",
            "type": "preference",
            "importance": 0.8,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True and data["id"] > 0

        resp = await c.get("/admin/memories")
        rows = resp.json()
        assert any(
            r["id"] == data["id"]
            and r["type"] == "preference"
            and "中文" in r["content"]
            and abs(r["importance"] - 0.8) < 1e-6
            for r in rows
        )


@pytest.mark.asyncio
async def test_create_memory_invalid(client, schema):
    """空内容 → 400; 非法类型回落 fact; importance 钳制到 [0,1]。"""
    async with client as c:
        resp = await c.post("/admin/memories", json={"content": "   "})
        assert resp.status_code == 400
        assert resp.json()["error"] == "memory_invalid_content"

        resp = await c.post("/admin/memories", json={
            "content": "x", "type": "weird", "importance": 9.9,
        })
        assert resp.status_code == 200
        rows = (await c.get("/admin/memories")).json()
        row = next(r for r in rows if r["id"] == resp.json()["id"])
        assert row["type"] == "fact"
        assert row["importance"] == 1.0


@pytest.mark.asyncio
async def test_delete_memory_soft(client, schema):
    """软删除: is_active=FALSE, 列表消失; 再删 → 404。"""
    async with client as c:
        mid = (await c.post("/admin/memories", json={"content": "待删除"})).json()["id"]
        resp = await c.delete(f"/admin/memories/{mid}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        rows = (await c.get("/admin/memories")).json()
        assert all(r["id"] != mid for r in rows)

        # 数据库层面 is_active=FALSE 已落库
        conn = await asyncpg.connect(TEST_DSN)
        try:
            active = await conn.fetchval(
                "SELECT is_active FROM user_memories WHERE id = $1", mid
            )
            assert active is False
        finally:
            await conn.close()

        resp = await c.delete(f"/admin/memories/{mid}")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_memories(client, schema):
    """q 参数检索 content ILIKE。"""
    async with client as c:
        await c.post("/admin/memories", json={"content": "Alpha 项目里程碑"})
        await c.post("/admin/memories", json={"content": "Beta 测试计划"})
        await c.post("/admin/memories", json={"content": "gamma 会议纪要"})

        resp = await c.get("/admin/memories", params={"q": "项目"})
        rows = resp.json()
        assert len(rows) == 1
        assert "Alpha" in rows[0]["content"]

        resp = await c.get("/admin/memories", params={"q": "不存在的词xyz"})
        assert resp.json() == []


@pytest.mark.asyncio
async def test_memory_config_roundtrip(client, schema):
    """记忆注入配置读写: GET 默认值 + PUT 覆盖 + GET 反映。"""
    async with client as c:
        resp = await c.get("/admin/settings/memory")
        assert resp.status_code == 200
        cfg = resp.json()
        assert cfg["inject_limit"] >= 1
        assert cfg["extract_interval_turns"] >= 1

        resp = await c.put("/admin/settings/memory", json={
            "inject_limit": 15,
            "extract_interval_turns": 4,
            "eviction_max_active_count": 500,
            "eviction_min_importance_threshold": 0.5,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = await c.get("/admin/settings/memory")
        cfg = resp.json()
        assert cfg["inject_limit"] == 15
        assert cfg["extract_interval_turns"] == 4
        assert cfg["eviction"]["max_active_count"] == 500
        assert cfg["eviction"]["min_importance_threshold"] == 0.5
