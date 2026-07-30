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
