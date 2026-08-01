"""蓝图 §2.14 checkpoint 机制 — ReAct 循环每轮结束自动写入 checkpoint 到 react_events。

B3 P0-3: MVP 仅存储 checkpoint,不实现恢复逻辑;会话标记 interrupted 后,用户可手动发起"继续"。
V2 断点续传:读取最新 checkpoint 事件 → 从 messages 表恢复完整 ctx → 从中断 turn 继续 ReAct 循环。
"""
from __future__ import annotations

from typing import Any

import asyncpg

from private_agent.storage.react_events import insert_react_event


class CheckpointManager:
    """checkpoint 存储 + 会话中断标记(蓝图 §2.14)。"""

    @staticmethod
    async def save_checkpoint(
        conn: asyncpg.Connection,
        *,
        session_id: int,
        turn: int,
        ctx_summary: dict[str, Any],
    ) -> int:
        """每轮结束写入 checkpoint(蓝图 §2.14)。

        payload 包含当前 turn + ctx 的序列化摘要(不含完整 messages,仅含结构与长度,用于恢复时重建)。

        Returns:
            react_events 自增 id。
        """
        payload = {
            "turn": turn,
            "ctx_summary": ctx_summary,
        }
        return await insert_react_event(
            conn,
            session_id=session_id,
            turn=turn,
            event_type="checkpoint",
            payload=payload,
        )

    @staticmethod
    async def mark_session_interrupted(
        conn: asyncpg.Connection, session_id: int
    ) -> None:
        """标记会话为 interrupted(蓝图 §2.14 用户断线/进程崩溃)。"""
        await conn.execute(
            "UPDATE sessions SET status='interrupted' WHERE id=$1",
            session_id,
        )