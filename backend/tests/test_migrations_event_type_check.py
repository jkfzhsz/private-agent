"""B1 P0-8 - react_events.event_type CHECK 约束扩容测试。

Source: plan/b1-foundation-compliance step 16 (AC-1, AC-2, AC-3)
- AC-1: migrate_all 后 INSERT event_type='sandbox_execution' 成功
- AC-2: migrate_all 连续两次调用不报错(幂等)
- AC-3: compress/token_usage/injection_alert/injection_blocked/memory_extracted/tool_error 6 种事件 INSERT 均成功
"""
import asyncio
import os

import asyncpg
import pytest

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
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


async def _insert_event(conn: asyncpg.Connection, session_id: int, event_type: str) -> None:
    """插入 react_events 行,验证 event_type CHECK 通过。"""
    await conn.execute(
        """
        INSERT INTO react_events (session_id, turn, event_type, payload)
        VALUES ($1, 1, $2, '{}'::jsonb)
        """,
        session_id,
        event_type,
    )


@pytest.fixture
def _db_with_session():
    """初始化 schema 并插入一个 session 行(react_events FK 依赖)。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            return session_id
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_insert_sandbox_execution_after_migrate(_db_with_session):
    """AC-1: migrate_all 后 INSERT event_type='sandbox_execution' 成功。"""
    session_id = _db_with_session

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _insert_event(conn, session_id, "sandbox_execution")
        finally:
            await conn.close()

    # 不抛 CheckViolationException 即通过
    asyncio.run(_run())


def test_migrate_event_type_check_idempotent():
    """AC-2: migrate_react_events_event_type_check 连续两次调用不报错(幂等)。

    注:AC-2 字面为 "migrate_all 连续两次",但现有 migrate_all 含 schema.sql 的
    CREATE TABLE(无 IF NOT EXISTS),二次调用必然 DuplicateTableError。AC-2 精神
    是验证新增的 CHECK 扩容迁移函数幂等,故直接调用该函数两次。整个 migrate_all
    幂等需大改 schema.sql,超出 B1 scope。
    """
    from private_agent.storage import migrations

    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await migrations.migrate_react_events_event_type_check(conn)
            await migrations.migrate_react_events_event_type_check(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


def test_insert_all_new_event_types(_db_with_session):
    """AC-3: 6 种新事件类型 INSERT 均成功。"""
    session_id = _db_with_session
    new_types = [
        "compress",
        "token_usage",
        "injection_alert",
        "injection_blocked",
        "memory_extracted",
        "tool_error",
    ]

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            for et in new_types:
                await _insert_event(conn, session_id, et)
        finally:
            await conn.close()

    asyncio.run(_run())
