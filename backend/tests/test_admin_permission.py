"""阶段三批次1(T1.2) - 会话级权限模式 admin 端点测试(调研 round2 §4.2.1)。

覆盖:
- GET /admin/settings/permission 读取模式 + 模式清单
- PUT /admin/settings/permission 更新模式(含非法值 422 / 不存在会话 404)
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
    """端点内部 db.connect() 指向测试库(真实落库验证 sessions.permission_mode)。"""

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
        # 预置会话 1(默认 default)
        await conn.execute("INSERT INTO sessions (id) VALUES (1)")
    finally:
        await conn.close()


class TestGetPermissionConfig:
    async def test_get_default_mode(self, client):
        resp = await client.get("/admin/settings/permission", params={"session_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == 1
        assert data["mode"] == "default"
        assert "default" in data["modes"]
        assert "deny_all" in data["modes"]
        assert len(data["mode_descriptions"]) == 5

    async def test_get_missing_session_defaults(self, client):
        """不存在会话 → 回退 default。"""
        resp = await client.get("/admin/settings/permission", params={"session_id": 999})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "default"


class TestUpdatePermissionConfig:
    async def test_update_mode_persisted(self, client):
        resp = await client.put(
            "/admin/settings/permission",
            json={"session_id": 1, "mode": "plan"},
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "plan"
        # 读取确认落库
        resp = await client.get("/admin/settings/permission", params={"session_id": 1})
        assert resp.json()["mode"] == "plan"

    async def test_update_invalid_mode_422(self, client):
        resp = await client.put(
            "/admin/settings/permission",
            json={"session_id": 1, "mode": "hacker"},
        )
        assert resp.status_code == 422

    async def test_update_missing_session_404(self, client):
        resp = await client.put(
            "/admin/settings/permission",
            json={"session_id": 888, "mode": "plan"},
        )
        assert resp.status_code == 404

    async def test_cycle_all_modes(self, client):
        """五种模式逐一写入均可读取。"""
        for mode in ("default", "plan", "acceptEdits", "cautious", "deny_all"):
            resp = await client.put(
                "/admin/settings/permission",
                json={"session_id": 1, "mode": mode},
            )
            assert resp.status_code == 200, mode
            get_resp = await client.get(
                "/admin/settings/permission", params={"session_id": 1}
            )
            assert get_resp.json()["mode"] == mode
