"""0.6.0 F1-1: async_tasks → missions 长任务表迁移测试。

覆盖 _migrate_missions_table 三分支(设计文档 §6.1 F1-1):
1. 全新库(两者皆无) → CREATE missions(与 schema.sql 同构)
2. 老部署(有 async_tasks) → RENAME + DROP 废弃列 + 补新列
3. missions 已存在 → 幂等跳过(migrate_all 两遍结构一致)

验收(设计文档 F1-1): migrate 幂等跑两遍 + DESC 结构正确 + state CHECK 枚举。
"""
import asyncio
import os

import asyncpg
import pytest

from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

MISSION_COLUMNS = {
    "id", "session_id", "charter", "plan", "budget", "journal",
    "state", "result", "error", "created_at", "updated_at", "completed_at",
}
LEGACY_COLUMNS = {"status", "progress", "tool_name"}
VALID_STATES = {
    "planning", "executing", "supervising",
    "done", "failed", "cancelled", "escalated",
}


def _run(coro_fn) -> None:
    asyncio.run(coro_fn())


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(TEST_DSN)


async def _reset_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("DROP SCHEMA public CASCADE")
    await conn.execute("CREATE SCHEMA public")
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def _columns() -> set[str]:
    async def _run() -> set[str]:
        conn = await _connect()
        try:
            rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='missions' AND table_schema='public'"
            )
            return {r["column_name"] for r in rows}
        finally:
            await conn.close()
    return asyncio.run(_run())


def _migrate() -> None:
    """全新库完整 migrate_all(依赖 sessions 等表, 走完整 schema 流程)。"""
    async def _run() -> None:
        conn = await _connect()
        try:
            await _reset_schema(conn)
            await migrations.migrate_all(conn)
        finally:
            await conn.close()
    asyncio.run(_run())


# ── 分支 1: 全新库 → CREATE ─────────────────────────────────────────────

def test_fresh_schema_creates_missions():
    """全新库 migrate_all → missions 表存在且列齐全。"""
    _migrate()
    cols = _columns()
    missing = MISSION_COLUMNS - cols
    assert not missing, f"Missing columns: {missing}. Got: {sorted(cols)}"
    assert not (cols & LEGACY_COLUMNS), f"Legacy columns leaked: {cols & LEGACY_COLUMNS}"


# ── 分支 2: 老部署 async_tasks → RENAME + 补列 ─────────────────────────

def test_rename_from_async_tasks():
    """老部署有 async_tasks → RENAME 为 missions + 废弃列清除 + 新列补齐。"""
    async def _run() -> None:
        conn = await _connect()
        try:
            await _reset_schema(conn)
            # 手工模拟老部署: 只建 async_tasks(不带新列), 不跑 migrate_all
            await conn.execute(
                "CREATE TABLE async_tasks ("
                "  id BIGSERIAL PRIMARY KEY,"
                "  session_id BIGINT NOT NULL,"
                "  tool_name VARCHAR(100) NOT NULL,"
                "  status VARCHAR(20) DEFAULT 'pending',"
                "  progress FLOAT DEFAULT 0,"
                "  result JSONB,"
                "  error TEXT,"
                "  created_at TIMESTAMPTZ DEFAULT NOW(),"
                "  updated_at TIMESTAMPTZ DEFAULT NOW(),"
                "  completed_at TIMESTAMPTZ)"
            )
            await migrations.migrate_all(conn)
        finally:
            await conn.close()
    _run()
    cols = _columns()
    missing = MISSION_COLUMNS - cols
    assert not missing, f"Missing columns after rename: {missing}"
    assert not (cols & LEGACY_COLUMNS), f"Legacy columns not dropped: {cols & LEGACY_COLUMNS}"


# ── 分支 3: 幂等 ────────────────────────────────────────────────────────

def test_migrate_twice_idempotent():
    """migrate_all 连跑两遍: 无报错, 列结构一致(验收核心)。"""
    _migrate()
    cols_first = _columns()
    async def _run() -> None:
        conn = await _connect()
        try:
            await migrations.migrate_all(conn)
        finally:
            await conn.close()
    asyncio.run(_run())
    assert _columns() == cols_first


# ── state CHECK 约束 ────────────────────────────────────────────────────

def test_state_check_enum():
    """合法 7 枚举全过; 非法值被 CHECK 拒绝。"""
    _migrate()

    async def _run() -> list[str]:
        conn = await _connect()
        try:
            # 外键依赖: 先建一条 session(missions.session_id → sessions)
            await conn.execute(
                "INSERT INTO sessions (title) VALUES ('f1-1-state-check')"
            )
            # 合法值: 逐个插入
            for i, state in enumerate(sorted(VALID_STATES)):
                await conn.execute(
                    "INSERT INTO missions (session_id, state) VALUES ($1, $2)",
                    1, state,
                )
            # 非法值: 期待 CHECK 拒绝
            try:
                await conn.execute(
                    "INSERT INTO missions (session_id, state) VALUES ($1, $2)",
                    1, "bogus_state",
                )
            except asyncpg.CheckViolationError:
                pass
            else:
                raise AssertionError("bogus_state 应被 missions_state_check 拒绝")
            rows = await conn.fetch("SELECT state FROM missions")
            return [r["state"] for r in rows]
        finally:
            await conn.close()
    states = asyncio.run(_run())
    assert set(states) == VALID_STATES


def test_missions_state_check_constraint_named():
    """CHECK 约束以 missions_state_check 命名(统一命名, D 批可依赖)。"""
    _migrate()

    async def _run() -> str | None:
        conn = await _connect()
        try:
            return await conn.fetchval(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid='missions'::regclass AND contype='c' "
                "AND pg_get_constraintdef(oid) LIKE '%escalated%'"
            )
        finally:
            await conn.close()
    assert asyncio.run(_run()) == "missions_state_check"
