"""provider 生命周期与降级链一致性回归测试。

背景(2026-08-08 线上问题): 用户接入 4 个模型, 设置页只显示 3 个, 降级链却有 4 项,
保存时报 400 "降级链包含不存在的 provider"。

根因: asyncpg 对 JSONB 列返回 JSON 字符串, create_provider 用 `existing is True`
判断软删标记恒为 False → 重建已删除 provider 时 deleted 标记残留(写入成功但被
GET 过滤掉), 同时又被加回 fallback_chain, 形成"幽灵项"死锁。

覆盖:
- 创建 → 删除 → 同名重建: deleted 标记必须被清除, 重新出现在列表与降级链
- 同名重复创建(未删除) → 409
- 降级链含幽灵项: GET 过滤; PUT 自愈剔除并回报 dropped, 不再 400 阻塞保存
"""

import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.config import loader as cfg_loader
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
    """ASGI 客户端 + db.connect 指向测试库。

    _load_cfg 复用真实 loader 的 override 解析(含 JSONB json.loads 与
    provider name 含小数点的分割规则), 保证测试链路等价于生产链路。
    """
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    async def _fake_load_cfg():
        cfg = {"models": {"providers": {}, "router": {"fallback_chain": []}}}
        conn = await asyncpg.connect(TEST_DSN)
        try:
            overrides = await cfg_loader._get_runtime_overrides(conn)
        finally:
            await conn.close()
        cfg_loader._deep_merge(cfg, overrides)
        return cfg

    monkeypatch.setattr(admin, "_load_cfg", _fake_load_cfg)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create(client, name: str, **kw):
    body = {
        "name": name,
        "base_url": "https://example.test/v1",
        "model_name": name,
        "enabled": True,
    }
    body.update(kw)
    return await client.post("/admin/settings/providers", json=body)


@pytest.mark.asyncio
async def test_recreate_deleted_provider_clears_flag(client, schema):
    """删除后同名重建: deleted 标记必须清除, 重新出现在列表与降级链。"""
    assert (await _create(client, "acme")).status_code == 200

    resp = await client.delete("/admin/settings/providers/acme")
    assert resp.status_code == 200
    body = (await client.get("/admin/settings/providers")).json()
    assert [p["name"] for p in body["providers"]] == []
    assert body["fallback_chain"] == []

    # 重建
    assert (await _create(client, "acme")).status_code == 200
    body = (await client.get("/admin/settings/providers")).json()
    assert [p["name"] for p in body["providers"]] == ["acme"]
    assert body["fallback_chain"] == ["acme"]

    # 软删标记确实已从 config_runtime 移除(而非仅被覆盖)
    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT value FROM config_runtime "
            "WHERE key = 'models.providers.acme.deleted'"
        )
    finally:
        await conn.close()
    assert row is None


@pytest.mark.asyncio
async def test_duplicate_create_rejected(client, schema):
    """未删除的同名 provider 重复创建 → 409(避免静默覆盖已有配置)。"""
    assert (await _create(client, "acme")).status_code == 200
    resp = await _create(client, "acme", base_url="https://other.test/v1")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_deleted_provider_404(client, schema):
    """已软删的 provider 不可 PUT 更新(应走 POST 重建)。"""
    assert (await _create(client, "acme")).status_code == 200
    await client.delete("/admin/settings/providers/acme")
    resp = await client.put(
        "/admin/settings/providers/acme", json={"model_name": "x"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fallback_chain_ghost_self_heal(client, schema):
    """降级链残留已删除 provider: GET 过滤, PUT 自愈剔除并回报 dropped。"""
    assert (await _create(client, "alpha")).status_code == 200
    assert (await _create(client, "beta")).status_code == 200

    # 构造脏数据: 手工把已删除的 ghost 塞回链中(模拟历史 bug 留下的状态)
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute(
            "INSERT INTO config_runtime (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "models.router.fallback_chain",
            '["alpha", "ghost", "beta"]',
        )
    finally:
        await conn.close()

    # GET: 幽灵项不暴露给前端
    body = (await client.get("/admin/settings/providers")).json()
    assert body["fallback_chain"] == ["alpha", "beta"]

    # PUT: 即便请求里带上幽灵项也能保存成功(剔除并回报)
    resp = await client.put(
        "/admin/settings/fallback-chain",
        json={"chain": ["beta", "ghost", "alpha"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chain"] == ["beta", "alpha"]
    assert data["dropped"] == ["ghost"]

    body = (await client.get("/admin/settings/providers")).json()
    assert body["fallback_chain"] == ["beta", "alpha"]


@pytest.mark.asyncio
async def test_fallback_chain_appends_missing_enabled(client, schema):
    """未在 chain 中的 enabled provider 自动追加到尾部。"""
    assert (await _create(client, "alpha")).status_code == 200
    assert (await _create(client, "beta")).status_code == 200

    resp = await client.put(
        "/admin/settings/fallback-chain", json={"chain": ["beta"]}
    )
    assert resp.status_code == 200
    assert resp.json()["chain"] == ["beta", "alpha"]
