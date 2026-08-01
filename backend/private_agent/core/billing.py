"""蓝图 §3.13 token 计费 — 三类计费(dialogue/compress/eval)。

B4 P0-4: 记录每次模型调用的 token 用量与成本,写入 react_events(event_type='token_usage')。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from private_agent.storage.react_events import insert_react_event

if TYPE_CHECKING:
    import asyncpg

DEFAULT_PRICING = {
    "input_per_1k": 0.001,
    "output_per_1k": 0.002,
    "cached_input_per_1k": 0.0005,
    "currency": "USD",
}


@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int = 0


class BillingRecorder:
    """token 计费记录器(蓝图 §3.13)。"""

    def __init__(self, pricing: dict | None = None) -> None:
        self._pricing = pricing or DEFAULT_PRICING

    def _calculate_cost(
        self,
        model_id: str,
        usage: TokenUsage,
        cost_type: Literal["dialogue", "compress", "eval"],
    ) -> float:
        p = self._pricing
        non_cached = usage.input_tokens - usage.cached_tokens
        input_cost = (
            non_cached / 1000 * p["input_per_1k"]
            + usage.cached_tokens / 1000 * p["cached_input_per_1k"]
        )
        output_cost = usage.output_tokens / 1000 * p["output_per_1k"]
        return round(input_cost + output_cost, 6)

    async def record_usage(
        self,
        conn: "asyncpg.Connection",
        *,
        session_id: int,
        turn: int,
        model_id: str,
        usage: TokenUsage,
        cost_type: Literal["dialogue", "compress", "eval"],
    ) -> int:
        cost = self._calculate_cost(model_id, usage, cost_type)
        payload = {
            "model_id": model_id,
            "cost_type": cost_type,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "total_tokens": usage.total_tokens,
            "currency": self._pricing["currency"],
            "cost": cost,
        }
        return await insert_react_event(
            conn,
            session_id=session_id,
            turn=turn,
            event_type="token_usage",
            payload=payload,
        )