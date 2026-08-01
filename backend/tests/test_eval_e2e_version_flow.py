"""M4 m4-version-compare-rollback AC-1..AC-7 闭环 - 端到端版本对比+回滚测试。

Source: spec/m4-version-compare-rollback AC-1..AC-7 + plan step 14
- 评估运行 v1.0.0 → 评估运行 v1.1.0 → 版本对比 → 退化检测 → 回滚 v1.0.0 → 新会话加载 v1.0.0
- 验证 7 个 AC 端到端串联
"""
import asyncio
import json
import os

import asyncpg
import pytest

from private_agent.eval.repos import EvalRunRepo, VersionSnapshotRepo
from private_agent.eval.rollback import SkillRollbackManager
from private_agent.eval.version_compare import EvalComparator
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
    variant: str | None = None,
) -> str:
    return await conn.fetchval(
        """
        INSERT INTO eval_runs (skill_name, skill_version, model_id, dataset_version,
                               eval_mode, mock_enabled, metrics, finished_at, variant)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now(), $8)
        RETURNING run_id::text
        """,
        skill_name,
        skill_version,
        model_id,
        "v1",
        "offline",
        False,
        json.dumps(metrics),
        variant,
    )


def test_e2e_version_compare_and_rollback_flow():
    """AC-1..AC-7 闭环:评估运行 → 版本对比 → 退化检测 → 回滚 → 新会话加载旧版本。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 1. AC-7: 插入两条 eval_runs(variant 默认 null)
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.0.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.9}},
            )
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.1.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.6}},  # 退化 0.3
            )
            # 2. 插入 skills 表 + v1.0.0 snapshot
            await conn.execute(
                """
                INSERT INTO skills (name, version, manifest, system_prompt, tools)
                VALUES ('office', '1.1.0', $1::jsonb, 'v1.1', '[]'::jsonb)
                """,
                json.dumps({"name": "office", "version": "1.1.0", "scenario": "office"}),
            )
            await conn.execute(
                """
                INSERT INTO version_snapshots (scope, version, payload)
                VALUES ('skill', '1.0.0', $1::jsonb)
                """,
                json.dumps(
                    {
                        "manifest": {"name": "office", "version": "1.0.0", "scenario": "office"},
                        "system_prompt": "v1.0",
                        "tools_yaml": [],
                    }
                ),
            )
            # 3. AC-1, AC-2: 版本对比
            repo = EvalRunRepo(conn)
            comparator = EvalComparator(repo)
            compare_result = await comparator.compare_versions(
                skill_name="office",
                base_version="1.0.0",
                target_version="1.1.0",
                model_id="mock-glm",
            )
            # 4. AC-6: 退化检测
            diff = compare_result["diff"]
            degraded_metrics = [
                m for cat in diff.values() for m in cat.values() if m["status"] == "degraded"
            ]
            # 5. AC-3/4: 回滚到 v1.0.0
            snapshot_repo = VersionSnapshotRepo(conn)
            rollback_mgr = SkillRollbackManager(snapshot_repo=snapshot_repo)
            rollback_result = await rollback_mgr.rollback_skill(
                skill_name="office", target_version="1.0.0", conn=conn
            )
            # 6. 验证 skills 表 version 已回滚
            skills_version = await conn.fetchval(
                "SELECT version FROM skills WHERE name='office'"
            )
            # 7. AC-5: harness 回滚返回命令
            harness_result = rollback_mgr.rollback_harness(target_commit="abc123")
            return {
                "compare": compare_result,
                "degraded_count": len(degraded_metrics),
                "rollback": rollback_result,
                "skills_version": skills_version,
                "harness": harness_result,
            }
        finally:
            await conn.close()

    out = asyncio.run(_run())
    # AC-1: 版本对比成功
    assert out["compare"]["base_version"] == "1.0.0"
    assert out["compare"]["target_version"] == "1.1.0"
    # AC-2: diff 标记 degraded
    assert out["degraded_count"] >= 1
    # AC-6: 退化检测(不阻断,仅标记)
    assert (
        out["compare"]["diff"]["task_completion"]["completion_rate"]["status"]
        == "degraded"
    )
    # AC-4: 回滚成功
    assert out["rollback"]["rolled_back_to"] == "1.0.0"
    assert out["skills_version"] == "1.0.0"
    # AC-5: harness 返回命令
    assert out["harness"]["command"] == "git revert abc123"
    assert "手动执行" in out["harness"]["note"]
