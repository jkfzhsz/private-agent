"""B4.1 - Postgres 13 张表 migrations 可执行。

Source: plan/m0-implementation step 4 (蓝图 §9.6 step4 + §9.14 + §2.10 + §2.13)

测试策略:
- 使用 private_agent_test 数据库(隔离开发数据)
- 每个测试前 DROP SCHEMA,跑 migrate_all,验证表存在
- 不验证具体字段(各章节已定义),仅验证 §9.14 全表清单
"""
import asyncio
import os

import asyncpg
import pytest

# 蓝图 §9.14 全表清单(13 张)
EXPECTED_TABLES = [
    "sessions",
    "messages",
    "messages_archive",
    "react_events",
    "user_memories",
    "kb_chunks",
    "kb_documents",
    "version_snapshots",
    "eval_datasets",
    "eval_runs",
    "async_tasks",
    "config_runtime",
    "skills",
]

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    """清空 schema + 安装扩展 + 跑 migrate_all。"""
    from private_agent.storage import migrations

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


def _list_tables() -> set[str]:
    """返回 public schema 的所有表名。"""
    async def _run() -> set[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            return {r["tablename"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_all_13_tables_exist():
    """蓝图 §9.14 的 13 张表全部创建成功。"""
    _setup_schema()
    actual = _list_tables()
    missing = set(EXPECTED_TABLES) - actual
    assert not missing, f"Missing tables: {missing}. Got: {sorted(actual)}"


def _sessions_columns() -> set[str]:
    """返回 sessions 表的列名集合。"""
    async def _run() -> set[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='sessions'"
            )
            return {r["column_name"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_sessions_has_skill_lock_columns():
    """M3 spec AC-1: sessions 表有 locked_skill_name/version/frozen_hash 三列。"""
    _setup_schema()
    cols = _sessions_columns()
    assert "locked_skill_name" in cols, f"locked_skill_name missing. cols={sorted(cols)}"
    assert "locked_skill_version" in cols, f"locked_skill_version missing"
    assert "frozen_hash" in cols, f"frozen_hash missing"


def test_sessions_lock_columns_nullable():
    """M3: 锁定列默认 NULL(不破坏现有数据)。

    INSERT 一行 sessions(不指定锁定列)后 SELECT,验证三列均为 NULL。
    """
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval("INSERT INTO sessions DEFAULT VALUES RETURNING id")
            row = await conn.fetchrow(
                "SELECT locked_skill_name, locked_skill_version, frozen_hash "
                "FROM sessions WHERE id = $1",
                session_id,
            )
        finally:
            await conn.close()
        assert row is not None, "INSERT 的 session 未查到"
        assert row["locked_skill_name"] is None
        assert row["locked_skill_version"] is None
        assert row["frozen_hash"] is None

    asyncio.run(_run())


# ── M4 §8.4 eval_datasets.split 列 ─────────────────────────────────────


def _eval_datasets_columns() -> set[str]:
    """返回 eval_datasets 表的列名集合。"""
    async def _run() -> set[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='eval_datasets'"
            )
            return {r["column_name"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_eval_datasets_has_split_column():
    """M4 AC-1: eval_datasets 表含 split VARCHAR(10) NOT NULL 列。"""
    _setup_schema()
    cols = _eval_datasets_columns()
    assert "split" in cols, f"split missing. cols={sorted(cols)}"


def test_eval_datasets_split_defaults_to_test():
    """M4 AC-1: split 列默认 'test'(不指定 split 入库后查得 'test')。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                "INSERT INTO eval_datasets "
                "(sample_id, scenario, skill_name, skill_version, case_type, difficulty, "
                " input, expected_react_trace) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                "m4-default-test", "office", "office", "1.0.0",
                "normal", "easy", "input",
                '{"tool_calls": [], "expected_output_contains": []}',
            )
            row = await conn.fetchrow(
                "SELECT split FROM eval_datasets WHERE sample_id = $1",
                "m4-default-test",
            )
        finally:
            await conn.close()
        assert row is not None
        assert row["split"] == "test"

    asyncio.run(_run())


def test_eval_datasets_split_check_rejects_invalid():
    """M4 AC-1: split 列 CHECK 约束拒绝 'invalid' 值。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO eval_datasets "
                    "(sample_id, scenario, skill_name, skill_version, case_type, difficulty, "
                    " input, expected_react_trace, split) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    "m4-invalid-test", "office", "office", "1.0.0",
                    "normal", "easy", "input",
                    '{"tool_calls": [], "expected_output_contains": []}',
                    "invalid",
                )
        finally:
            await conn.close()

    asyncio.run(_run())


def test_eval_datasets_split_accepts_train_and_test():
    """M4 AC-1: split 列接受 'train' 与 'test' 两个合法值。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                "INSERT INTO eval_datasets "
                "(sample_id, scenario, skill_name, skill_version, case_type, difficulty, "
                " input, expected_react_trace, split) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                "m4-train", "office", "office", "1.0.0",
                "normal", "easy", "input",
                '{"tool_calls": [], "expected_output_contains": []}',
                "train",
            )
            await conn.execute(
                "INSERT INTO eval_datasets "
                "(sample_id, scenario, skill_name, skill_version, case_type, difficulty, "
                " input, expected_react_trace, split) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                "m4-test", "office", "office", "1.0.0",
                "normal", "easy", "input",
                '{"tool_calls": [], "expected_output_contains": []}',
                "test",
            )
            rows = await conn.fetch(
                "SELECT split FROM eval_datasets "
                "WHERE sample_id IN ('m4-train', 'm4-test') ORDER BY sample_id"
            )
        finally:
            await conn.close()
        assert len(rows) == 2
        assert {r["split"] for r in rows} == {"train", "test"}

    asyncio.run(_run())
