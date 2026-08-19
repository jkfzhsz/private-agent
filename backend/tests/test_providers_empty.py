"""V2 P3 - 去预置化: 空 providers 场景 + 按 model_name 匹配 compress/judge。

验证:
- 空 providers/fallback_chain → 空链(不炸)
- build_compress_adapter / build_judge_adapter 按 model_name 匹配, 无匹配返回 None
- 设置页 GET providers 空列表; 添加首个 provider 后进入 fallback_chain
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.eval.judge import build_judge_adapter
from private_agent.main import app
from private_agent.models.registry import build_compress_adapter, build_fallback_chain
from private_agent.storage import db, migrations

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


def _empty_cfg() -> dict:
    """去预置化后的配置: providers/fallback_chain 为空。"""
    return {
        "models": {
            "providers": {},
            "router": {"type": "manual", "fallback_chain": []},
        },
        "eval": {"judge_model": "judge-m-1"},
    }


def _cfg_with_provider(name: str, model_name: str, **kw) -> dict:
    cfg = _empty_cfg()
    cfg["models"]["providers"][name] = {
        "base_url": f"http://{name}.test",
        "model_name": model_name,
        "enabled": True,
        **kw,
    }
    return cfg


class TestEmptyFallbackChain:
    def test_empty_providers_returns_empty_chain(self):
        """空 providers/chain → FallbackChain 无 adapter(不炸)。"""
        chain = build_fallback_chain(_empty_cfg())
        assert chain._adapters == []


class TestCompressAdapterMatch:
    def test_matches_provider_by_model_name(self):
        """compress_model 与某 provider 的 model_name 相等 → 构造该 provider 的 adapter。"""
        cfg = _cfg_with_provider("my-llm", "flash-m")
        cfg["models"]["compress_model"] = "flash-m"

        adapter = build_compress_adapter(cfg)

        assert adapter is not None
        assert adapter.model_name == "flash-m"
        assert adapter.provider_name == "my-llm"

    def test_no_match_returns_none(self):
        """compress_model 无匹配 provider → None(优雅降级, 不炸)。"""
        cfg = _cfg_with_provider("my-llm", "flash-m")
        cfg["models"]["compress_model"] = "nonexistent-model"

        assert build_compress_adapter(cfg) is None

    def test_no_compress_model_returns_none(self):
        """未配置 compress_model → None。"""
        cfg = _cfg_with_provider("my-llm", "flash-m")
        assert build_compress_adapter(cfg) is None

    def test_disabled_provider_not_used(self):
        """model_name 匹配但 provider disabled → None。"""
        cfg = _cfg_with_provider("my-llm", "flash-m", enabled=False)
        cfg["models"]["compress_model"] = "flash-m"
        assert build_compress_adapter(cfg) is None


class TestJudgeAdapterMatch:
    def test_matches_provider_by_model_name(self):
        """judge_model 与某 provider 的 model_name 相等 → 构造该 provider 的 adapter。"""
        cfg = _cfg_with_provider("my-llm", "judge-m")
        cfg["eval"]["judge_model"] = "judge-m"

        adapter = build_judge_adapter(cfg)

        assert adapter is not None
        assert adapter.model_name == "judge-m"
        assert adapter.provider_name == "my-llm"

    def test_no_match_returns_none(self):
        """judge_model 无匹配 provider → None(优雅降级)。"""
        cfg = _cfg_with_provider("my-llm", "judge-m")
        cfg["eval"]["judge_model"] = "ghost-model"
        assert build_judge_adapter(cfg) is None

    def test_no_judge_model_returns_none(self):
        """未配置 judge_model → None。"""
        cfg = _cfg_with_provider("my-llm", "judge-m")
        del cfg["eval"]["judge_model"]
        assert build_judge_adapter(cfg) is None


class TestProvidersApiEmpty:
    def test_get_providers_empty(self, monkeypatch):
        """空配置 → GET /admin/settings/providers 返回空列表 + 空链。"""
        _setup_schema()
        _patch_db_connect(monkeypatch)
        # 清空 config_runtime 的 provider 相关 key
        _clear_runtime_providers()

        client = TestClient(app)


        client.headers.update(_AUTH_HEADERS)
        resp = client.get("/admin/settings/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["providers"] == []
        assert body["fallback_chain"] == []

    def test_add_first_provider_enters_chain(self, monkeypatch):
        """添加首个 provider(enabled) → 自动进入 fallback_chain。"""
        _setup_schema()
        _patch_db_connect(monkeypatch)
        _clear_runtime_providers()

        client = TestClient(app)


        client.headers.update(_AUTH_HEADERS)
        resp = client.post(
            "/admin/settings/providers",
            json={
                "name": "my-first-llm",
                "base_url": "https://api.example.com/v1",
                "model_name": "m-1",
                "enabled": True,
            },
        )
        assert resp.status_code == 200

        body = client.get("/admin/settings/providers").json()
        assert [p["name"] for p in body["providers"]] == ["my-first-llm"]
        assert body["fallback_chain"] == ["my-first-llm"]


def _patch_db_connect(monkeypatch) -> None:
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


def _clear_runtime_providers() -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                "DELETE FROM config_runtime WHERE key LIKE 'models.providers.%'"
                " OR key = 'models.router.fallback_chain'"
            )
        finally:
            await conn.close()

    asyncio.run(_run())
