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
    {
        "thinking",
        "tool_call",
        "tool_result",
        "final",
        "error",
        "checkpoint",
        "sandbox_execution",
        "memory_extracted",
        "memory_evicted",  # §4.4 [MVP]: 记忆淘汰事件(蓝图要求单独记录)
        "compress",
        "token_usage",
        "injection_alert",
        "injection_blocked",
        "tool_error",
        # 流式输出增量(逐句/逐字返回, Phase 2 流式对话)
        "delta",
        # V2 P1: 工具权限确认(蓝图 §5.12)
        "tool_confirmation_required",
        "tool_confirmation_result",
        # 项目优化(opencode Doom Loop 借鉴): 工具调用死循环检测告警
        "tool_loop_detected",
        # V1.5 项-1(ADR-012 M4): 子代理可观测事件(stalled/kill/zombie/心跳
        # 故障, 具体类型见 payload.kind)
        "subagent",
    }
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
