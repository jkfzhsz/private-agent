"""V1.2-6.2 MCP env 配置测试。

覆盖:
- POST /settings/mcp 带 env → 保存到 config_runtime + 列表返回 env
- _build_server_value 保留 env
- MCPClientConfig 接受 env(stdio 子进程注入)
"""
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.storage import migrations
from private_agent.tools.mcp_client import MCPClientConfig

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
async def test_upsert_mcp_with_env(client, schema):
    """POST /settings/mcp 带 env → 列表返回含 env。"""
    resp = await client.post("/admin/settings/mcp", json={
        "name": "my-stdio",
        "type": "stdio",
        "command": "node",
        "args": ["server.js"],
        "env": {"MY_API_KEY": "secret123", "BASE_URL": "http://x"},
    })
    assert resp.status_code == 200

    resp = await client.get("/admin/mcp/servers")
    assert resp.status_code == 200
    servers = resp.json()["servers"]
    target = next((s for s in servers if s["id"] == "my-stdio"), None)
    assert target is not None
    assert target["env"] == {"MY_API_KEY": "secret123", "BASE_URL": "http://x"}
    assert target["command"] == "node"


@pytest.mark.asyncio
async def test_build_server_value_env_preserved(client, schema):
    """_build_server_value 保留 env。"""
    value = admin._build_server_value(
        name="x", server_type="stdio", command="a", args=["b"], env={"K": "v"}
    )
    assert value["env"] == {"K": "v"}


def test_mcp_client_config_env():
    """MCPClientConfig 支持 env(stdio 子进程注入用)。"""
    cfg = MCPClientConfig(server_id="s", server_type="stdio", command="x", env={"K": "v"})
    assert cfg.env == {"K": "v"}
