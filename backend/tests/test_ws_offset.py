"""B3.2b - ws_offset 补发机制(基于 turn 维度的 react_events 补发)。

Source: plan/m0-implementation step 3 (蓝图 §2.3 line 445-450 + §9.6 step3)

蓝图 §2.3:
- config_runtime 表存储 ws_offset:{session_id} = 客户端最大已接收 turn 值
- 服务端推送事件时携带 turn 字段
- 重连时客户端发送上次 offset,服务端从 react_events 表查询 turn > offset 的事件补发

0.5.1 A-1(C-4 事件级去重): 新增 last_event_id 事件级增量锚点 ——
- fetch_react_events_since(last_event_id): 查 id > last_event_id(事件级精确补发)
- build_replay_messages(last_event_id): 重放事件带 event_id(与实时推送同源),
  前端据此去重(修复 turn N 中途断线重连时该轮事件全量重放的重复渲染)
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


async def _seed_three_events(conn: "asyncpg.Connection", session_id: int) -> None:
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
            return await ws_offset.fetch_react_events_since(
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
            return await ws_offset.fetch_react_events_since(
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
            return await ws_offset.fetch_react_events_since(
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
            events = await ws_offset.fetch_react_events_since(
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
            events = await ws_offset.fetch_react_events_since(
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


def test_build_replay_messages_filters_confirmation_and_marks_replayed():
    """2026-08-10: replay 过滤 tool_confirmation_required 且带 replayed: True。

    回归: 切回历史会话时后端 replay 重放历史事件, 若含 tool_confirmation_required
    前端会再次弹权限确认框。修复: ① 过滤该事件类型(历史工具调用已执行完毕,
    恢复会话只读回放不应重新确认); ② 其余重放消息带 replayed: True 标记,
    供前端区分实时事件与回放(跳过确认弹窗等实时副作用)。
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            # 插入一条历史确认事件(模拟某轮工具调用走 elevated 确认)
            await insert_react_event(
                conn, session_id=session_id, turn=4,
                event_type="tool_confirmation_required",
                payload={"message": "Allow tool 'code_execution' to execute?"},
            )
            msgs = await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=0, full=True,
            )
            return msgs
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    # 不含确认事件: 3 条业务事件 + 1 条 replay_end
    assert len(msgs) == 4
    assert all(
        m.get("event_type") != "tool_confirmation_required"
        for m in msgs if m["type"] == "react_event"
    )
    # 业务事件均带 replayed: True
    replay_evts = [m for m in msgs if m["type"] == "react_event"]
    assert len(replay_evts) == 3
    assert all(m["replayed"] is True for m in replay_evts)


# ──────────────────────────────────────────────────────────────────────────────
# 0.5.1 A-1(C-4 事件级去重): fetch_react_events_since 按 event_id 增量补发
# ──────────────────────────────────────────────────────────────────────────────


async def _seed_same_turn_events(conn: "asyncpg.Connection", session_id: int) -> None:
    """同一 turn 内插入多条事件(模拟 turn N 中途断线场景)。"""
    for event_type in ["thinking", "delta", "delta", "tool_call", "final"]:
        await insert_react_event(
            conn, session_id=session_id, turn=1,
            event_type=event_type, payload={"turn": 1},
        )


def test_fetch_react_events_since_by_event_id_returns_strict_superset():
    """last_event_id 提供时: 返回 id > last_event_id 的事件(事件级精确补发)。

    回归 C-4 缺口②: turn 粒度补发(turn > offset)在 turn N 中途断线时
    要么全量重放该轮(前端重复渲染), 要么 offset 已推进后永久丢失。
    事件级补发(id 锚点单调)精确返回客户端缺失的事件。
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], list[int]]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_same_turn_events(conn, session_id)
            # 取前两条的 id(客户端已收 thinking + 第 1 条 delta)
            ids = [
                r["id"] for r in await conn.fetch(
                    "SELECT id FROM react_events "
                    "WHERE session_id=$1 ORDER BY id ASC LIMIT 2",
                    session_id,
                )
            ]
            last_event_id = ids[1]
            events = await ws_offset.fetch_react_events_since(
                conn, session_id=session_id, last_turn=0,
                last_event_id=last_event_id,
            )
            return events, ids
        finally:
            await conn.close()

    events, ids = asyncio.run(_run())
    # 返回 id > last_event_id 的剩余 3 条(同一 turn=1 内的后 3 条事件)
    assert len(events) == 3
    assert all(e["id"] > ids[1] for e in events)
    assert all(e["turn"] == 1 for e in events)


def test_fetch_react_events_since_falls_back_to_turn_when_no_event_id():
    """未提供 last_event_id 时回退 turn 粒度(旧协议向后兼容)。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_three_events(conn, session_id)
            return await ws_offset.fetch_react_events_since(
                conn, session_id=session_id, last_turn=1,
            )
        finally:
            await conn.close()

    events = asyncio.run(_run())
    assert [e["turn"] for e in events] == [2, 3]


def test_build_replay_messages_with_event_id_anchor():
    """last_event_id 增量 replay: 事件带 event_id 且只补发缺失事件。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_same_turn_events(conn, session_id)
            ids = [
                r["id"] for r in await conn.fetch(
                    "SELECT id FROM react_events "
                    "WHERE session_id=$1 ORDER BY id ASC",
                    session_id,
                )
            ]
            last_event_id = ids[2]  # 已收前 3 条(thinking + 2 条 delta)
            msgs = await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=1,
                last_event_id=last_event_id,
            )
            return msgs
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    replay_evts = [m for m in msgs if m["type"] == "react_event"]
    # 缺失的后 2 条(tool_call + final), 同 turn 内精确补发
    assert len(replay_evts) == 2
    assert [m["event_type"] for m in replay_evts] == ["tool_call", "final"]
    # 重放事件带 event_id(前端去重锚点)
    assert all(isinstance(m["event_id"], int) for m in replay_evts)
    # replay_end 正常
    assert msgs[-1]["type"] == "replay_end"


def test_build_replay_messages_full_ignores_event_id():
    """full=True(切历史会话全量加载)时忽略 last_event_id。

    跨会话事件 id 不连续, 按 id 过滤会丢新会话早于该 id 的事件。
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_same_turn_events(conn, session_id)
            ids = [
                r["id"] for r in await conn.fetch(
                    "SELECT id FROM react_events "
                    "WHERE session_id=$1 ORDER BY id ASC",
                    session_id,
                )
            ]
            msgs = await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=0,
                full=True, last_event_id=ids[3],
            )
            return msgs
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    replay_evts = [m for m in msgs if m["type"] == "react_event"]
    assert len(replay_evts) == 5  # full 模式全量, 不受 event_id 限制
