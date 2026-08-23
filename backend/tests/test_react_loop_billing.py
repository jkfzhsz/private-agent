"""react_loop → billing 集成链路测试(2026-08-19 补 #2 第 11 步缺口)。

Source: 蓝图 §3.13 + 2026-08-18 B 方案(去计价 + 缓存命中率)
覆盖 react_loop L805-817 的计费调用链:
- adapter 返回带 usage 的 ChatResult → record_usage → token_usage 事件真实落库
- adapter 未返回 usage → 守卫兜底, 不写事件(零数据兼容, 不破坏循环)
"""
import asyncio
import os

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.models.base import ChatResult, ModelCapability, TokenUsage
from private_agent.storage import migrations

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


async def _create_session(conn: "asyncpg.Connection") -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-billing",
        "mock-glm",
    )


class _MockAdapter:
    """测试用 mock 适配器, 返回预设 ChatResult(与 test_react_loop 同构)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.chat_calls: list[tuple[list[dict], list[dict] | None]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.chat_calls.append((list(messages), list(tools) if tools else None))
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


def test_run_turn_persists_token_usage_event_when_adapter_returns_usage():
    """adapter 返回带 usage 的 ChatResult 时, token_usage 事件真实落库(集成链路)。

    验证 react_loop → billing.record_usage → react_events(token_usage) 全链路:
    payload 含 input/output/cached/total + cost_type=dialogue + model_id=used_provider。
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            usage = TokenUsage(
                input_tokens=120, output_tokens=30, total_tokens=150, cached_tokens=40
            )
            adapter = _MockAdapter(
                responses=[
                    ChatResult(
                        content="answer", used_provider="mock", usage=usage
                    )
                ]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("question")
            rows = await conn.fetch(
                "SELECT event_type, payload, turn FROM react_events "
                "WHERE session_id=$1 AND event_type='token_usage'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1, f"expect exactly 1 token_usage event, got {len(rows)}"
    assert rows[0]["turn"] == 1
    payload = rows[0]["payload"]
    assert payload["cost_type"] == "dialogue"
    assert payload["model_id"] == "mock"  # 取 used_provider
    assert payload["input_tokens"] == 120
    assert payload["output_tokens"] == 30
    assert payload["cached_tokens"] == 40
    assert payload["total_tokens"] == 150


def test_run_turn_without_usage_skips_token_usage_event():
    """adapter 未返回 usage 时, 不写 token_usage 事件(守卫兜底, 循环不受影响)。"""
    _setup_schema()

    async def _run() -> tuple[int, str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            # usage 缺省 None —— 老 adapter/无 usage 响应的兼容路径
            adapter = _MockAdapter(
                responses=[ChatResult(content="answer", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
            )
            await loop.run_turn("question")
            n = await conn.fetchval(
                "SELECT count(*) FROM react_events "
                "WHERE session_id=$1 AND event_type='token_usage'",
                session_id,
            )
            return n, loop.state.value
        finally:
            await conn.close()

    n, state = asyncio.run(_run())
    assert n == 0, "usage=None 时不应写入 token_usage 事件"
    assert state == "idle", "无 usage 不应影响循环正常结束"
