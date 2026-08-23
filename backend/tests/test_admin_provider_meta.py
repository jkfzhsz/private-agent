"""V1.4-8.2 模型管理完整体系: provider 分组元数据测试。

覆盖:
- PUT /settings/providers/{name}: 落 group/sort_order/kind 到 config_runtime
- GET /settings/providers: 透传 group/sort_order/kind
- group 传空串 → 清除(config_runtime key 删除 → GET 返回 null)
"""

import asyncio
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
def client(monkeypatch, schema):
    """ASGI 客户端 + db.connect 指向测试库 + 固定 cfg(含 demo provider)。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    async def _fake_load_cfg():
        # 模拟真实 _load_cfg: yaml 基础 + config_runtime providers 覆盖合并
        cfg = {
            "models": {
                "providers": {
                    "demo": {
                        "base_url": "http://demo.test/v1",
                        "model_name": "demo-m",
                        "enabled": True,
                    }
                },
                "router": {"fallback_chain": ["demo"]},
            }
        }
        conn = await asyncpg.connect(TEST_DSN)
        try:
            rows = await conn.fetch(
                "SELECT key, value FROM config_runtime WHERE key LIKE 'models.providers.demo.%'"
            )
        finally:
            await conn.close()
        import json as _json

        prefix = "models.providers.demo."
        for r in rows:
            k = r["key"][len(prefix):]
            v = r["value"]
            if isinstance(v, str):
                try:
                    v = _json.loads(v)
                except ValueError:
                    pass
            cfg["models"]["providers"]["demo"][k] = v
        return cfg

    monkeypatch.setattr(admin, "_load_cfg", _fake_load_cfg)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_provider_group_meta_roundtrip(client, schema):
    """PUT 分组元数据 → GET 透传。"""
    resp = await client.put("/admin/settings/providers/demo", json={
        "group": "主力模型",
        "sort_order": 2,
        "kind": "cloud",
    })
    assert resp.status_code == 200, resp.text

    resp = await client.get("/admin/settings/providers")
    assert resp.status_code == 200
    prov = next(p for p in resp.json()["providers"] if p["name"] == "demo")
    assert prov["group"] == "主力模型"
    assert prov["sort_order"] == 2
    assert prov["kind"] == "cloud"


@pytest.mark.asyncio
async def test_provider_group_clear(client, schema):
    """group 传空串 → 清除(key 删除 → 返回 null)。"""
    await client.put("/admin/settings/providers/demo", json={"group": "临时组"})
    resp = await client.put("/admin/settings/providers/demo", json={"group": ""})
    assert resp.status_code == 200

    resp = await client.get("/admin/settings/providers")
    prov = next(p for p in resp.json()["providers"] if p["name"] == "demo")
    assert prov["group"] is None

    # config_runtime key 已删
    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT value FROM config_runtime WHERE key = 'models.providers.demo.group'"
        )
        assert row is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_provider_kind_default(client, schema):
    """未设置 kind → 默认 cloud。"""
    resp = await client.get("/admin/settings/providers")
    prov = next(p for p in resp.json()["providers"] if p["name"] == "demo")
    assert prov["kind"] == "cloud"
    assert prov["sort_order"] == 0
