"""§6.14 [MVP] 沙箱 UI 配置管理测试。

覆盖:
- GET /admin/settings/sandbox 读取配置(yaml+runtime 合并)
- PUT /admin/settings/sandbox 写入 runtime(config_runtime 点分 key)
- POST /admin/settings/sandbox/test 测试沙箱执行
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


@pytest.fixture(scope="module")
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
async def _patch_db_connect(monkeypatch):
    """端点内部 db.connect() 指向测试库(真实落库验证 config_runtime)。"""

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    # 建 schema(每个测试重建, 与全库测试风格一致)
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_sandbox_config_returns_merged(client):
    """GET /admin/settings/sandbox 返回 yaml+runtime 合并配置。"""
    resp = await client.get("/admin/settings/sandbox")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "limits" in data
    assert "memory_limit_mb" in data["limits"]
    assert "cpu_timeout_sec" in data["limits"]


@pytest.mark.asyncio
async def test_put_sandbox_config_writes_runtime(client):
    """PUT /admin/settings/sandbox 写入 config_runtime 点分 key。"""
    resp = await client.put(
        "/admin/settings/sandbox",
        json={"memory_limit_mb": 1024, "code_scan_enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    conn = await asyncpg.connect(TEST_DSN)
    try:
        mem = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key='sandbox.limits.memory_limit_mb'"
        )
        scan = await conn.fetchval(
            "SELECT value FROM config_runtime "
            "WHERE key='sandbox.security.code_scan_enabled'"
        )
    finally:
        await conn.close()
    assert mem is not None and int(mem) == 1024
    assert scan is not None and scan == "false"


@pytest.mark.asyncio
async def test_put_sandbox_config_none_fields_ignored(client):
    """PUT 空字段不写入(避免误清 runtime 其它 key)。"""
    resp = await client.put("/admin/settings/sandbox", json={"enabled": None})
    assert resp.status_code == 200

    conn = await asyncpg.connect(TEST_DSN)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM config_runtime WHERE key LIKE 'sandbox.%'"
        )
    finally:
        await conn.close()
    assert count == 0


@pytest.mark.asyncio
async def test_test_sandbox_runs_code(client):
    """POST /admin/settings/sandbox/test 执行示例代码。"""
    resp = await client.post(
        "/admin/settings/sandbox/test",
        json={"code": "print('hello sandbox')", "language": "python"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "hello sandbox" in data["stdout"]
