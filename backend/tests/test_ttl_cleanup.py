"""B4.3 - TTL 清理调度(react_events 30 天 + messages_archive 90 天)。

Source: plan/m0-implementation step 4 (蓝图 §2.10 第 2、3 条)

蓝图 §2.10:
- 第 2 条:react_events TTL 清理,默认保留 30 天,超期自动删除
- 第 3 条:messages 归档策略,会话关闭 90 天后转储 messages_archive
- 清理任务在 sidecar 启动时与每日定时执行
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import asyncpg

from private_agent.storage import migrations, ttl_cleanup
from private_agent.storage.react_events import insert_react_event

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
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


def _create_session(conn: "asyncpg.Connection") -> int:
    """创建一个 session,返回 id。"""
    return asyncio.get_event_loop().run_until_complete(
        conn.fetchval("INSERT INTO sessions DEFAULT VALUES RETURNING id")
    )


def test_cleanup_react_events_deletes_expired():
    """超期(>retention_days)的 react_events 被删除,保留期内的保留。"""
    _setup_schema()

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            # 插入 31 天前的事件(超期)
            old_id = await insert_react_event(
                conn, session_id=session_id, turn=1,
                event_type="thinking", payload={"test": "old"},
            )
            await conn.execute(
                "UPDATE react_events SET created_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(days=31),
                old_id,
            )
            # 插入 7 天前的事件(保留期内)
            recent_id = await insert_react_event(
                conn, session_id=session_id, turn=2,
                event_type="final", payload={"test": "recent"},
            )
            await conn.execute(
                "UPDATE react_events SET created_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(days=7),
                recent_id,
            )
            # cleanup,默认 30 天
            deleted = await ttl_cleanup.cleanup_react_events(conn, retention_days=30)
            # 查剩余
            remaining = await conn.fetchval("SELECT COUNT(*) FROM react_events")
            return deleted, remaining
        finally:
            await conn.close()

    deleted, remaining = asyncio.run(_run())
    assert deleted == 1, f"应删除 1 条超期记录,实际删除 {deleted}"
    assert remaining == 1, f"应保留 1 条记录,实际剩余 {remaining}"


def test_cleanup_react_events_zero_when_nothing_expired():
    """无超期记录时返回 0。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await insert_react_event(
                conn, session_id=session_id, turn=1,
                event_type="thinking", payload={"test": "fresh"},
            )
            return await ttl_cleanup.cleanup_react_events(conn, retention_days=30)
        finally:
            await conn.close()

    deleted = asyncio.run(_run())
    assert deleted == 0


def test_cleanup_messages_archive_deletes_expired():
    """messages_archive 超 90 天的记录被删除。"""
    _setup_schema()

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 插入 91 天前的归档(超期)
            old_id = await conn.fetchval(
                "INSERT INTO messages_archive (original_msg_id, session_id, turn, role, content, zone) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                1001, 1, 1, "user", "old archived message", "active",
            )
            await conn.execute(
                "UPDATE messages_archive SET archived_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(days=91),
                old_id,
            )
            # 插入 30 天前的归档(保留期内)
            recent_id = await conn.fetchval(
                "INSERT INTO messages_archive (original_msg_id, session_id, turn, role, content, zone) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                1002, 1, 2, "assistant", "recent archived message", "active",
            )
            await conn.execute(
                "UPDATE messages_archive SET archived_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(days=30),
                recent_id,
            )
            deleted = await ttl_cleanup.cleanup_messages_archive(conn, retention_days=90)
            remaining = await conn.fetchval("SELECT COUNT(*) FROM messages_archive")
            return deleted, remaining
        finally:
            await conn.close()

    deleted, remaining = asyncio.run(_run())
    assert deleted == 1, f"应删除 1 条超期归档,实际删除 {deleted}"
    assert remaining == 1, f"应保留 1 条归档,实际剩余 {remaining}"


def test_run_ttl_cleanup_runs_both_and_returns_summary():
    """run_ttl_cleanup 同时清理 react_events + messages_archive,返回汇总。"""
    _setup_schema()

    async def _run() -> dict[str, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            # 超期 react_event
            old_event_id = await insert_react_event(
                conn, session_id=session_id, turn=1,
                event_type="thinking", payload={"t": 1},
            )
            await conn.execute(
                "UPDATE react_events SET created_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(days=31),
                old_event_id,
            )
            # 超期 messages_archive
            await conn.execute(
                "INSERT INTO messages_archive (original_msg_id, session_id, turn, role, content, zone, archived_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                2001, session_id, 1, "user", "x", "active",
                datetime.now(timezone.utc) - timedelta(days=91),
            )
            summary = await ttl_cleanup.run_ttl_cleanup(
                conn,
                react_events_retention_days=30,
                messages_archive_retention_days=90,
            )
            return summary
        finally:
            await conn.close()

    summary = asyncio.run(_run())
    assert summary["react_events_deleted"] == 1
    assert summary["messages_archive_deleted"] == 1


# ── M4 §8.16 eval_runs TTL 清理 ────────────────────────────────────────


def test_cleanup_old_eval_runs_keeps_recent_deletes_old():
    """AC-10: cleanup_old_eval_runs 保留最近 100 条,删除超出部分。

    插入 105 条 eval_runs,keep_recent=100,应删除 5 条,剩余 100 条。
    """
    _setup_schema()

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 插入 105 条 eval_runs(各字段均合法)
            for i in range(105):
                await conn.execute(
                    "INSERT INTO eval_runs (skill_name, skill_version, model_id, "
                    "dataset_version, eval_mode, mock_enabled) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    "office", "1.0.0", "glm-4-flash", "v1", "offline", False,
                )
            deleted = await ttl_cleanup.cleanup_old_eval_runs(conn, keep_recent=100)
            remaining = await conn.fetchval("SELECT COUNT(*) FROM eval_runs")
            return deleted, remaining
        finally:
            await conn.close()

    deleted, remaining = asyncio.run(_run())
    assert deleted == 5, f"应删除 5 条旧记录,实际删除 {deleted}"
    assert remaining == 100, f"应保留 100 条,实际剩余 {remaining}"


def test_cleanup_old_eval_runs_zero_when_under_threshold():
    """AC-10: eval_runs 数量 < keep_recent 时返回 0,不删除。"""
    _setup_schema()

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            for i in range(10):
                await conn.execute(
                    "INSERT INTO eval_runs (skill_name, skill_version, model_id, "
                    "dataset_version, eval_mode, mock_enabled) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    "office", "1.0.0", "glm-4-flash", "v1", "offline", False,
                )
            deleted = await ttl_cleanup.cleanup_old_eval_runs(conn, keep_recent=100)
            remaining = await conn.fetchval("SELECT COUNT(*) FROM eval_runs")
            return deleted, remaining
        finally:
            await conn.close()

    deleted, remaining = asyncio.run(_run())
    assert deleted == 0, f"应删除 0 条,实际删除 {deleted}"
    assert remaining == 10, f"应保留 10 条,实际剩余 {remaining}"


def test_cleanup_old_eval_runs_empty_table_returns_zero():
    """AC-10: 空表时返回 0。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await ttl_cleanup.cleanup_old_eval_runs(conn, keep_recent=100)
        finally:
            await conn.close()

    deleted = asyncio.run(_run())
    assert deleted == 0
