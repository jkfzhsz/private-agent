"""蓝图 §2.13 react_events 入库。

B6.2:ReAct 事件流持久化到 react_events 表。
event_type 枚举:thinking / tool_call / tool_result / final / error / checkpoint。
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg

# 蓝图 §2.13 event_type 合法值
_VALID_EVENT_TYPES = frozenset(
    {"thinking", "tool_call", "tool_result", "final", "error", "checkpoint"}
)


async def insert_react_event(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    turn: int,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """插入一条 react_event,返回自增 id(蓝图 §2.13)。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID(外键 sessions.id)。
        turn: 当前轮次。
        event_type: 事件类型(thinking/tool_call/tool_result/final/error/checkpoint)。
        payload: 事件负载(JSONB)。

    Returns:
        新插入记录的 id。

    Raises:
        ValueError: event_type 不在合法枚举中。
    """
    if event_type not in _VALID_EVENT_TYPES:
        raise ValueError(
            f"event_type='{event_type}' 不合法,合法值: {sorted(_VALID_EVENT_TYPES)}"
        )
    payload_json = json.dumps(payload, ensure_ascii=False)
    event_id = await conn.fetchval(
        """
        INSERT INTO react_events (session_id, turn, event_type, payload)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING id
        """,
        session_id,
        turn,
        event_type,
        payload_json,
    )
    return event_id
