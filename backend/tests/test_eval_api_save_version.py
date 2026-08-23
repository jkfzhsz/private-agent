"""M4 m4-version-compare-rollback AC-12 - save-version 端点测试。

Source: spec/m4-version-compare-rollback AC-12 + plan step 12
- AC-12: POST /admin/skills/{name}/save-version 保存新版本到 version_snapshots + 触发 SkillVersionListener 快速回归
- listener 失败仅记日志,不阻塞版本保存
"""
import asyncio
import json
import os
from unittest.mock import MagicMock

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.main import app
from private_agent.storage import migrations

_AUTH_HEADERS = {"X-Admin-Token": "test-admin-token"}

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


def test_save_version_persists_snapshot_and_triggers_listener(monkeypatch):
    """AC-12: 保存新版本到 version_snapshots + 触发 SkillVersionListener 快速回归。"""
    _setup_schema()

    listener_calls = []

    class _MockListener:
        async def on_skill_version_saved(self, *, skill_name, version, conn):
            listener_calls.append({"skill_name": skill_name, "version": version})

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr("private_agent.api.admin.db.connect", _fake_connect)
    # 注入 mock listener(构造函数替换)
    monkeypatch.setattr(
        "private_agent.api.admin._build_skill_version_listener",
        lambda cfg: _MockListener(),
    )

    client = TestClient(app)


    client.headers.update(_AUTH_HEADERS)
    resp = client.post(
        "/admin/skills/office/save-version",
        json={
            "version": "1.2.0",
            "manifest": {
                "name": "office",
                "version": "1.2.0",
                "scenario": "office",
            },
            "system_prompt": "v1.2 prompt",
            "tools_yaml": [{"name": "file_read"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved_version"] == "1.2.0"
    assert data["scope"] == "skill"

    # 验证 version_snapshots 表已写入
    async def _verify() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT payload FROM version_snapshots WHERE scope='skill' AND version='1.2.0'"
            )
            return {"payload": json.loads(row["payload"]) if row else None}
        finally:
            await conn.close()

    out = asyncio.run(_verify())
    assert out["payload"] is not None
    assert out["payload"]["manifest"]["name"] == "office"
    assert out["payload"]["system_prompt"] == "v1.2 prompt"

    # listener 被调用
    assert len(listener_calls) == 1
    assert listener_calls[0]["skill_name"] == "office"
    assert listener_calls[0]["version"] == "1.2.0"


def test_save_version_does_not_block_when_listener_fails(monkeypatch):
    """AC-12: listener 失败仅记日志,不阻塞版本保存。"""
    _setup_schema()

    class _FailingListener:
        async def on_skill_version_saved(self, *, skill_name, version, conn):
            raise RuntimeError("listener boom")

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr("private_agent.api.admin.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.admin._build_skill_version_listener",
        lambda cfg: _FailingListener(),
    )

    client = TestClient(app)


    client.headers.update(_AUTH_HEADERS)
    resp = client.post(
        "/admin/skills/office/save-version",
        json={
            "version": "1.3.0",
            "manifest": {"name": "office", "version": "1.3.0", "scenario": "office"},
            "system_prompt": "v1.3",
            "tools_yaml": [],
        },
    )
    # 版本保存成功(listener 失败不阻塞)
    assert resp.status_code == 200
    assert resp.json()["saved_version"] == "1.3.0"
