"""M4 m4-version-compare-rollback AC-8, AC-9, AC-10 - eval API 端点测试。

Source: spec/m4-version-compare-rollback AC-8, AC-9, AC-10 + plan step 11
- AC-8: POST /admin/eval/runs 触发评估运行,返回 run_id
- AC-9: GET /admin/eval/versions/compare 返回版本对比结果含 diff
- AC-10: POST /admin/eval/rollback 触发回滚,返回 rolled_back_to + affected_sessions
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.eval.repos import EvalRunRepo, VersionSnapshotRepo
from private_agent.main import app
from private_agent.storage import migrations

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


async def _insert_run(
    conn: asyncpg.Connection,
    *,
    skill_name: str,
    skill_version: str,
    model_id: str,
    metrics: dict,
    finished: bool = True,
) -> str:
    return await conn.fetchval(
        """
        INSERT INTO eval_runs (skill_name, skill_version, model_id, dataset_version,
                               eval_mode, mock_enabled, metrics, finished_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb,
                CASE WHEN $8 THEN now() ELSE NULL END)
        RETURNING run_id::text
        """,
        skill_name,
        skill_version,
        model_id,
        "v1",
        "offline",
        False,
        json.dumps(metrics),
        finished,
    )


def test_trigger_eval_run_endpoint_returns_run_id(monkeypatch):
    """AC-8: POST /admin/eval/runs 触发评估运行,返回 run_id。"""
    _setup_schema()

    # mock EvalRunner.run_evaluation 避免依赖完整评估流程
    captured = {}

    class _MockRunner:
        async def run_evaluation(self, **kwargs):
            captured.update(kwargs)
            return "mock-run-id-123"

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_eval_runner",
        lambda cfg, conn: _MockRunner(),
    )

    client = TestClient(app)
    resp = client.post(
        "/admin/eval/runs",
        json={
            "skill_name": "office",
            "skill_version": "1.0.0",
            "model_id": "mock-glm",
            "eval_mode": "offline",
            "mock_enabled": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "mock-run-id-123"
    assert captured["skill_name"] == "office"
    assert captured["skill_version"] == "1.0.0"
    assert captured["model_id"] == "mock-glm"
    assert captured["eval_mode"] == "offline"
    assert captured["mock_enabled"] is False


def test_compare_versions_endpoint_returns_diff(monkeypatch):
    """AC-9: GET /admin/eval/versions/compare 返回版本对比结果含 diff。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.0.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.8}},
            )
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.1.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.7}},  # 退化
            )
        finally:
            await conn.close()

    asyncio.run(_run())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)

    client = TestClient(app)
    resp = client.get(
        "/admin/eval/versions/compare",
        params={
            "skill_name": "office",
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "model_id": "mock-glm",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_version"] == "1.0.0"
    assert data["target_version"] == "1.1.0"
    assert "diff" in data
    assert (
        data["diff"]["task_completion"]["completion_rate"]["status"] == "degraded"
    )


def test_rollback_endpoint_returns_rolled_back_to(monkeypatch):
    """AC-10: POST /admin/eval/rollback 触发回滚,返回 rolled_back_to + affected_sessions。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 插入 skill 行 + version snapshot
            await conn.execute(
                """
                INSERT INTO skills (name, version, manifest, system_prompt, tools)
                VALUES ('office', '1.1.0', '{}'::jsonb, 'v1.1', '[]'::jsonb)
                """
            )
            await conn.execute(
                """
                INSERT INTO version_snapshots (scope, version, payload)
                VALUES ('skill', '1.0.0', $1::jsonb)
                """,
                json.dumps(
                    {
                        "manifest": {"name": "office", "version": "1.0.0"},
                        "system_prompt": "v1.0",
                        "tools_yaml": [],
                    }
                ),
            )
        finally:
            await conn.close()

    asyncio.run(_run())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)

    client = TestClient(app)
    resp = client.post(
        "/admin/eval/rollback",
        json={
            "skill_name": "office",
            "target_version": "1.0.0",
            "scope": "skill",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rolled_back_to"] == "1.0.0"
    assert data["scope"] == "skill"
    assert data["affected_sessions"] == 0


def test_variant_field_default_null(monkeypatch):
    """AC-7: eval_runs.variant 字段默认 null,MVP 不涉及流量分配。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            run_id = await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.0.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.8}},
            )
            row = await conn.fetchrow(
                "SELECT variant FROM eval_runs WHERE run_id=$1::uuid", run_id
            )
            return {"variant": row["variant"]}
        finally:
            await conn.close()

    out = asyncio.run(_run())
    assert out["variant"] is None  # MVP 默认 null
