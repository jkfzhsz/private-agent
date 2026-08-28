"""skill-binding admin API 读写回归测试(2026-08-27, skill_binding 语义重构)。

覆盖(设计 §2.2-E):
- GET: yaml 默认 → runtime 覆盖 → DELETE 回退 yaml 的完整生命周期
- PUT: 合法 server/通配接受; 非法 server 拒绝整单(400 invalid_binding_pattern)
- 空 binding 可保存(清空起始清单)
- source 标记: yaml|runtime

参照 test_admin_provider_lifecycle.py 的 ASGI TestClient + 测试库模式。
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

# yaml 默认绑定(fake _load_cfg 静态返回, 模拟 config.yaml)
YAML_DEFAULT = {
    "office": ["mempalace", "hexin-ifind-ds-*"],
    "monitor": ["mempalace"],
}
SERVERS = [
    {"id": "mempalace", "type": "stdio", "command": "x"},
    {"id": "Searchpin", "type": "stdio", "command": "x"},
    {"id": "codegraph", "type": "stdio", "command": "x"},
    {"id": "hexin-ifind-ds-1", "type": "sse", "url": "http://x"},
]


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
    """ASGI 客户端 + db.connect 指向测试库 + 静态 cfg(含 servers 与 yaml 默认)。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    async def _fake_load_cfg():
        # 与真实 _load_cfg 等价: yaml 默认 + config_runtime 覆盖合并
        # (PUT 写的 tools.mcp.skill_binding 经此被 GET 读到)
        cfg = {
            "tools": {
                "mcp": {
                    "servers": SERVERS,
                    "skill_binding": YAML_DEFAULT,
                }
            }
        }
        conn = await asyncpg.connect(TEST_DSN)
        try:
            overrides = await cfg_loader._get_runtime_overrides(conn)
        finally:
            await conn.close()
        cfg_loader._deep_merge(cfg, overrides)
        return cfg

    monkeypatch.setattr(admin, "_load_cfg", _fake_load_cfg)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_returns_yaml_default(client):
    """无 runtime 覆盖 → source=yaml, 返回 yaml 默认绑定。"""
    resp = await client.get("/admin/config/skill-binding")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "yaml"
    assert body["skill_binding"] == YAML_DEFAULT


@pytest.mark.asyncio
async def test_put_then_get_returns_runtime(client):
    """PUT 合法绑定 → 200 + source=runtime; GET 读到 runtime 覆盖。"""
    new_binding = {
        "office": ["mempalace", "codegraph"],
        "monitor": ["Searchpin"],
    }
    resp = await client.put(
        "/admin/config/skill-binding", json={"skill_binding": new_binding}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "runtime"
    assert body["skill_binding"] == new_binding

    resp = await client.get("/admin/config/skill-binding")
    assert resp.json()["source"] == "runtime"
    assert resp.json()["skill_binding"] == new_binding


@pytest.mark.asyncio
async def test_put_accepts_wildcard_matching_server(client):
    """通配模式(hexin-ifind-ds-*)至少匹配一个 server → 接受。"""
    resp = await client.put(
        "/admin/config/skill-binding",
        json={"skill_binding": {"office": ["hexin-ifind-ds-*"]}},
    )
    assert resp.status_code == 200
    assert resp.json()["skill_binding"]["office"] == ["hexin-ifind-ds-*"]


@pytest.mark.asyncio
async def test_put_rejects_unknown_server(client):
    """不存在的 server id → 400 invalid_binding_pattern(整单拒绝, 不写库)。"""
    resp = await client.put(
        "/admin/config/skill-binding",
        json={"skill_binding": {"office": ["ghost-server"]}},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_binding_pattern"
    assert "ghost-server" in body["invalid"]["office"]
    # 未写库: GET 仍 yaml 默认
    resp = await client.get("/admin/config/skill-binding")
    assert resp.json()["source"] == "yaml"


@pytest.mark.asyncio
async def test_put_empty_binding_allowed(client):
    """空 binding(清空起始清单)可保存 —— 种子为空 = 仅内置工具起步。"""
    resp = await client.put(
        "/admin/config/skill-binding", json={"skill_binding": {}}
    )
    assert resp.status_code == 200
    resp = await client.get("/admin/config/skill-binding")
    body = resp.json()
    assert body["source"] == "runtime"
    assert body["skill_binding"] == {}


@pytest.mark.asyncio
async def test_delete_falls_back_to_yaml(client):
    """DELETE 删除 runtime 键 → GET 回退 yaml 默认。"""
    # 先写 runtime
    await client.put(
        "/admin/config/skill-binding",
        json={"skill_binding": {"monitor": ["codegraph"]}},
    )
    resp = await client.delete("/admin/config/skill-binding")
    assert resp.status_code == 200
    assert resp.json()["source"] == "yaml"

    resp = await client.get("/admin/config/skill-binding")
    body = resp.json()
    assert body["source"] == "yaml"
    assert body["skill_binding"] == YAML_DEFAULT
