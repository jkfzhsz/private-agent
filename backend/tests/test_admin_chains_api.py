"""模型链管理端点回归（P1-3 后端，2026-09-29）。

覆盖新增的三个端点：
- `GET  /settings/chains`            三链视图 + 悬空标注 + 发图能力
- `PUT  /settings/chains/{name}`     设置顺序(自愈剔除 + dropped 回报)
- `POST /settings/chains/repair`     一键修复(复用启动期 audit_chains)

设计要点：
- `text_chain` / `vision_chain` **不自动追加**成员(顺序由用户定, 避免多模态
  provider 被塞到 text_chain 链首抢占主对话 —— D1 决策);
- `fallback_chain` 保持"自动补齐 enabled provider 到尾部"的既有语义;
- `vision_capable` 与系统提示的能力声明同源(看链不看字典)。
"""
import json
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
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    async def _fake_load_cfg(conn=None):
        cfg = {
            "models": {
                "providers": {},
                "router": {"type": "manual", "fallback_chain": []},
            }
        }
        own = conn is None
        c = await asyncpg.connect(TEST_DSN) if own else conn
        try:
            overrides = await cfg_loader._get_runtime_overrides(c)
        finally:
            if own:
                await c.close()
        cfg_loader._deep_merge(cfg, overrides)
        return cfg

    monkeypatch.setattr(admin, "_load_cfg", _fake_load_cfg)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _runtime_set(key: str, value) -> None:
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute(
            "INSERT INTO config_runtime (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            json.dumps(value),
        )
    finally:
        await conn.close()


async def _runtime_get(key: str):
    conn = await asyncpg.connect(TEST_DSN)
    try:
        row = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = $1", key
        )
    finally:
        await conn.close()
    return json.loads(row) if isinstance(row, str) else row


async def _add_provider(name: str, *, enabled=True, multimodal=False) -> None:
    await _runtime_set(f"models.providers.{name}.enabled", enabled)
    await _runtime_set(f"models.providers.{name}.multimodal", multimodal)


# ──────────────────────────────────────────────────────────────────────────────
# GET /settings/chains
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_chains_reports_dangling_and_applied(client, schema):
    """三链视图: 有效项进 applied, 悬空项(不存在/禁用/删除)进 dangling。"""
    await _add_provider("alpha", enabled=True)
    await _add_provider("dead", enabled=False)
    await _runtime_set("models.router.text_chain", ["alpha", "dead", "ghost"])
    await _runtime_set("models.router.vision_chain", [])
    await _runtime_set("models.router.fallback_chain", ["alpha"])

    resp = await client.get("/admin/settings/chains")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    text = body["chains"]["text_chain"]
    assert text["configured"] is True
    assert text["order"] == ["alpha", "dead", "ghost"]
    assert text["applied"] == ["alpha"]
    assert text["dangling"] == ["dead", "ghost"]
    by_name = {i["name"]: i for i in text["items"]}
    assert by_name["alpha"]["valid"] is True
    assert by_name["dead"]["valid"] is False
    assert by_name["dead"]["enabled"] is False
    assert by_name["ghost"]["exists"] is False
    assert body["any_dangling"] is True


@pytest.mark.asyncio
async def test_get_chains_marks_unconfigured_chain(client, schema):
    """未配置的链 configured=False(区别于"空链" —— 未配置会回退 fallback_chain)。"""
    await _runtime_set("models.router.fallback_chain", ["alpha"])
    await _add_provider("alpha", enabled=True)

    body = (await client.get("/admin/settings/chains")).json()
    assert body["chains"]["vision_chain"]["configured"] is False
    assert body["chains"]["fallback_chain"]["configured"] is True


@pytest.mark.asyncio
async def test_get_chains_vision_capable_follows_chain_not_dict(client, schema):
    """vision_capable 看链: vision_chain 为空 → 回退 fallback_chain, 其中多模态
    仍应判为具备发图能力(与发图轮的真实行为一致)。"""
    await _add_provider("deepseek-flash", enabled=True, multimodal=False)
    await _add_provider("glm-vision", enabled=True, multimodal=True)
    await _runtime_set("models.router.vision_chain", [])
    await _runtime_set(
        "models.router.fallback_chain", ["deepseek-flash", "glm-vision"]
    )

    body = (await client.get("/admin/settings/chains")).json()
    assert body["vision_capable"] is True

    # 禁用多模态 provider → 能力随之消失(不再谎报)
    await _add_provider("glm-vision", enabled=False, multimodal=True)
    body2 = (await client.get("/admin/settings/chains")).json()
    assert body2["vision_capable"] is False


# ──────────────────────────────────────────────────────────────────────────────
# PUT /settings/chains/{chain_name}
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_text_chain_sets_order_and_drops_invalid(client, schema):
    """设置 text_chain 顺序: 无效项剔除并回报, 不自动追加其它 provider(D1)。"""
    await _add_provider("alpha", enabled=True)
    await _add_provider("beta", enabled=True)
    await _add_provider("dead", enabled=False)
    await _runtime_set("models.router.text_chain", ["alpha", "beta"])

    resp = await client.put(
        "/admin/settings/chains/text_chain",
        json={"chain": ["beta", "dead", "alpha"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["chain"] == ["beta", "alpha"]
    assert data["dropped"] == ["dead"]
    assert await _runtime_get("models.router.text_chain") == ["beta", "alpha"]


@pytest.mark.asyncio
async def test_put_text_chain_does_not_auto_append(client, schema):
    """text_chain 不自动补齐 —— 未列出的 enabled provider 不得被塞进链(D1)。"""
    await _add_provider("alpha", enabled=True)
    await _add_provider("glm-vision", enabled=True, multimodal=True)
    await _runtime_set("models.router.text_chain", ["alpha"])

    resp = await client.put(
        "/admin/settings/chains/text_chain", json={"chain": ["alpha"]}
    )
    assert resp.status_code == 200
    assert resp.json()["chain"] == ["alpha"], (
        "多模态 provider 不应被自动塞进 text_chain(会抢占主对话)"
    )


@pytest.mark.asyncio
async def test_put_fallback_chain_still_auto_appends(client, schema):
    """fallback_chain 保持既有语义: 未列出的 enabled provider 追加到尾部。"""
    await _add_provider("alpha", enabled=True)
    await _add_provider("beta", enabled=True)
    await _runtime_set("models.router.fallback_chain", ["alpha", "beta"])

    resp = await client.put(
        "/admin/settings/chains/fallback_chain", json={"chain": ["beta"]}
    )
    assert resp.status_code == 200
    assert resp.json()["chain"] == ["beta", "alpha"]


@pytest.mark.asyncio
async def test_put_unknown_chain_rejected(client, schema):
    """未知链名 → 400(防拼错后静默写入无效 key)。"""
    resp = await client.put(
        "/admin/settings/chains/bogus_chain", json={"chain": ["alpha"]}
    )
    assert resp.status_code == 400
    assert "bogus_chain" in resp.text


# ──────────────────────────────────────────────────────────────────────────────
# POST /settings/chains/repair
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repair_chains_removes_dangling_and_reports(client, schema):
    """一键修复: 剔除三道链的悬空项, 并回报 emptied(vision 清空 → 发图降级)。"""
    await _add_provider("alpha", enabled=True)
    await _add_provider("dead", enabled=False)
    await _runtime_set("models.router.text_chain", ["alpha", "dead"])
    await _runtime_set("models.router.vision_chain", ["glm-vision"])
    await _runtime_set("models.router.fallback_chain", ["alpha"])

    resp = await client.post("/admin/settings/chains/repair")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["dropped"]["text_chain"] == ["dead"]
    assert data["dropped"]["vision_chain"] == ["glm-vision"]
    assert data["emptied"] == ["vision_chain"]
    assert await _runtime_get("models.router.text_chain") == ["alpha"]
    assert await _runtime_get("models.router.vision_chain") == []


@pytest.mark.asyncio
async def test_repair_chains_idempotent(client, schema):
    """重复调用 repair 稳定(第二次无变更)。"""
    await _add_provider("alpha", enabled=True)
    await _runtime_set("models.router.text_chain", ["alpha"])
    await _runtime_set("models.router.fallback_chain", ["alpha"])

    first = (await client.post("/admin/settings/chains/repair")).json()
    second = (await client.post("/admin/settings/chains/repair")).json()
    assert first["dropped"] == {}
    assert second["dropped"] == {}
