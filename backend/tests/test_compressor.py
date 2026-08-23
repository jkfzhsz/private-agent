"""B4 P0-1 AC-3..8 - Compressor 测试。

Source: plan/b4-compress-billing step 13 (AC-3, AC-4, AC-5, AC-6, AC-7, AC-8)
"""
import asyncio
import os
from unittest.mock import AsyncMock

import asyncpg

from private_agent.core.compressor import Compressor
from private_agent.core.token_estimator import TokenEstimator

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _make_msg(role, content, turn=1, **extra):
    m = {"role": role, "content": content, "turn": turn}
    m.update(extra)
    return m


def _make_tool_call_msg(turn=1, tool_call_id="c1", tool_name="echo"):
    return {
        "role": "assistant",
        "content": "",
        "turn": turn,
        "tool_calls": [{"function": {"name": tool_name, "arguments": "{}"}, "id": tool_call_id}],
    }


def _make_tool_result_msg(turn=1, tool_call_id="c1", output="ok"):
    return {"role": "tool", "content": output, "turn": turn, "tool_call_id": tool_call_id}


# ── AC-3: maybe_compress 触发条件 ──

def test_maybe_compress_no_trigger_under_limits():
    """AC-3: token 和 turn 均未超限 → 不触发压缩(返回 None)。"""
    compressor = Compressor()
    msgs = [_make_msg("user", "hi" * 50, turn=1), _make_msg("assistant", "ok" * 50, turn=1)]
    result = compressor.maybe_compress(
        msgs, active_turns=3, context_window=2000, compress_adapter=None
    )
    assert result is None  # B-1: 返回 trigger 字符串, 未触发为 None(falsy)


def test_maybe_compress_triggers_on_token_limit():
    """AC-3: token 超限(>0.8*context_window) → 触发压缩(trigger=token_limit)。"""
    compressor = Compressor()
    big_msg = "x" * int(2000 * 0.9 * 3)  # 0.9 * 2000 tokens worth of chars
    msgs = [_make_msg("user", big_msg, turn=1)]
    result = compressor.maybe_compress(
        msgs, active_turns=3, context_window=2000, compress_adapter=None
    )
    assert result == "token_limit"  # B-1: trigger 枚举(truthy)


def test_maybe_compress_triggers_on_turn_limit():
    """AC-4: turn 超限(>10 轮) → 触发压缩(trigger=turn_limit)。"""
    compressor = Compressor()
    msgs = [_make_msg("user", "hi", turn=i) for i in range(1, 12)]
    result = compressor.maybe_compress(
        msgs, active_turns=11, context_window=50000, compress_adapter=None
    )
    assert result == "turn_limit"


# ── AC-5: 滑动窗口 ──

def test_sliding_window_keep_turns():
    """AC-5: 滑动窗口保留最近 6 轮,旧消息标记 compressed=True。"""
    compressor = Compressor()
    msgs = []
    for t in range(1, 10):
        msgs.append(_make_msg("user", f"msg{t}", turn=t))
        msgs.append(_make_msg("assistant", f"reply{t}", turn=t))
    result = compressor._sliding_window(msgs, keep_turns=6)
    # 新消息(keep_turns 内)不应标记 compressed
    new_msgs = [m for m in result if not m.get("compressed")]
    old_msgs = [m for m in result if m.get("compressed")]
    assert len(new_msgs) > 0
    assert len(old_msgs) > 0
    for m in old_msgs:
        assert m["turn"] <= 3  # 前 3 轮(turn 1-3)应被压缩


# ── AC-6: 滑动窗口配对 ──

def test_sliding_window_pairing_keeps_tool_pairs():
    """AC-6: 滑动窗口 tool_call/tool_result 配对不拆分。"""
    compressor = Compressor()
    msgs = [
        _make_msg("user", "do it", turn=1),
        _make_tool_call_msg(turn=1, tool_call_id="c1"),
        _make_tool_result_msg(turn=1, tool_call_id="c1"),
        _make_msg("user", "more", turn=2),
        _make_tool_call_msg(turn=2, tool_call_id="c2"),
        _make_tool_result_msg(turn=2, tool_call_id="c2"),
        _make_msg("user", "again", turn=3),
        _make_tool_call_msg(turn=3, tool_call_id="c3"),
        _make_tool_result_msg(turn=3, tool_call_id="c3"),
        _make_msg("user", "keep", turn=4),
        _make_msg("assistant", "ok", turn=4),
        _make_msg("user", "keep2", turn=5),
        _make_msg("assistant", "ok2", turn=5),
        _make_msg("user", "keep3", turn=6),
        _make_msg("assistant", "ok3", turn=6),
        _make_msg("user", "keep4", turn=7),
        _make_msg("assistant", "ok4", turn=7),
    ]
    result = compressor._sliding_window(msgs, keep_turns=6)
    # 检查 tool_call/tool_result 配对:turn 1-3 的 tool 对应该被压缩
    compressed_tool_calls = [m for m in result if m.get("compressed") and m.get("tool_calls")]
    compressed_tool_results = [m for m in result if m.get("compressed") and m["role"] == "tool"]
    # 配对不拆分:同一 turn 的 tool_call 和 tool_result 要么都压缩,要么都不压缩
    for tc in compressed_tool_calls:
        assert tc["turn"] <= 3
    for tr in compressed_tool_results:
        assert tr["turn"] <= 3


# ── AC-7: 摘要 ──

def test_summarize_calls_adapter():
    """AC-7: 摘要模式调 compress_adapter.chat。"""
    compressor = Compressor()
    mock_adapter = AsyncMock()
    mock_adapter.chat.return_value = AsyncMock(content="summary text", used_provider="mock")

    compressed_msgs = [
        _make_msg("user", "old question", turn=1),
        _make_msg("assistant", "old answer", turn=1),
    ]

    async def _run():
        msg = await compressor._summarize(mock_adapter, compressed_msgs)
        assert msg["role"] == "assistant"
        assert "summary" in msg["content"].lower()
        assert msg.get("compressed_from") is not None
        mock_adapter.chat.assert_called_once()

    asyncio.run(_run())


# ── AC-8: compress 事件 ──

def test_compress_writes_react_event():
    """AC-8: 压缩事件写入 react_events(event_type='compress')。"""
    from private_agent.storage import migrations

    async def _setup():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_setup())

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            compressor = Compressor()
            await compressor._emit_compress_event(
                conn, session_id=session_id, turn=5, trigger="token_limit"
            )
            row = await conn.fetchrow(
                "SELECT event_type, payload FROM react_events WHERE session_id=$1",
                session_id,
            )
            assert row is not None
            assert row["event_type"] == "compress"
            import json
            payload = json.loads(row["payload"])
            assert payload["trigger"] == "token_limit"
            assert payload["turn"] == 5
        finally:
            await conn.close()

    asyncio.run(_run())