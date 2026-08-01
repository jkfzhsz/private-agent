"""蓝图 §3.9/§3.10 上下文压缩 — 三类策略(滑动窗口/摘要/Stable Zone 合并)。

B4 P0-1: 检查触发条件(token 超限/轮次超限),执行压缩,写入 compress 事件。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from private_agent.core.token_estimator import TokenEstimator

if TYPE_CHECKING:
    import asyncpg


class Compressor:
    """上下文压缩器(蓝图 §3.9/§3.10)。

    三类策略:
    1. 滑动窗口: 保留最近 keep_turns 轮,旧消息标记 compressed=True
    2. 摘要: 调 compress_adapter 生成摘要消息
    3. Stable Zone 合并: 每 5 轮或 >20 条时合并(留 V2)
    """

    def __init__(self) -> None:
        self._estimator = TokenEstimator()

    def maybe_compress(
        self,
        messages: list[dict],
        *,
        active_turns: int,
        context_window: int,
        compress_adapter: Any = None,
    ) -> bool:
        if active_turns > 10:
            return True
        tokens = self._estimator.estimate_messages(messages)
        if tokens > context_window * 0.8:
            return True
        return False

    def _sliding_window(
        self, messages: list[dict], keep_turns: int = 6
    ) -> list[dict]:
        if not messages:
            return messages
        max_turn = max(m.get("turn", 0) for m in messages)
        keep_from = max(1, max_turn - keep_turns + 1)

        # 收集 tool_call_id 映射,确保配对不被拆分
        tool_call_turns: dict[str, int] = {}
        for m in messages:
            for tc in m.get("tool_calls", []):
                cid = tc.get("id", "")
                if cid:
                    tool_call_turns[cid] = m.get("turn", 0)

        result = []
        for m in messages:
            msg = dict(m)
            turn = msg.get("turn", 0)
            if turn < keep_from:
                # 检查是否有 tool_result 配对在 keep_from 之后
                tid = msg.get("tool_call_id", "")
                if tid and tid in tool_call_turns:
                    call_turn = tool_call_turns[tid]
                    if call_turn >= keep_from:
                        msg["compressed"] = False
                        result.append(msg)
                        continue
                msg["compressed"] = True
            result.append(msg)
        return result

    async def _summarize(
        self, compress_adapter: Any, compressed_msgs: list[dict]
    ) -> dict:
        summary_prompt = (
            "Summarize the following conversation concisely, preserving key facts, "
            "decisions, and action items:\n\n"
        )
        for m in compressed_msgs:
            role = m.get("role", "unknown")
            content = m.get("content", "") or ""
            summary_prompt += f"[{role}]: {content[:500]}\n"

        result = await compress_adapter.chat(
            [{"role": "user", "content": summary_prompt}], tools=[]
        )
        return {
            "role": "assistant",
            "content": f"[Previous Context Summary]\n{result.content}",
            "compressed_from": [id(m) for m in compressed_msgs],
        }

    async def _emit_compress_event(
        self,
        conn: "asyncpg.Connection",
        *,
        session_id: int,
        turn: int,
        trigger: str,
    ) -> None:
        from private_agent.storage.react_events import insert_react_event

        await insert_react_event(
            conn,
            session_id=session_id,
            turn=turn,
            event_type="compress",
            payload={"trigger": trigger, "turn": turn},
        )