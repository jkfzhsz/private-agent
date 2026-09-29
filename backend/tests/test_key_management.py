"""P4 密钥管理回归（2026-09-29）。

覆盖：
- P4-2 `POST /settings/providers/{name}/rotate-key` —— 密钥轮换一等操作：
  密文落库 + 进程内热更新 + 审计字段（不记明文）；
- P4-3 `check_master_key_consistency` —— 多处 PA_MASTER_KEY 不一致时显式
  报告（消除"用 A 加密、用 B 解密 → provider 静默 401"的隐雷），并经
  `/health` 的 `warnings` 暴露。

D2 决策：**不反转** key 优先级（保持环境变量优先），故本文件不涉及优先级变更。
"""
import asyncio
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


async def _runtime_fields(prefix: str) -> dict:
    conn = await asyncpg.connect(TEST_DSN)
    try:
        rows = await conn.fetch(
            "SELECT key, value FROM config_runtime WHERE key LIKE $1",
            f"{prefix}.%",
        )
    finally:
        await conn.close()
    out: dict = {}
    for r in rows:
        field = r["key"].rsplit(".", 1)[1]
        v = r["value"]
        out[field] = json.loads(v) if isinstance(v, str) else v
    return out


# ──────────────────────────────────────────────────────────────────────────────
# P4-2 密钥轮换
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_key_stores_ciphertext_and_audit(client, schema, monkeypatch):
    """轮换: 密文落库(无明文) + key_rotated_at 审计 + 环境变量热更新。"""
    await _runtime_set("models.providers.acme.enabled", True)
    monkeypatch.delenv("PA_ACME_API_KEY", raising=False)

    resp = await client.post(
        "/admin/settings/providers/acme/rotate-key",
        json={"api_key": "sk-new-secret", "verify": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "acme"
    assert body["rotated_at"]

    # 热生效: 环境变量已更新(adapter 每次按当前环境变量构造, 不缓存)
    assert os.environ.get("PA_ACME_API_KEY") == "sk-new-secret"

    fields = await _runtime_fields("models.providers.acme")
    assert "key_rotated_at" in fields, "应记录轮换时间(审计)"
    encrypted = fields["api_key_encrypted"]
    assert "ciphertext" in encrypted and "nonce" in encrypted
    assert "sk-new-secret" not in json.dumps(encrypted), "明文不得落库"
    # verify=False 时不做连通性测试 → 无测试审计字段
    assert "last_test_ok" not in fields


@pytest.mark.asyncio
async def test_rotate_key_rejects_empty_key(client, schema):
    """空 key → 400（防止把 provider 的 key 清成空串后静默失效）。"""
    await _runtime_set("models.providers.acme.enabled", True)
    resp = await client.post(
        "/admin/settings/providers/acme/rotate-key",
        json={"api_key": "   ", "verify": False},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_rotate_key_rejects_unknown_provider(client, schema):
    """未知/已删除 provider → 404。"""
    resp = await client.post(
        "/admin/settings/providers/nope/rotate-key",
        json={"api_key": "sk-x", "verify": False},
    )
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# P4-3 双 master key 一致性
# ──────────────────────────────────────────────────────────────────────────────


def test_master_key_consistency_detects_divergence(monkeypatch):
    """进程环境与 user_env 的 key 不同 → 报告不一致, 且不泄露完整密钥。"""
    master_a = "a" * 64
    master_b = "b" * 64
    monkeypatch.setenv("PA_MASTER_KEY", master_a)
    admin._write_env_updates(admin._user_env_path(), {"PA_MASTER_KEY": master_b})

    report = admin.check_master_key_consistency()
    assert report["consistent"] is False
    assert "process_env=" in report["detail"]
    assert "user_env=" in report["detail"]
    assert master_a not in report["detail"], "不得泄露完整 master key"
    assert master_b not in report["detail"]


def test_master_key_consistency_ok_when_single_source(monkeypatch, tmp_path):
    """只有一处有值(或各处同值) → 视为一致。"""
    master = "c" * 64
    monkeypatch.setenv("PA_MASTER_KEY", master)
    admin._write_env_updates(admin._user_env_path(), {"PA_MASTER_KEY": master})
    # 把 workspace_root 指到空目录, 使 backend/.env 不参与比较
    monkeypatch.setattr(
        admin.loader,
        "load_config",
        lambda *a, **k: {"system": {"workspace_root": str(tmp_path)}},
    )

    report = admin.check_master_key_consistency()
    assert report["consistent"] is True
    assert report["detail"] == ""


# ──────────────────────────────────────────────────────────────────────────────
# /health 暴露启动期告警
# ──────────────────────────────────────────────────────────────────────────────


def test_health_exposes_startup_warnings(monkeypatch):
    from private_agent import main as main_mod

    monkeypatch.setattr(main_mod, "_STARTUP_WARNINGS", ["PA_MASTER_KEY 不一致"])
    payload = asyncio.run(main_mod.health())
    assert payload["status"] == "ok", "status 语义不变"
    assert payload["warnings"] == ["PA_MASTER_KEY 不一致"]


def test_health_omits_warnings_when_clean(monkeypatch):
    from private_agent import main as main_mod

    monkeypatch.setattr(main_mod, "_STARTUP_WARNINGS", [])
    payload = asyncio.run(main_mod.health())
    assert payload == {"status": "ok"}
