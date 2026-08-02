"""蓝图 §2.3 line 445-450 ws_offset 补发机制 + M1 ACK 回写与服务端权威。

B3.2b:重连时客户端发送上次 offset,服务端从 react_events 表
查询 turn > offset 的事件补发(按 turn, id 升序)。

M1 Phase 1 step 4:
- handle_ack(conn, session_id, turn):客户端 ACK 后回写 config_runtime ws_offset:{session_id}=turn
- build_replay_messages 优先读 config_runtime(服务端权威),
  effective_offset = max(config_runtime, last_turn),客户端 last_turn 作 fallback。

蓝图 §2.3:
- config_runtime 表存储 ws_offset:{session_id} = 客户端最大已接收 turn 值
- 重连时客户端发送上次 offset,服务端查询 turn > offset 的事件补发
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg


async def fetch_react_events_since_turn(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    last_turn: int,
) -> list[dict[str, Any]]:
    """查询 turn > last_turn 的 react_events,按 (turn, id) 升序返回(蓝图 §2.3 line 449)。

    用于客户端断线重连时补发遗漏事件。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID(补发按会话隔离)。
        last_turn: 客户端最大已接收 turn 值(返回 turn > last_turn 的事件)。

    Returns:
        事件 dict 列表,每个 dict 含 id/session_id/turn/event_type/payload/created_at 字段。
        payload 解析为 Python 原生 dict(asyncpg JSONB 默认返回 JSON 字符串)。
    """
    rows = await conn.fetch(
        """
        SELECT id, session_id, turn, event_type, payload, created_at
        FROM react_events
        WHERE session_id = $1 AND turn > $2
        ORDER BY turn ASC, id ASC
        """,
        session_id,
        last_turn,
    )
    return [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "turn": r["turn"],
            "event_type": r["event_type"],
            "payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def get_ws_offset(
    conn: asyncpg.Connection,
    *,
    session_id: int,
) -> int:
    """读取 config_runtime 表中 ws_offset:{session_id} 的值(蓝图 §2.3 line 447)。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID。

    Returns:
        客户端最大已接收 turn 值;未记录时返回 0。
    """
    key = f"ws_offset:{session_id}"
    value = await conn.fetchval(
        "SELECT value FROM config_runtime WHERE key = $1",
        key,
    )
    if value is None:
        return 0
    # asyncpg JSONB 返回 JSON 字符串
    if isinstance(value, str):
        return int(json.loads(value))
    return int(value)


async def update_ws_offset(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    turn: int,
) -> None:
    """更新 config_runtime 表中 ws_offset:{session_id} 的值(蓝图 §2.3 line 447)。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID。
        turn: 客户端最大已接收 turn 值。
    """
    key = f"ws_offset:{session_id}"
    value_json = json.dumps(turn)
    await conn.execute(
        """
        INSERT INTO config_runtime (key, value, updated_at)
        VALUES ($1, $2::jsonb, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key,
        value_json,
    )


async def handle_ack(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    turn: int,
) -> None:
    """处理客户端 ACK:回写 config_runtime ws_offset:{session_id}=turn(蓝图 §2.3 line 447)。

    客户端确认收到 turn 后,服务端记录权威 offset,供后续 replay 优先使用。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID。
        turn: 客户端确认已接收的最大 turn 值。
    """
    await update_ws_offset(conn, session_id=session_id, turn=turn)


async def build_replay_messages(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    last_turn: int,
    full: bool = False,
) -> list[dict[str, Any]]:
    """构造 WS replay 消息序列(蓝图 §2.3 line 449 + M1 服务端权威)。

    优先读 config_runtime 的 ws_offset:{session_id}(服务端权威),
    effective_offset = max(config_runtime_offset, last_turn),
    查询 turn > effective_offset 的事件补发。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID。
        last_turn: 客户端最大已接收 turn 值(fallback)。
        full: 全量加载(切换历史会话场景)。True 时忽略服务端 ws_offset,
            从 last_turn(客户端传 0)开始拉取, 并把 messages 表的 user 消息
            合并为 user 事件补进事件流(react_events 不存 user 事件)。

    Returns:
        WS 消息 dict 列表:
        - 前 N 条:{"type": "react_event", "session_id": ..., "turn": ..., "event_type": ..., "payload": ...}
        - 末 1 条:{"type": "replay_end", "session_id": ..., "count": N, "effective_offset": M}
    """
    if full:
        # 全量: 忽略服务端权威 offset, 客户端 last_turn=0 即从第一轮拉
        effective_offset = last_turn
    else:
        config_offset = await get_ws_offset(conn, session_id=session_id)
        effective_offset = max(config_offset, last_turn)
    events = await fetch_react_events_since_turn(
        conn, session_id=session_id, last_turn=effective_offset,
    )
    # 包装为 WS react_event 消息(原始 events 无 type 字段)
    event_msgs: list[dict[str, Any]] = [
        {
            "type": "react_event",
            "session_id": e["session_id"],
            "turn": e["turn"],
            "event_type": e["event_type"],
            "payload": e["payload"],
        }
        for e in events
    ]
    # 补 user 事件(messages 表 user 消息, turn > offset) —— react_events 不存 user
    user_rows = await conn.fetch(
        """
        SELECT turn, content FROM messages
        WHERE session_id = $1 AND role = 'user' AND turn > $2
        ORDER BY id ASC
        """,
        session_id,
        effective_offset,
    )
    user_events = [
        {
            "type": "react_event",
            "session_id": session_id,
            "turn": r["turn"],
            "event_type": "user",
            "payload": {"content": r["content"], "turn": r["turn"]},
        }
        for r in user_rows
    ]
    # 合并排序: user 事件位于该轮最前(渲染分组需要 user 在 turn 组内)
    merged: list[tuple[int, int, dict[str, Any]]] = [
        (e["turn"], 1, e) for e in event_msgs
    ]
    merged.extend((u["turn"], 0, u) for u in user_events)
    merged.sort(key=lambda x: (x[0], x[1]))
    messages: list[dict[str, Any]] = [item[2] for item in merged]
    messages.append({
        "type": "replay_end",
        "session_id": session_id,
        "count": len(events) + len(user_events),
        "effective_offset": effective_offset,
    })
    return messages
