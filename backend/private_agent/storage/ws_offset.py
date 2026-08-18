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

# 2026-08-16(历史任务切换空白修复): replay 重放过滤的流式增量事件类型。
# thinking/delta 是实时流式中间态, 最终内容由 final 承载 —— 长会话
# (数千条增量)重放时逐条推给前端会卡死渲染, 重放只重建最终状态。
_REPLAY_STREAM_TYPES = frozenset({"thinking", "delta"})


async def fetch_react_events_since(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    last_turn: int,
    last_event_id: int | None = None,
) -> list[dict[str, Any]]:
    """查询补发事件(蓝图 §2.3 line 449 + 0.5.1 A-1 事件级去重)。

    两种粒度:
    - last_event_id 提供时(新协议): 查 id > last_event_id —— 事件级精确补发,
      修复 turn N 中途断线重连时该轮事件全量重放的前端重复(增量锚点单调)。
    - 否则(旧协议向后兼容): 查 turn > last_turn —— 轮级补发。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID(补发按会话隔离)。
        last_turn: 客户端最大已接收 turn 值(旧协议, 返回 turn > last_turn 的事件)。
        last_event_id: 客户端最大已接收事件 id(新协议, 返回 id > last_event_id 的事件)。

    Returns:
        事件 dict 列表,每个 dict 含 id/session_id/turn/event_type/payload/created_at 字段。
        payload 解析为 Python 原生 dict(asyncpg JSONB 默认返回 JSON 字符串)。
    """
    if last_event_id is not None:
        rows = await conn.fetch(
            """
            SELECT id, session_id, turn, event_type, payload, created_at
            FROM react_events
            WHERE session_id = $1 AND id > $2
            ORDER BY id ASC
            """,
            session_id,
            last_event_id,
        )
    else:
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
        ON CONFLICT (key) DO UPDATE
        -- C-4(架构修订 P0-5): 单调保护 —— stale ack 不允许 offset 回退
        SET value = EXCLUDED.value, updated_at = now()
        WHERE (EXCLUDED.value::text)::int > (config_runtime.value::text)::int
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
    last_event_id: int | None = None,
) -> list[dict[str, Any]]:
    """构造 WS replay 消息序列(蓝图 §2.3 line 449 + M1 服务端权威 + A-1 事件级)。

    补发粒度: 新协议(last_event_id)按事件 id; 旧协议按 turn。

    Args:
        conn: Postgres 连接。
        session_id: 会话 ID。
        last_turn: 客户端最大已接收 turn 值(fallback)。
        full: 全量加载(切换历史会话场景)。True 时忽略服务端 ws_offset,
            从 last_turn(客户端传 0)开始拉取, 并把 messages 表的 user 消息
            合并为 user 事件补进事件流(react_events 不存 user 事件)。
        last_event_id: 客户端最大已接收事件 id(0.5.1 A-1, 事件级去重锚点)。
            提供时优先于 turn 粒度; user 消息补发按其对应 turn 对齐。

    Returns:
        WS 消息 dict 列表:
        - 前 N 条:{"type": "react_event", "session_id": ..., "turn": ..., "event_type": ..., "payload": ...}
        - 末 1 条:{"type": "replay_end", "session_id": ..., "count": N, "effective_offset": M}
    """
    if full:
        # 全量: 忽略服务端权威 offset, 客户端 last_turn=0 即从第一轮拉
        # 0.5.1 A-1: 同时忽略 last_event_id —— 切历史会话全量加载时, 客户端
        # 已收事件 id 属于其他会话, 跨会话不连续, 按 id 过滤会丢新会话早于
        # 该 id 的事件(用户消息/事件全部重放, 前端按 event_id 去重兜底)。
        effective_offset = last_turn
        last_event_id = None
    else:
        config_offset = await get_ws_offset(conn, session_id=session_id)
        effective_offset = max(config_offset, last_turn)
    # 事件级补发(user 消息对齐下界: last_event_id 对应事件的 turn)
    user_offset = effective_offset
    if last_event_id is not None:
        row = await conn.fetchrow(
            "SELECT turn FROM react_events WHERE session_id = $1 AND id = $2",
            session_id,
            last_event_id,
        )
        if row is not None:
            user_offset = row["turn"]
    events = await fetch_react_events_since(
        conn, session_id=session_id, last_turn=effective_offset,
        last_event_id=last_event_id,
    )
    # 包装为 WS react_event 消息(原始 events 无 type 字段)
    # 2026-08-10 22:00: ① 过滤 tool_confirmation_required —— 历史会话的权限确认
    #   事件在 replay 重放时不应再次触发前端确认弹窗(历史工具调用已执行完毕,
    #   恢复会话只读回放, 不重新执行/确认); ② 所有重放消息带 replayed: True
    #   标记, 供前端区分实时事件与回放(前端据此跳过确认弹窗等实时副作用)。
    # 2026-08-16(蒋先生反馈: 历史任务切换空白/无响应): ③ 过滤流式增量
    #   thinking/delta —— 重放是重建最终状态, 99% 的增量事件(长会话数千条)
    #   一次性推给前端逐条 setState 会卡死渲染(表现为"点进去什么都不显示"
    #   + 后续消息积压"不回答")。最终内容由 final 事件承载, 增量无重放价值。
    event_msgs: list[dict[str, Any]] = [
        {
            "type": "react_event",
            "session_id": e["session_id"],
            "turn": e["turn"],
            "event_type": e["event_type"],
            "payload": e["payload"],
            # 0.5.1 A-1(C-4): 重放事件带 DB id(event_id), 前端据此去重 ——
            # 与实时推送的 event_id 同源(react_loop._emit_event 回填)
            "event_id": e["id"],
            "replayed": True,
        }
        for e in events
        if (
            e["event_type"] != "tool_confirmation_required"
            and e["event_type"] not in _REPLAY_STREAM_TYPES
        )
    ]
    # 补 user 事件(messages 表 user 消息, turn > offset) —— react_events 不存 user
    # C-5(架构修订 P2-6): zone 过滤 —— 仅 active 用户消息重放为气泡,
    # KB/记忆注入(zone='stable')不重放(避免界面污染与上下文误导)
    user_rows = await conn.fetch(
        """
        SELECT turn, content FROM messages
        WHERE session_id = $1 AND role = 'user' AND turn > $2
          AND (zone IS NULL OR zone = 'active')
        ORDER BY id ASC
        """,
        session_id,
        user_offset,
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
    # 2026-08-16(问题2, 蒋先生反馈): 中断轮次(final 事件缺失)补全 ——
    # 未正常结束的轮次(tool_loop_detected/中断)在 react_events 无 final,
    # 但 messages 表已累积 assistant 文本。前端渲染只认 final 事件 →
    # 历史对话显示"思考中"。full 模式下对缺 final 的 turn 从 messages
    # 补一条合成 final 事件(取该 turn 最后一条 assistant 消息)。
    # 已正常结束的 turn 有 final 事件, 不重复补(全库 17 个中断会话受益)。
    if full:
        missing_turn_rows = await conn.fetch(
            """
            SELECT m.turn, m.content
            FROM messages m
            WHERE m.session_id = $1 AND m.role = 'assistant'
              AND m.turn > $2
              AND NOT EXISTS (
                  SELECT 1 FROM react_events e
                  WHERE e.session_id = m.session_id
                    AND e.turn = m.turn AND e.event_type = 'final'
              )
            ORDER BY m.id DESC
            """,
            session_id,
            user_offset,
        )
        # 每个 turn 只补一条(最后一条 assistant 消息)
        seen_turns: set[int] = set()
        for r in missing_turn_rows:
            turn = int(r["turn"])
            if turn in seen_turns:
                continue
            seen_turns.add(turn)
            user_events.append({
                "type": "react_event",
                "session_id": session_id,
                "turn": turn,
                "event_type": "final",
                "payload": {"content": r["content"] or "", "turn": turn},
                "replayed": True,
            })
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
        # 2026-08-16: count 改为实际补发条数(过滤流式增量后 ≠ 原始事件数)
        "count": len(messages),
        "effective_offset": effective_offset,
    })
    return messages
