"""蓝图 §3.13 token 用量记录(2026-08-18 去计价版)。

记录每次模型调用的 token 用量(dialogue/subagent/compress), 写入
react_events(event_type='token_usage')。

变更(2026-08-18, 蒋先生确认 B 方案):
- 删除 DEFAULT_PRICING/_calculate_cost/currency/cost —— 只统计消耗量, 不计价
- TokenUsage 定义上移至 models/base.py(adapter 层解析共用, 避免 models→core 反向依赖)
- cost_type 增加 "subagent"(子代理走 ReactLoop 自动记录, 事件带子会话 id)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from private_agent.models.base import TokenUsage
from private_agent.storage.react_events import insert_react_event

if TYPE_CHECKING:
    import asyncpg


class BillingRecorder:
    """token 用量记录器(蓝图 §3.13)。"""

    async def record_usage(
        self,
        conn: "asyncpg.Connection",
        *,
        session_id: int,
        turn: int,
        model_id: str,
        usage: TokenUsage,
        cost_type: Literal["dialogue", "subagent", "compress"],
    ) -> int:
        payload = {
            "model_id": model_id,
            "cost_type": cost_type,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "total_tokens": usage.total_tokens,
        }
        return await insert_react_event(
            conn,
            session_id=session_id,
            turn=turn,
            event_type="token_usage",
            payload=payload,
        )
