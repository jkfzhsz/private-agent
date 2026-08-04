"""B3.2b - ws_offset 补发机制(基于 turn 维度的 react_events 补发)。

Source: plan/m0-implementation step 3 (蓝图 §2.3 line 445-450 + §9.6 step3)

蓝图 §2.3:
- config_runtime 表存储 ws_offset:{session_id} = 客户端最大已接收 turn 值
- 服务端推送事件时携带 turn 字段
- 重连时客户端发送上次 offset,服务端从 react_events 表查询 turn > offset 的事件补发
"""
import asyncio
import os
from datetime import datetime, timezone

import asyncpg

from private_agent.storage import migrations, ws_offset
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


async def _seed_three_events(conn: asyncpg.Connection, session_id: int) -> None:
    """插入 turn=1,2,3 三条事件。"""
    for turn, event_type in [(1, "thinking"), (2, "tool_call"), (3, "final")]:
        await insert_react_event(
            conn, session_id=session_id, turn=turn,
            event_type=event_type, payload={"turn": turn},
        )


def test_fetch_react_events_since_turn_returns_events_after_offset():
    """turn > last_turn 的事件按 (turn, id) 升序返回。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            return await ws_offset.fetch_react_events_since_turn(
                conn, session_id=session_id, last_turn=1,
            )
        finally:
            await conn.close()

    events = asyncio.run(_run())
    assert len(events) == 2
    assert [e["turn"] for e in events] == [2, 3]
    assert [e["event_type"] for e in events] == ["tool_call", "final"]


def test_fetch_react_events_since_turn_returns_all_when_offset_zero():
    """last_turn=0 时返回全部事件。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            return await ws_offset.fetch_react_events_since_turn(
                conn, session_id=session_id, last_turn=0,
            )
        finally:
            await conn.close()

    events = asyncio.run(_run())
    assert len(events) == 3
    assert [e["turn"] for e in events] == [1, 2, 3]


def test_fetch_react_events_since_turn_returns_empty_when_offset_at_max():
    """last_turn >= 最大 turn 时返回空列表。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            return await ws_offset.fetch_react_events_since_turn(
                conn, session_id=session_id, last_turn=3,
            )
        finally:
            await conn.close()

    events = asyncio.run(_run())
    assert events == []


def test_fetch_react_events_since_turn_isolates_by_session():
    """补发按 session_id 隔离,不返回其他会话的事件。"""
    _setup_schema()

    async def _run() -> tuple[list[dict], int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid_a = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            sid_b = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, sid_a)
            await _seed_three_events(conn, sid_b)
            # 查 sid_a 的补发,不应返回 sid_b 的
            events = await ws_offset.fetch_react_events_since_turn(
                conn, session_id=sid_a, last_turn=0,
            )
            return events, sid_a
        finally:
            await conn.close()

    events, sid_a = asyncio.run(_run())
    assert len(events) == 3
    assert all(e["session_id"] == sid_a for e in events), (
        "补发应按 session_id 隔离"
    )


def test_fetch_react_events_since_turn_includes_required_fields():
    """返回的事件 dict 包含 id/session_id/turn/event_type/payload/created_at 字段。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            events = await ws_offset.fetch_react_events_since_turn(
                conn, session_id=session_id, last_turn=0,
            )
            return events[0]
        finally:
            await conn.close()

    event = asyncio.run(_run())
    required_fields = {"id", "session_id", "turn", "event_type", "payload", "created_at"}
    assert required_fields.issubset(event.keys()), (
        f"缺少字段: {required_fields - event.keys()}"
    )
    # payload 应解析为 dict
    assert isinstance(event["payload"], dict), "payload 应为 dict 类型"
    assert event["payload"]["turn"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# B3.2b - get_ws_offset / update_ws_offset:ws_offset 持久化到 config_runtime
# ──────────────────────────────────────────────────────────────────────────────


def test_get_ws_offset_returns_zero_for_unrecorded_session():
    """未记录的 session_id,get_ws_offset 返回 0。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await ws_offset.get_ws_offset(conn, session_id=999)
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 0


def test_update_then_get_ws_offset_roundtrip():
    """update_ws_offset(session_id, turn) 后 get_ws_offset 返回 turn。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await ws_offset.update_ws_offset(conn, session_id=1, turn=5)
            return await ws_offset.get_ws_offset(conn, session_id=1)
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 5


def test_update_ws_offset_overwrites_previous_value():
    """重复 update 同一 session,新值覆盖旧值。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await ws_offset.update_ws_offset(conn, session_id=1, turn=5)
            await ws_offset.update_ws_offset(conn, session_id=1, turn=8)
            return await ws_offset.get_ws_offset(conn, session_id=1)
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 8


def test_update_ws_offset_no_rollback_on_stale_ack():
    """C-4(架构修订 P0-5): 单调保护 —— stale ack 不允许 offset 回退。

    乱序/重复 ACK(turn 从 8 回退到 5)必须被忽略, 否则重放会重复推送
    已确认事件, 且权威 offset 被拉低导致后续补发错乱。
    """
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await ws_offset.update_ws_offset(conn, session_id=1, turn=8)
            await ws_offset.update_ws_offset(conn, session_id=1, turn=5)  # stale
            return await ws_offset.get_ws_offset(conn, session_id=1)
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 8


def test_ws_offset_isolates_by_session():
    """不同 session 的 ws_offset 互不影响。"""
    _setup_schema()

    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await ws_offset.update_ws_offset(conn, session_id=1, turn=3)
            await ws_offset.update_ws_offset(conn, session_id=2, turn=7)
            return (
                await ws_offset.get_ws_offset(conn, session_id=1),
                await ws_offset.get_ws_offset(conn, session_id=2),
            )
        finally:
            await conn.close()

    s1, s2 = asyncio.run(_run())
    assert s1 == 3
    assert s2 == 7


# ──────────────────────────────────────────────────────────────────────────────
# B3.2b - build_replay_messages:WS replay 消息构造(纯函数,WS 端点调用)
# ──────────────────────────────────────────────────────────────────────────────


def test_build_replay_messages_returns_events_plus_end():
    """build_replay_messages 返回 [react_event x N, replay_end],N=补发事件数。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            return await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=1,
            )
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    assert len(msgs) == 3  # 2 个 react_event + 1 个 replay_end
    assert msgs[0]["type"] == "react_event"
    assert msgs[0]["turn"] == 2
    assert msgs[0]["event_type"] == "tool_call"
    assert msgs[1]["type"] == "react_event"
    assert msgs[1]["turn"] == 3
    assert msgs[2]["type"] == "replay_end"
    assert msgs[2]["count"] == 2


def test_build_replay_messages_last_turn_zero_returns_all():
    """last_turn=0 → 补发全部事件 + replay_end。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            return await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=0,
            )
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    assert len(msgs) == 4  # 3 个 react_event + 1 个 replay_end
    assert [m["turn"] for m in msgs if m["type"] == "react_event"] == [1, 2, 3]
    assert msgs[-1]["count"] == 3


def test_build_replay_messages_no_events_returns_only_end():
    """无事件可补发时,仅返回 [replay_end],count=0。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await ws_offset.build_replay_messages(
                conn, session_id=999, last_turn=0,
            )
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    assert len(msgs) == 1
    assert msgs[0]["type"] == "replay_end"
    assert msgs[0]["count"] == 0


def test_build_replay_messages_react_event_has_required_fields():
    """react_event 消息包含 type/session_id/turn/event_type/payload 字段。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            msgs = await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=0,
            )
            return msgs[0]
        finally:
            await conn.close()

    msg = asyncio.run(_run())
    required = {"type", "session_id", "turn", "event_type", "payload"}
    assert required.issubset(msg.keys()), f"缺少字段: {required - msg.keys()}"
    assert msg["type"] == "react_event"
    assert isinstance(msg["payload"], dict)


def test_build_replay_messages_replay_end_has_required_fields():
    """replay_end 消息包含 type/session_id/count 字段。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            msgs = await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=1,
            )
            return msgs[-1]
        finally:
            await conn.close()

    msg = asyncio.run(_run())
    required = {"type", "session_id", "count"}
    assert required.issubset(msg.keys()), f"缺少字段: {required - msg.keys()}"
    assert msg["type"] == "replay_end"
    assert msg["count"] == 2
