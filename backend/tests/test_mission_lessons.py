"""0.6.0 D-4: 跨任务经验沉淀测试(mission_lessons 结构化 + skill_lessons 文本)。

覆盖(V7 验收③④):
- record_mission_outcome: 时长样本统计/聚合键派生/增量加权合并
- get_initial_interval: 样本<3 回退默认档; 达标取 p50 clamp; 30 天窗
- extract_and_save_lesson: 正常落 skill_lessons(scope=monitor/project_evolution);
  adapter 异常静默 None; 非终态 state 拒绝
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from private_agent.core import mission_assembly as ma_mod
from private_agent.core import mission_runner as mr_mod
from private_agent.core.mission_lessons import (
    MISSION_LESSONS_MIN_SAMPLES,
    derive_aggregation_key,
    extract_and_save_lesson,
    get_initial_interval,
    record_mission_outcome,
)
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations

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


@pytest.fixture(scope="module", autouse=True)
def _schema_fixture():
    _setup_schema()


@pytest.fixture(autouse=True)
def _clean():
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                "TRUNCATE missions, subagents, mission_lessons, skill_lessons "
                "RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()
    asyncio.run(_run())
    ma_mod.mission_registry._count = 0
    mr_mod.mission_tasks.clear()


async def _seed_mission_with_sub(
    *,
    executor_type: str = "subagent",
    task_type: str = "search",
    dur_sec: float = 90.0,
) -> int:
    """mission + 一个已完成 subagent(时长 dur_sec)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        sid = await conn.fetchval(
            "INSERT INTO sessions (title) VALUES ('d4') RETURNING id"
        )
        mid = await conn.fetchval(
            "INSERT INTO missions (session_id, charter, plan, state) "
            "VALUES ($1, '{}', $2, 'done') RETURNING id",
            sid,
            json.dumps([{"id": "m1", "milestone": "x",
                         "executor_type": executor_type}]),
        )
        await conn.execute(
            """
            INSERT INTO subagents (session_id, parent_turn, parent_task, prompt,
                                   status, task_type, started_at, finished_at)
            VALUES ($1, 0, $2, 'p', 'succeeded', $3,
                    now() - make_interval(secs => $4), now())
            """,
            sid, f"mission-{mid}-m1", task_type, dur_sec,
        )
        return mid
    finally:
        await conn.close()


# ── 聚合键派生 ──────────────────────────────────────────────────────────

def test_derive_aggregation_key_mode():
    key = derive_aggregation_key(
        [{"executor_type": "subagent"}, {"executor_type": "script"},
         {"executor_type": "subagent"}],
        ["search", "search", "other"],
    )
    assert key == ("subagent", "search")


def test_derive_aggregation_key_empty_fallback():
    assert derive_aggregation_key([], []) == ("subagent", "other")


# ── record_mission_outcome ──────────────────────────────────────────────

def test_record_outcome_upsert_and_merge():
    """首次记录 → upsert; 二次记录 → 增量加权合并 + sample_count 累加。"""
    _setup_schema()

    async def _flow() -> tuple:
        mid1 = await _seed_mission_with_sub(dur_sec=90.0)
        r1 = None
        conn = await asyncpg.connect(TEST_DSN)
        try:
            r1 = await record_mission_outcome(conn, mid1)
        finally:
            await conn.close()
        mid2 = await _seed_mission_with_sub(dur_sec=210.0)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            r2 = await record_mission_outcome(conn, mid2)
            row = await conn.fetchrow(
                "SELECT sample_count, p50_duration_sec, last_interval_sec "
                "FROM mission_lessons WHERE executor_type='subagent' "
                "AND task_type='search'"
            )
            return r1, r2, dict(row) if row else {}
        finally:
            await conn.close()
    r1, r2, row = asyncio.run(_flow())
    assert r1 is not None and r2 is not None
    assert r1["observed_p50"] == 90.0 and r2["observed_p50"] == 210.0
    assert row["sample_count"] == 2
    # 加权合并: (90+210)/2 = 150
    assert abs(row["p50_duration_sec"] - 150.0) < 0.01
    # last_interval = clamp(p50) → 150 ∈ [30,600]
    assert abs(row["last_interval_sec"] - 150.0) < 0.01


def test_record_outcome_no_subagent_returns_none():
    """无 subagent 样本(纯 wait/script) → None 不落表。"""
    _setup_schema()

    async def _flow() -> object:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title) VALUES ('x') RETURNING id"
            )
            mid = await conn.fetchval(
                "INSERT INTO missions (session_id, charter, plan, state) "
                "VALUES ($1, '{}', '[]', 'done') RETURNING id", sid,
            )
            return await record_mission_outcome(conn, mid)
        finally:
            await conn.close()
    assert asyncio.run(_flow()) is None


# ── get_initial_interval(V7-③) ─────────────────────────────────────────

def test_initial_interval_fallback_below_min_samples():
    """样本 <3 → 回退默认档(小样本不信任)。"""
    _setup_schema()

    async def _flow() -> float:
        await _seed_mission_with_sub(dur_sec=90.0)  # 1 个样本
        mid = await _seed_mission_with_sub(dur_sec=210.0)  # 第 2 个样本
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await record_mission_outcome(conn, mid)  # 合并后 sample=2 <3
            return await get_initial_interval(
                conn, "subagent", "search", fallback_sec=90.0
            )
        finally:
            await conn.close()
    assert asyncio.run(_flow()) == 90.0


def test_initial_interval_p50_when_samples_sufficient():
    """样本 ≥3 → 取 p50(经 clamp); 30 天时间窗内。"""
    _setup_schema()

    async def _flow() -> float:
        for dur in (90.0, 210.0):
            mid = await _seed_mission_with_sub(dur_sec=dur)
            conn = await asyncpg.connect(TEST_DSN)
            try:
                await record_mission_outcome(conn, mid)
            finally:
                await conn.close()
        # 第 3 个样本 → sample_count=3
        mid = await _seed_mission_with_sub(dur_sec=150.0)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await record_mission_outcome(conn, mid)
            return await get_initial_interval(
                conn, "subagent", "search", fallback_sec=90.0
            )
        finally:
            await conn.close()
    val = asyncio.run(_flow())
    # 合并 p50 = (90*1+210*1)/2=150 → (150*2+150)/3=150(加权与全量重算一致)
    assert abs(val - 150.0) < 0.01


def test_initial_interval_expired_lessons_fallback():
    """updated_at 超过 30 天 → 视为过期, 回退默认档。"""
    _setup_schema()

    async def _flow() -> float:
        await _seed_mission_with_sub(dur_sec=90.0)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await record_mission_outcome(conn, 1)
            # 人为把 updated_at 推到 31 天前
            await conn.execute(
                "UPDATE mission_lessons SET updated_at = now() - interval '31 days', "
                "sample_count = 5"
            )
            return await get_initial_interval(
                conn, "subagent", "search", fallback_sec=120.0
            )
        finally:
            await conn.close()
    assert asyncio.run(_flow()) == 120.0


# ── extract_and_save_lesson(V7-④) ───────────────────────────────────────

class _LessonAdapter:
    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
        return ChatResult(content="搜索类子代理在境外域名不可达时应优先镜像, 避免盲试")


def test_extract_lesson_saves_to_skill_lessons():
    _setup_schema()

    async def _flow() -> tuple:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title) VALUES ('d4l') RETURNING id"
            )
            lid = await extract_and_save_lesson(
                conn, _LessonAdapter(), 42,
                {"goal": "整理缺陷清单"},
                [{"kind": "milestone_failed", "detail": "超时"}],
                "failed",
            )
            row = await conn.fetchrow(
                "SELECT scope, lesson_category, lesson_type, lesson_content "
                "FROM skill_lessons WHERE id=$1", lid,
            )
            return lid, dict(row) if row else {}
        finally:
            await conn.close()
    lid, row = asyncio.run(_flow())
    assert lid is not None
    assert row["scope"] == "monitor"
    assert row["lesson_category"] == "project_evolution"
    assert row["lesson_type"] == "failure"
    assert "镜像" in row["lesson_content"]


def test_extract_lesson_rejects_non_terminal_state():
    _setup_schema()

    async def _flow() -> object:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await extract_and_save_lesson(
                conn, _LessonAdapter(), 1, {}, [], "executing"
            )
        finally:
            await conn.close()
    assert asyncio.run(_flow()) is None


def test_extract_lesson_adapter_crash_silent_none():
    _setup_schema()

    class _Bad:
        async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
            raise RuntimeError("down")

    async def _flow() -> object:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await extract_and_save_lesson(
                conn, _Bad(), 1, {"goal": "g"}, [], "done"
            )
        finally:
            await conn.close()
    assert asyncio.run(_flow()) is None


def test_min_samples_constant():
    """§4.4.1: 样本阈值 3(设计文档裁决)。"""
    assert MISSION_LESSONS_MIN_SAMPLES == 3
