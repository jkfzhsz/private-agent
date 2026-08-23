"""V2 上下文工程 - 上下文压缩执行链路测试(AI-Agents-in-Depth §2.7.4 + 蓝图 §3.9)。

覆盖:
- Compressor.execute: 滑动窗口 + 摘要(有 compress_adapter) / 纯滑动窗口降级(无 adapter)
- ReactLoop._maybe_compress: turn 超限触发真实压缩, 消息标记 compressed
- get_messages 过滤 compressed 消息(不进 API)
- 压缩摘要落库 + react_events compress 事件
- 状态栏注入(ReactLoop 构建消息时追加 <agent_status> user 消息)
"""
import asyncio
import json
import os
from unittest.mock import AsyncMock

import asyncpg

from private_agent.core.compressor import Compressor
from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.models.base import ChatResult
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
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


def _make_msg(role, content, turn=1, **extra):
    m = {"role": role, "content": content, "turn": turn}
    m.update(extra)
    return m


# ── Compressor.execute ──

def test_execute_sliding_window_only_without_adapter():
    """无 compress_adapter: 纯滑动窗口, 旧消息标记 compressed, 无摘要。"""
    compressor = Compressor()
    msgs = []
    for t in range(1, 10):
        msgs.append(_make_msg("user", f"msg{t}", turn=t, msg_id=t))
        msgs.append(_make_msg("assistant", f"reply{t}", turn=t, msg_id=100 + t))

    async def _run():
        result = await compressor.execute(msgs, keep_turns=6, compress_adapter=None)
        assert result["summary"] is None
        assert len(result["compressed_msgs"]) > 0
        # 保留消息中无 compressed 标记
        for m in result["messages"]:
            assert not m.get("compressed")
        return result

    result = asyncio.run(_run())
    assert all(m.get("turn", 99) <= 3 for m in result["compressed_msgs"])


def test_execute_with_adapter_generates_summary():
    """有 compress_adapter: 生成摘要消息并置于保留消息头部。"""
    compressor = Compressor()
    msgs = []
    for t in range(1, 9):
        msgs.append(_make_msg("user", f"q{t}", turn=t, msg_id=t))
        msgs.append(_make_msg("assistant", f"a{t}", turn=t, msg_id=100 + t))

    mock_adapter = AsyncMock()
    mock_adapter.chat.return_value = ChatResult(
        content="已完成的讨论摘要", used_provider="mock"
    )

    async def _run():
        result = await compressor.execute(
            msgs, keep_turns=6, compress_adapter=mock_adapter
        )
        assert result["summary"] is not None
        assert result["summary"]["content"].startswith("[Previous Context Summary]")
        # 摘要置于头部(保留消息第一项)
        assert result["messages"][0] is result["summary"]
        assert len(result["compressed_msgs"]) > 0
        mock_adapter.chat.assert_called_once()
        return result

    asyncio.run(_run())


def test_execute_summary_failure_degrades_to_window():
    """摘要失败(adapter 抛错): 降级为纯滑动窗口, 不中断。"""
    compressor = Compressor()
    msgs = []
    for t in range(1, 9):
        msgs.append(_make_msg("user", f"q{t}", turn=t, msg_id=t))
        msgs.append(_make_msg("assistant", f"a{t}", turn=t, msg_id=100 + t))

    mock_adapter = AsyncMock()
    mock_adapter.chat.side_effect = RuntimeError("upstream down")

    async def _run():
        result = await compressor.execute(
            msgs, keep_turns=6, compress_adapter=mock_adapter
        )
        assert result["summary"] is None
        assert len(result["compressed_msgs"]) > 0
        return result

    asyncio.run(_run())


# ── ReactLoop 集成: 压缩触发 + 落库 ──

class _MockAdapter:
    provider_name = "mock"

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.chat_calls = []

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
        self.chat_calls.append(list(messages))
        if self._idx >= len(self._responses):
            raise RuntimeError("mock exhausted")
        r = self._responses[self._idx]
        self._idx += 1
        return r


async def _create_session(conn):
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-compress", "mock",
    )


def test_react_loop_compression_marks_old_messages():
    """turn 超限触发压缩: 旧消息 DB compressed=true, 摘要事件入库。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            # 预置 11 轮历史(turn 1..11, 模拟长对话)
            for t in range(1, 12):
                await cm.append_user_message(conn, turn=t, content=f"user msg {t}")
                await cm.append_assistant_message(conn, turn=t, content=f"asst {t}")
            # 重建内存(模拟续聊 reload)
            await cm.reload_from_db(conn)

            adapter = _MockAdapter(responses=[
                ChatResult(content="final answer", used_provider="mock"),
            ])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                cfg={"context": {"compression": {"enabled": True, "keep_turns": 6}}},
            )
            await loop.run_turn("再问一个问题")
            # 压缩已触发(turn=12 > 10)
            n_compressed = await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE session_id=$1 AND compressed=TRUE",
                session_id,
            )
            assert n_compressed > 0
            # compress 事件入库
            ev = await conn.fetchrow(
                "SELECT event_type FROM react_events "
                "WHERE session_id=$1 AND event_type='compress'",
                session_id,
            )
            assert ev is not None
            # get_messages 过滤 compressed: 进 API 的消息数 < 全部消息数
            api_msgs = cm.get_messages()
            meta_msgs = cm.get_messages_with_meta()
            assert len(api_msgs) < len(meta_msgs)
            # §3.10 [MVP] 压缩存档: 被压缩消息归档到 messages_archive
            n_archived = await conn.fetchval(
                "SELECT COUNT(*) FROM messages_archive WHERE session_id=$1",
                session_id,
            )
            assert n_archived == n_compressed
        finally:
            await conn.close()

    asyncio.run(_run())


def test_react_loop_compression_circuit_breaker():
    """熔断器: 连续失败 3 次后禁用本会话压缩。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            for t in range(1, 12):
                await cm.append_user_message(conn, turn=t, content=f"u{t}")
                await cm.append_assistant_message(conn, turn=t, content=f"a{t}")
            await cm.reload_from_db(conn)

            adapter = _MockAdapter(responses=[
                ChatResult(content="ok", used_provider="mock"),
                ChatResult(content="ok2", used_provider="mock"),
            ])
            # compress_adapter 抛错 → 摘要失败 → 熔断计数
            bad_adapter = AsyncMock()
            bad_adapter.chat.side_effect = RuntimeError("boom")
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                cfg={"context": {"compression": {"enabled": True, "keep_turns": 6}}},
                compress_adapter=bad_adapter,
            )
            await loop.run_turn("问题")
            await loop.run_turn("问题2")
            # 连续 2 次失败(每次 run_turn 触发一次 _maybe_compress)
            assert loop._compress_failures >= 1
            assert not loop._compress_disabled
        finally:
            await conn.close()

    asyncio.run(_run())


# ── ReactLoop 集成: 状态栏注入 ──

def test_react_loop_injects_status_bar():
    """状态栏注入: 每次模型调用消息末尾追加 <agent_status> user 消息。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            adapter = _MockAdapter(responses=[
                ChatResult(content="final", used_provider="mock"),
            ])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                cfg={"context": {"status_bar": {"enabled": True, "inject_per_turn": True}}},
            )
            await loop.run_turn("hello")
            # 最后一次 chat 调用末尾应为状态栏 user 消息
            last_messages = adapter.chat_calls[-1]
            assert last_messages[-1]["role"] == "user"
            assert "<agent_status>" in last_messages[-1]["content"]
            assert "当前时间:" in last_messages[-1]["content"]
            assert "当前状态:" in last_messages[-1]["content"]
        finally:
            await conn.close()

    asyncio.run(_run())


def test_react_loop_status_bar_disabled_when_configured():
    """状态栏可配置关闭(enabled=false 时不注入)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            adapter = _MockAdapter(responses=[
                ChatResult(content="final", used_provider="mock"),
            ])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                cfg={"context": {"status_bar": {"enabled": False}}},
            )
            await loop.run_turn("hello")
            last_messages = adapter.chat_calls[-1]
            # 末尾是普通 assistant 消息, 无状态栏
            assert "<agent_status>" not in last_messages[-1]["content"]
        finally:
            await conn.close()

    asyncio.run(_run())
