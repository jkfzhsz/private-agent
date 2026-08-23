"""V1.1-3.6 智能体基础配置闭环测试。

覆盖:
- PUT /admin/skills/{name}/meta: 更新 description/avatar/tags/model_params(写 skill.yaml + 同步 PG)
- POST /admin/skills/{name}/clone: 复制目录 + PG 行(name-copy)
- GET /admin/skills 与 /skills/{name} 返回新元数据字段
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
    """ASGI 客户端 + db.connect 指向测试库 + 隔离 skill 目录。

    loader 与 _skill_dev_dir 都从 loader.load_config 读 dev_dir ——
    统一 monkeypatch 使 SkillLoader 与文件操作指向同一隔离目录。
    """
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


def _make_skill(dev_dir, name: str, extra_yaml: str = "") -> None:
    """创建最小合法 skill(manifest 必填 + tools.yaml 顶层列表)。"""
    skill_dir = dev_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        f"name: {name}\nversion: \"1.0.0\"\ndescription: 源描述\nscenario: test\n{extra_yaml}",
        encoding="utf-8",
    )
    (skill_dir / "system_prompt.md").write_text("prompt-body", encoding="utf-8")
    (skill_dir / "tools.yaml").write_text("[]\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_update_skill_meta(client, schema):
    """meta 更新: skill.yaml 落盘 + 详情返回新字段。"""
    client, dev_dir = client
    _make_skill(dev_dir, "testmeta", 'tags: [办公]\n')

    resp = await client.put("/admin/skills/testmeta/meta", json={
        "description": "新描述",
        "avatar": "data:image/png;base64,xxx",
        "tags": ["办公", "测试"],
        "model_params": {"temperature": 0.7, "max_tokens": 2048},
    })
    assert resp.status_code == 200

    yaml_text = (dev_dir / "testmeta" / "skill.yaml").read_text(encoding="utf-8")
    assert "新描述" in yaml_text
    assert "data:image/png" in yaml_text
    assert "max_tokens" in yaml_text

    resp = await client.get("/admin/skills/testmeta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "新描述"
    assert data["avatar"].startswith("data:image")
    assert data["tags"] == ["办公", "测试"]
    assert data["model_params"]["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_clone_skill(client, schema):
    """clone: 目录复制 + yaml name 改写 + 列表可见 + PG 行。"""
    client, dev_dir = client
    _make_skill(dev_dir, "testclone", 'tags: [a]\n')

    resp = await client.post("/admin/skills/testclone/clone")
    assert resp.status_code == 200
    new_name = resp.json()["name"]
    assert new_name == "testclone-copy"

    assert (dev_dir / new_name).exists()
    copied_yaml = (dev_dir / new_name / "skill.yaml").read_text(encoding="utf-8")
    assert "name: testclone-copy" in copied_yaml

    resp = await client.get("/admin/skills")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert new_name in names
    cloned = next(s for s in resp.json() if s["name"] == new_name)
    assert "tags" in cloned

    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow("SELECT name, manifest FROM skills WHERE name = $1", new_name)
        assert row is not None
        manifest = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
        assert manifest["name"] == new_name
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_clone_missing_skill_404(client, schema):
    """clone 不存在的 skill → 404。"""
    client, _ = client
    resp = await client.post("/admin/skills/not_exist_skill/clone")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meta_missing_skill_404(client, schema):
    """meta 更新不存在的 skill → 404。"""
    client, _ = client
    resp = await client.put("/admin/skills/not_exist_skill/meta", json={"description": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_skill(client, schema):
    """删除技能: 目录移除 + PG 行删除 + 列表不可见。"""
    client, dev_dir = client
    _make_skill(dev_dir, "delme")

    resp = await client.delete("/admin/skills/delme")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not (dev_dir / "delme").exists()

    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchrow("SELECT name FROM skills WHERE name = 'delme'")
        assert row is None
    finally:
        await conn.close()

    resp = await client.get("/admin/skills")
    names = [s["name"] for s in resp.json()]
    assert "delme" not in names


@pytest.mark.asyncio
async def test_delete_skill_in_use_rejected(client, schema):
    """被活跃会话锁定的技能 → 400。"""
    client, dev_dir = client
    _make_skill(dev_dir, "locked_skill")
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute(
            "INSERT INTO sessions (status, locked_skill_name) VALUES ('active', 'locked_skill')"
        )
    finally:
        await conn.close()

    resp = await client.delete("/admin/skills/locked_skill")
    assert resp.status_code == 400
    assert (dev_dir / "locked_skill").exists()  # 未删除


# ──────────────────────────────────────────────────────────────────────────────
# Skill 健康测试(2026-08-07 基础功能补齐: 让技能"可测试")
# ──────────────────────────────────────────────────────────────────────────────


async def test_skill_test_endpoint(client, schema):
    """POST /admin/skills/{name}/test: 加载/工具白名单/system_prompt 校验。"""
    client, dev_dir = client
    _make_skill(dev_dir, "echo_skill")

    resp = await client.post("/admin/skills/echo_skill/test")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    names = [c["name"] for c in d["checks"]]
    assert "加载" in names
    assert "工具白名单" in names
    assert "system_prompt" in names


async def test_skill_test_missing_skill(client, schema):
    """不存在的技能 → 200 + ok=false + 加载失败详情(非 500)。"""
    client, _ = client
    resp = await client.post("/admin/skills/no_such_skill_xyz/test")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is False
    assert d["checks"][0]["name"] == "加载"
    assert d["checks"][0]["ok"] is False
