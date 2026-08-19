"""M4 m4-version-compare-rollback AC-1, AC-2, AC-6 - EvalComparator 测试。

Source: spec/m4-version-compare-rollback AC-1, AC-2, AC-6 + plan step 9
- AC-1: compare_versions 双维度筛选(同 model_id + 同 skill_version)取最新成功基线,缺数据抛 InsufficientDataError
- AC-2: _compute_diff 计算指标差值,正确标记 improved/degraded/stable
- AC-6: 退化检测在 diff 中标记 degraded,不自动阻断发布
"""
import asyncio
import os
from datetime import datetime, timezone

import asyncpg
import pytest

from private_agent.eval.repos import EvalRunRepo
from private_agent.eval.version_compare import EvalComparator, InsufficientDataError
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
    conn: "asyncpg.Connection",
    *,
    skill_name: str,
    skill_version: str,
    model_id: str,
    metrics: dict,
    finished: bool = True,
    started_offset_sec: float = 0.0,
) -> str:
    """插入 eval_runs 行,返回 run_id。"""
    run_id = await conn.fetchval(
        """
        INSERT INTO eval_runs (skill_name, skill_version, model_id, dataset_version,
                               eval_mode, mock_enabled, metrics, started_at, finished_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now() - ($8 || ' seconds')::interval,
                CASE WHEN $9 THEN now() ELSE NULL END)
        RETURNING run_id::text
        """,
        skill_name,
        skill_version,
        model_id,
        "v1",
        "offline",
        False,
        __import__("json").dumps(metrics),
        str(started_offset_sec),
        finished,
    )
    return run_id


def test_compare_versions_selects_latest_completed():
    """AC-1: 双维度筛选 + 取最新成功基线。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # base_version="1.0.0" 两条 completed,取最新(started_at 更晚)
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.0.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.7}},
                started_offset_sec=200,  # 较早
            )
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.0.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.8}},  # 最新
                started_offset_sec=100,  # 较晚
            )
            # target_version="1.1.0" 一条 completed
            await _insert_run(
                conn,
                skill_name="office",
                skill_version="1.1.0",
                model_id="mock-glm",
                metrics={"task_completion": {"completion_rate": 0.9}},
                started_offset_sec=0,
            )
            repo = EvalRunRepo(conn)
            comparator = EvalComparator(repo)
            result = await comparator.compare_versions(
                skill_name="office",
                base_version="1.0.0",
                target_version="1.1.0",
                model_id="mock-glm",
            )
            return result
        finally:
            await conn.close()

    result = asyncio.run(_run())
    # base_metrics 取最新(started_at DESC 第一条),即 0.8
    assert result["base_metrics"]["task_completion"]["completion_rate"] == 0.8
    assert result["target_metrics"]["task_completion"]["completion_rate"] == 0.9
    assert result["base_version"] == "1.0.0"
    assert result["target_version"] == "1.1.0"
    assert result["model_id"] == "mock-glm"


def test_compare_versions_raises_insufficient_data_when_no_completed_runs():
    """AC-1: 缺数据抛 InsufficientDataError。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # base_version 无 completed runs
            repo = EvalRunRepo(conn)
            comparator = EvalComparator(repo)
            await comparator.compare_versions(
                skill_name="office",
                base_version="2.0.0",  # 不存在
                target_version="1.1.0",
                model_id="mock-glm",
            )
        finally:
            await conn.close()

    with pytest.raises(InsufficientDataError):
        asyncio.run(_run())


def test_compute_diff_marks_improved_degraded_stable():
    """AC-2: _compute_diff 标记 improved/degraded/stable。"""
    repo = EvalRunRepo.__new__(EvalRunRepo)  # 不需要 conn
    comparator = EvalComparator(repo)
    base = {
        "task_completion": {"completion_rate": 0.8},
        "tool_calls": {"precision": 0.6, "recall": 0.5},
    }
    target = {
        "task_completion": {"completion_rate": 0.9},  # +0.1 improved
        "tool_calls": {"precision": 0.5, "recall": 0.5},  # -0.1 degraded, 0 stable
    }
    diff = comparator._compute_diff(base, target)
    assert diff["task_completion"]["completion_rate"]["delta"] == pytest.approx(0.1)
    assert diff["task_completion"]["completion_rate"]["status"] == "improved"
    assert diff["tool_calls"]["precision"]["delta"] == pytest.approx(-0.1)
    assert diff["tool_calls"]["precision"]["status"] == "degraded"
    assert diff["tool_calls"]["recall"]["delta"] == 0.0
    assert diff["tool_calls"]["recall"]["status"] == "stable"


def test_degradation_marked_in_diff_for_rollback_decision():
    """AC-6: 退化检测在 diff 中标记 degraded,不自动阻断(仅返回标记)。"""
    repo = EvalRunRepo.__new__(EvalRunRepo)
    comparator = EvalComparator(repo)
    base = {"task_completion": {"completion_rate": 0.9}}
    target = {"task_completion": {"completion_rate": 0.5}}  # -0.4 degraded
    diff = comparator._compute_diff(base, target)
    assert diff["task_completion"]["completion_rate"]["status"] == "degraded"
    # 不自动阻断:diff 仅返回标记,无阻断字段
    assert "block" not in diff["task_completion"]["completion_rate"]
