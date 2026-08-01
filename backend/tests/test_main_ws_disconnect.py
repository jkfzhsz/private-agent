"""B3 P0-3 AC-11 - WebSocket 断连标记 sessions.status='interrupted'。

Source: plan/b3-injection-protection-checkpoint step 12 (AC-11)
不依赖真实 WebSocket,用 asyncpg 直接创建 session,模拟 mark_session_interrupted 调用。
"""
import asyncio
import os

import asyncpg

from private_agent.core.checkpoint import CheckpointManager

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
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


def test_ws_disconnect_marks_session_interrupted():
    """AC-11: 模拟 WS 断连 → sessions.status 变为 'interrupted'。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            # 模拟 WebSocket 断连场景:先插入 checkpoint,再标记 interrupted
            await CheckpointManager.save_checkpoint(
                conn,
                session_id=session_id,
                turn=3,
                ctx_summary={
                    "frozen_zone_len": 1,
                    "stable_zone_len": 0,
                    "active_zone_msg_count": 5,
                    "active_zone_turn_range": [1, 3],
                },
            )
            await CheckpointManager.mark_session_interrupted(conn, session_id)

            status = await conn.fetchval(
                "SELECT status FROM sessions WHERE id=$1", session_id
            )
            assert status == "interrupted"

            # 同时验证 checkpoint 事件已写入
            row = await conn.fetchrow(
                "SELECT event_type FROM react_events WHERE session_id=$1 AND event_type='checkpoint'",
                session_id,
            )
            assert row is not None
        finally:
            await conn.close()

    asyncio.run(_run())