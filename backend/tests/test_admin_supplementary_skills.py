"""2026-08-12 Phase 2: 会话附加技能(多技能调用)API 测试。

覆盖:
- POST add: 挂载附加技能(隔离目录中存在) → added 列表
- POST add: 技能不存在 → failed 含原因
- GET list: 返回已挂载列表
- DELETE remove: 移除成功
- 幂等: 重复挂载不产生重复行

注: 用 TestClient(独立事件循环 portal) 规避 Windows asyncio 共享循环下
asyncpg 短连接风暴的 ConnectionResetError。
"""
import os

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture
def schema():
    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    import asyncio

    asyncio.run(_run())


@pytest.fixture
def client(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config
    dev_dir = tmp_path / "skills"
    dev_dir.mkdir(exist_ok=True)

    def _fake_load_config(*_args, **_kwargs):
        cfg = original_load()
        return {**cfg, "skills": {"storage": {"dev_dir": str(dev_dir)}}}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)
    return TestClient(app), dev_dir


def _make_skill(dev_dir, name: str) -> None:
    skill_dir = dev_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        f'name: {name}\nversion: "1.0.0"\ndescription: 测试技能\nscenario: test\n',
        encoding="utf-8",
    )
    (skill_dir / "system_prompt.md").write_text("prompt", encoding="utf-8")


def _make_session(client) -> int:
    resp = client.post("/admin/sessions", json={"kind": "main"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_add_list_remove_supplementary_skills(client, schema):
    """挂载 → 列表 → 移除 全流程。"""
    client, dev_dir = client
    _make_skill(dev_dir, "supp_test")
    sid = _make_session(client)

    resp = client.post(
        f"/admin/sessions/{sid}/supplementary-skills",
        json={"skill_names": ["supp_test"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["added"] == ["supp_test"]
    assert data["failed"] == []

    resp = client.get(f"/admin/sessions/{sid}/supplementary-skills")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["skills"]]
    assert names == ["supp_test"]

    resp = client.delete(f"/admin/sessions/{sid}/supplementary-skills/supp_test")
    assert resp.status_code == 200
    assert resp.json()["removed"] is True

    resp = client.get(f"/admin/sessions/{sid}/supplementary-skills")
    assert resp.json()["skills"] == []


def test_add_unknown_skill_fails_with_reason(client, schema):
    """技能不存在 → failed 含原因, 不阻塞其余。"""
    client, dev_dir = client
    _make_skill(dev_dir, "supp_known")
    sid = _make_session(client)

    resp = client.post(
        f"/admin/sessions/{sid}/supplementary-skills",
        json={"skill_names": ["supp_known", "no_such_skill_xyz"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["added"] == ["supp_known"]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["name"] == "no_such_skill_xyz"
    assert "reason" in data["failed"][0]


def test_add_is_idempotent(client, schema):
    """重复挂载同一技能 → 不产生重复行。"""
    client, dev_dir = client
    _make_skill(dev_dir, "supp_idem")
    sid = _make_session(client)

    for _ in range(2):
        resp = client.post(
            f"/admin/sessions/{sid}/supplementary-skills",
            json={"skill_names": ["supp_idem"]},
        )
        assert resp.status_code == 200

    resp = client.get(f"/admin/sessions/{sid}/supplementary-skills")
    assert len(resp.json()["skills"]) == 1


def test_migration_creates_table(schema):
    """migrate_all 创建 session_supplementary_skills 表。"""
    import asyncio

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' "
                "AND tablename='session_supplementary_skills')"
            )
        finally:
            await conn.close()

    assert asyncio.run(_run()) is True
