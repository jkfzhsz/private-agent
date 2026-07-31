"""M1 Phase 1 step 4 - handle_ack + replay 优先 config_runtime。

Source: plan/m1-react-loop step 4 (蓝图 §2.3 line 445-450)

- handle_ack(conn, session_id, turn):回写 config_runtime ws_offset:{session_id}=turn
- build_replay_messages 优先读 config_runtime,effective_offset = max(config_runtime, last_turn)
  客户端 last_turn 作 fallback(蓝图 §2.3 ws_offset 服务端权威)。
"""
import asyncio
import os

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


async def _seed_events(
    conn: asyncpg.Connection, session_id: int, turns: list[int],
) -> None:
    """插入指定 turn 序列的事件。"""
    for turn in turns:
        await insert_react_event(
            conn, session_id=session_id, turn=turn,
            event_type="thinking", payload={"turn": turn},
        )


# ──────────────────────────────────────────────────────────────────────────────
# handle_ack:回写 config_runtime ws_offset
# ──────────────────────────────────────────────────────────────────────────────


def test_handle_ack_writes_config_runtime():
    """handle_ack(session_id=1, turn=5) 后 get_ws_offset 返回 5。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await ws_offset.handle_ack(conn, session_id=1, turn=5)
            return await ws_offset.get_ws_offset(conn, session_id=1)
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 5


def test_handle_ack_overwrites_previous():
    """两次 handle_ack,后值覆盖前值。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await ws_offset.handle_ack(conn, session_id=1, turn=5)
            await ws_offset.handle_ack(conn, session_id=1, turn=8)
            return await ws_offset.get_ws_offset(conn, session_id=1)
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 8


# ──────────────────────────────────────────────────────────────────────────────
# build_replay_messages:优先 config_runtime offset
# ──────────────────────────────────────────────────────────────────────────────


def test_build_replay_messages_prefers_config_runtime_offset():
    """config_runtime=5,客户端发 last_turn=3 → 补发 turn>5(不是 turn>3)。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_events(conn, session_id, [1, 2, 3, 4, 5, 6])
            # 服务端权威:config_runtime ws_offset=5
            await ws_offset.update_ws_offset(conn, session_id=session_id, turn=5)
            # 客户端 last_turn=3,应被 config_runtime=5 覆盖
            return await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=3,
            )
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    react_turns = [m["turn"] for m in msgs if m["type"] == "react_event"]
    assert react_turns == [6], (
        f"应只补发 turn>5 的事件(config_runtime 权威),实际: {react_turns}"
    )
    assert msgs[-1]["count"] == 1


def test_build_replay_messages_uses_last_turn_when_no_config_runtime():
    """无 config_runtime 时用 last_turn(回退行为)。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_events(conn, session_id, [1, 2, 3])
            # 未设置 config_runtime → effective = last_turn = 1 → 补发 turn>1
            return await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=1,
            )
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    react_turns = [m["turn"] for m in msgs if m["type"] == "react_event"]
    assert react_turns == [2, 3], f"应补发 turn>1 的事件,实际: {react_turns}"


def test_build_replay_messages_uses_max_of_config_and_last_turn():
    """config_runtime=3, last_turn=5 → 用 max=5(客户端领先时不回退)。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions DEFAULT VALUES RETURNING id"
            )
            await _seed_events(conn, session_id, [1, 2, 3, 4, 5, 6, 7])
            # config_runtime=3,客户端 last_turn=5 → effective=max(3,5)=5 → 补发 turn>5
            await ws_offset.update_ws_offset(conn, session_id=session_id, turn=3)
            return await ws_offset.build_replay_messages(
                conn, session_id=session_id, last_turn=5,
            )
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    react_turns = [m["turn"] for m in msgs if m["type"] == "react_event"]
    assert react_turns == [6, 7], (
        f"应补发 turn>5 的事件(max=5),实际: {react_turns}"
    )
