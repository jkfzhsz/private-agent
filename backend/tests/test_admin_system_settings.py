"""V1.4-8.3 系统设置完善测试。

覆盖:
- POST /admin/cache/clear: 清理 outputs 过期文件(按 retention_days)
- GET/PUT /admin/settings/system: 读默认 + 写 log_level/retention/proxy + master key 状态
- workspace_root 写入 config_runtime
"""

import os
from datetime import datetime, timedelta, timezone

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
    """ASGI 客户端 + db.connect 指向测试库 + workspace_root 指向 tmp/ws。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "outputs").mkdir(exist_ok=True)

    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config

    def _fake_load_config(*args, **kwargs):
        cfg = original_load(*args, **kwargs)
        return {**cfg, "system": {**cfg.get("system", {}), "workspace_root": str(ws)}}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), ws


@pytest.mark.asyncio
async def test_cache_clear(client, schema):
    """清理 outputs 中超过保留期的文件; 新文件保留。"""
    client, ws = client
    old = ws / "outputs" / "old.txt"
    new = ws / "outputs" / "new.txt"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    # old 文件 mtime 改为 10 天前
    past = datetime.now(timezone.utc) - timedelta(days=10)
    os.utime(old, (past.timestamp(), past.timestamp()))

    resp = await client.post("/admin/cache/clear")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["cleaned_files"] >= 1
    assert not old.exists()
    assert new.exists()


@pytest.mark.asyncio
async def test_system_settings_roundtrip(client, schema):
    """GET 默认 → PUT log_level/retention/proxy → GET 反映 + master key 状态。"""
    client, _ = client
    resp = await client.get("/admin/settings/system")
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["app_name"] == "Private Agent"
    assert cfg["log_level"] in ("DEBUG", "INFO", "WARNING", "ERROR")
    assert "master_key_configured" in cfg

    resp = await client.put("/admin/settings/system", json={
        "log_level": "DEBUG",
        "log_retention_days": 3,
        "proxy_http": "http://127.0.0.1:7890",
        "proxy_https": "http://127.0.0.1:7890",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get("/admin/settings/system")
    cfg = resp.json()
    assert cfg["log_level"] == "DEBUG"
    assert cfg["log_retention_days"] == 3
    assert cfg["proxy_http"] == "http://127.0.0.1:7890"
    assert cfg["proxy_https"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_system_settings_clear_proxy(client, schema):
    """proxy 空串 → 清除(config_runtime key 删除 → GET 返回 null)。"""
    client, _ = client
    await client.put("/admin/settings/system", json={"proxy_http": "http://x"})
    await client.put("/admin/settings/system", json={"proxy_http": "", "proxy_https": ""})

    resp = await client.get("/admin/settings/system")
    cfg = resp.json()
    assert cfg["proxy_http"] is None
    assert cfg["proxy_https"] is None

    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT value FROM config_runtime WHERE key = 'system.proxy.http'"
        )
        assert row is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_workspace_root_persist(client, schema):
    """workspace_root 写入 config_runtime。"""
    client, _ = client
    resp = await client.put("/admin/settings/system", json={
        "workspace_root": "D:/custom/ws"
    })
    assert resp.status_code == 200

    conn = await asyncpg.connect(TEST_DSN)
    try:
        val = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = 'system.workspace_root'"
        )
        assert val == '"D:/custom/ws"'
    finally:
        await conn.close()
