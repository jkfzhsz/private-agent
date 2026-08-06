"""B3 P0-3 AC-8..10 - CheckpointManager 测试。

Source: plan/b3-injection-protection-checkpoint step 11 (AC-8, AC-9, AC-10)
"""
import asyncio
import json
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


def test_save_checkpoint_writes_react_event():
    """AC-8: save_checkpoint 写入 react_events 表, event_type='checkpoint'。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            ctx_summary = {
                "frozen_zone_len": 1,
                "stable_zone_len": 2,
                "active_zone_msg_count": 5,
                "active_zone_turn_range": [3, 7],
            }
            await CheckpointManager.save_checkpoint(
                conn, session_id=session_id, turn=3, ctx_summary=ctx_summary
            )
            row = await conn.fetchrow(
                "SELECT event_type, payload FROM react_events WHERE session_id=$1",
                session_id,
            )
            assert row is not None
            assert row["event_type"] == "checkpoint"
            stored = json.loads(row["payload"])
            assert stored["turn"] == 3
            assert stored["ctx_summary"]["frozen_zone_len"] == 1
            assert stored["ctx_summary"]["active_zone_msg_count"] == 5
        finally:
            await conn.close()

    asyncio.run(_run())


def test_save_checkpoint_payload_excludes_full_messages():
    """AC-9: save_checkpoint payload 不含完整 messages(仅含结构和长度摘要)。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            await CheckpointManager.save_checkpoint(
                conn,
                session_id=session_id,
                turn=1,
                ctx_summary={"frozen_zone_len": 1, "stable_zone_len": 0, "active_zone_msg_count": 0, "active_zone_turn_range": [1, 1]},
            )
            row = await conn.fetchrow(
                "SELECT payload FROM react_events WHERE session_id=$1", session_id
            )
            stored = json.loads(row["payload"])
            assert "messages" not in stored
            assert "full_content" not in stored
            assert "ctx_summary" in stored
        finally:
            await conn.close()

    asyncio.run(_run())


def test_mark_session_interrupted_updates_status():
    """AC-10: mark_session_interrupted 后 sessions.status='interrupted'。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            await CheckpointManager.mark_session_interrupted(conn, session_id)
            status = await conn.fetchval(
                "SELECT status FROM sessions WHERE id=$1", session_id
            )
            assert status == "interrupted"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_load_latest_checkpoint_returns_newest():
    """V1.5 项-4: load_latest_checkpoint 返回最新 checkpoint(含 turn/ctx_summary)。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            await CheckpointManager.save_checkpoint(
                conn, session_id=session_id, turn=1,
                ctx_summary={"active_zone_msg_count": 3},
            )
            await CheckpointManager.save_checkpoint(
                conn, session_id=session_id, turn=2,
                ctx_summary={"active_zone_msg_count": 6},
            )
            ckpt = await CheckpointManager.load_latest_checkpoint(conn, session_id)
            assert ckpt is not None
            assert ckpt["turn"] == 2
            assert ckpt["ctx_summary"]["active_zone_msg_count"] == 6
        finally:
            await conn.close()

    asyncio.run(_run())


def test_load_latest_checkpoint_none_when_missing():
    """V1.5 项-4: 无 checkpoint 事件时返回 None。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            ckpt = await CheckpointManager.load_latest_checkpoint(conn, session_id)
            assert ckpt is None
        finally:
            await conn.close()

    asyncio.run(_run())


def test_load_latest_checkpoint_ignores_other_event_types():
    """V1.5 项-4: 只认 event_type='checkpoint', 其他事件不干扰。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            # 先写 final/thinking 事件, 再写 checkpoint
            from private_agent.storage.react_events import insert_react_event

            await insert_react_event(
                conn, session_id=session_id, turn=1,
                event_type="final", payload={"content": "done"},
            )
            await insert_react_event(
                conn, session_id=session_id, turn=2,
                event_type="thinking", payload={"reasoning": "x"},
            )
            await CheckpointManager.save_checkpoint(
                conn, session_id=session_id, turn=2,
                ctx_summary={"active_zone_msg_count": 4},
            )
            ckpt = await CheckpointManager.load_latest_checkpoint(conn, session_id)
            assert ckpt is not None and ckpt["turn"] == 2
        finally:
            await conn.close()

    asyncio.run(_run())