"""B6.2 - react_events 入库。

Source: plan/m0-implementation step 6 (蓝图 §9.6 step6 + §2.13)

蓝图 §2.13 react_events 表:ReAct 事件流持久化。
event_type 枚举:thinking / tool_call / tool_result / final / error / checkpoint。
"""
import asyncio
import json
import os

import asyncpg

from private_agent.storage import migrations, react_events

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_and_create_session() -> int:
    """建表 + 创建一个 session,返回 session_id。"""
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
            # 创建一个 session(react_events.session_id 外键依赖)
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, status) VALUES ($1, $2) RETURNING id",
                "test session",
                "active",
            )
            return session_id
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_insert_react_event_returns_id():
    """插入 react_event 返回 id > 0。"""
    session_id = _setup_and_create_session()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            event_id = await react_events.insert_react_event(
                conn,
                session_id=session_id,
                turn=1,
                event_type="thinking",
                payload={"thought": "analyzing user request"},
            )
            return event_id
        finally:
            await conn.close()

    event_id = asyncio.run(_run())
    assert event_id > 0


def test_inserted_event_has_correct_fields():
    """插入的 react_event 字段与查询结果一致。"""
    session_id = _setup_and_create_session()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await react_events.insert_react_event(
                conn,
                session_id=session_id,
                turn=2,
                event_type="tool_call",
                payload={"tool": "search", "args": {"q": "test"}},
            )
            rows = await conn.fetch(
                "SELECT session_id, turn, event_type, payload FROM react_events WHERE session_id = $1",
                session_id,
            )
            return {
                "session_id": rows[0]["session_id"],
                "turn": rows[0]["turn"],
                "event_type": rows[0]["event_type"],
                "payload": json.loads(rows[0]["payload"]),
            }
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result["session_id"] == session_id
    assert result["turn"] == 2
    assert result["event_type"] == "tool_call"
    assert result["payload"] == {"tool": "search", "args": {"q": "test"}}