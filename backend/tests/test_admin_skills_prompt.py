"""V1.2-6.1 技能配置编辑器测试。

覆盖:
- GET /admin/skills/{name}/prompt: 返回 system_prompt + token_count + version
- PUT /admin/skills/{name}/prompt: 落盘 system_prompt.md + 自动快照(scope=prompt) + 同步 PG
- 不存在技能 → 404
"""
import json
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
def client(monkeypatch, tmp_path):
    """ASGI 客户端 + db.connect 指向测试库 + 隔离 skill 目录。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config
    dev_dir = tmp_path / "skills"
    dev_dir.mkdir(exist_ok=True)

    def _fake_load_config():
        cfg = original_load()
        return {**cfg, "skills": {"storage": {"dev_dir": str(dev_dir)}}}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), dev_dir


def _make_skill(dev_dir, name: str) -> None:
    skill_dir = dev_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        f"name: {name}\nversion: \"1.0.0\"\ndescription: d\nscenario: test\n",
        encoding="utf-8",
    )
    (skill_dir / "system_prompt.md").write_text("你是数据分析助手。", encoding="utf-8")
    (skill_dir / "tools.yaml").write_text("[]\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_get_skill_prompt(client, schema):
    """GET prompt: 返回内容 + token_count(>0) + version。"""
    client, dev_dir = client
    _make_skill(dev_dir, "proskill")

    resp = await client.get("/admin/skills/proskill/prompt")
    assert resp.status_code == 200
    data = resp.json()
    assert "你是数据分析助手" in data["system_prompt"]
    assert data["token_count"] is not None and data["token_count"] > 0
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_put_skill_prompt(client, schema):
    """PUT prompt: 落盘 + 快照(scope=prompt) + PG 同步。"""
    client, dev_dir = client
    _make_skill(dev_dir, "proskill")

    new_prompt = "你是新的助手，专注文本总结。"
    resp = await client.put("/admin/skills/proskill/prompt", json={"system_prompt": new_prompt})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # 落盘验证
    assert (dev_dir / "proskill" / "system_prompt.md").read_text(encoding="utf-8") == new_prompt

    # 快照验证
    conn = await asyncpg.connect(TEST_DSN)
    try:
        snap = await conn.fetchrow(
            "SELECT scope, payload FROM version_snapshots WHERE scope = 'prompt' ORDER BY created_at DESC LIMIT 1"
        )
        assert snap is not None
        payload = json.loads(snap["payload"]) if isinstance(snap["payload"], str) else snap["payload"]
        assert payload["skill_name"] == "proskill"
        assert payload["system_prompt"] == new_prompt
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_prompt_missing_skill_404(client, schema):
    """不存在技能 → 404。"""
    client, _ = client
    resp = await client.get("/admin/skills/nope/prompt")
    assert resp.status_code == 404
    resp = await client.put("/admin/skills/nope/prompt", json={"system_prompt": "x"})
    assert resp.status_code == 404
