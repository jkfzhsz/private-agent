"""§3.10.3 [MVP] Stable Zone 合并压缩测试。

覆盖:
- Compressor.should_merge_stable: 每 N 轮 / KB 超阈值 / 无 KB 不触发
- Compressor.build_merge_prompt: 合并 prompt 结构
- ReactLoop 集成: turn%5 触发合并 → 旧 KB 标记 compressed + merged 消息
  + version_snapshots 存档(scope=stable_zone)
"""
import asyncio
import json
import os

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


async def _create_session(conn):
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-merge", "mock",
    )


# ── should_merge_stable ──

def test_should_merge_stable_turn_interval():
    """每 N=5 轮且存在 KB 片段时触发。"""
    assert Compressor.should_merge_stable(turn=5, kb_count=3, merge_interval=5) is True
    assert Compressor.should_merge_stable(turn=10, kb_count=3, merge_interval=5) is True
    # 非整 5 轮不触发
    assert Compressor.should_merge_stable(turn=4, kb_count=3, merge_interval=5) is False


def test_should_merge_stable_kb_threshold():
    """KB 片段超阈值(>20)触发, 无论轮次。"""
    assert Compressor.should_merge_stable(turn=3, kb_count=21, kb_threshold=20) is True
    assert Compressor.should_merge_stable(turn=3, kb_count=20, kb_threshold=20) is False


def test_should_merge_stable_no_kb_never_triggers():
    """无 KB 片段时不触发(避免误合并纯记忆场景)。"""
    assert Compressor.should_merge_stable(turn=5, kb_count=0, merge_interval=5) is False


def test_build_merge_prompt_structure():
    """合并 prompt 含片段列表 + 输出要求。"""
    msgs = [
        {"role": "user", "content": "[KB Context]\n茅台 2025 营收 1500 亿"},
        {"role": "user", "content": "[KB Context]\n茅台 2026 Q1 营收 400 亿"},
    ]
    prompt = Compressor.build_merge_prompt(msgs)
    assert "[片段1]" in prompt
    assert "[片段2]" in prompt
    assert "[Merged KB Context]" in prompt


# ── ReactLoop 集成: 合并执行 ──

class _MockAdapter:
    provider_name = "mock"

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        r = self._responses[self._idx]
        self._idx += 1
        return r


def test_react_loop_merges_stable_zone_at_interval():
    """turn%5 触发合并: 旧 KB compressed + merged 消息 + 存档。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            # 注入 2 条 KB 片段(turn 1/2)
            await cm.inject_kb_chunks(conn, turn=1, content="片段 A: 茅台 2025 营收 1500 亿")
            await cm.inject_kb_chunks(conn, turn=2, content="片段 B: 茅台 2026 Q1 营收 400 亿")
            assert cm.kb_chunk_count() == 2

            class _CompressAdapter:
                async def chat(self, messages, tools=None, max_tokens=None):
                    return ChatResult(
                        content="[Merged KB Context]\n茅台 2025 营收 1500 亿, 2026 Q1 400 亿",
                        used_provider="mock",
                    )

            # 模拟续聊到 turn=4, 然后 run_turn 使 turn=5(触发合并)
            for t in range(3, 5):
                await cm.append_user_message(conn, turn=t, content=f"u{t}")
                await cm.append_assistant_message(conn, turn=t, content=f"a{t}")
            await cm.reload_from_db(conn)

            adapter = _MockAdapter(responses=[
                ChatResult(content="final", used_provider="mock"),
            ])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                cfg={"context": {"compression": {"enabled": True, "merge_interval_turns": 5}}},
                compress_adapter=_CompressAdapter(),
            )
            await loop.run_turn("第五轮")
            assert loop._turn == 5  # turn%5 触发

            # 旧 KB 片段 compressed
            n_compressed = await conn.fetchval(
                "SELECT COUNT(*) FROM messages "
                "WHERE session_id=$1 AND zone='stable' AND compressed=TRUE",
                session_id,
            )
            assert n_compressed >= 2
            # merged 消息存在且带 [Merged KB Context]
            merged = await conn.fetchval(
                "SELECT COUNT(*) FROM messages "
                "WHERE session_id=$1 AND zone='stable' AND content LIKE '[Merged KB Context]%'",
                session_id,
            )
            assert merged == 1
            # 存档 version_snapshots scope=stable_zone
            snap = await conn.fetchval(
                "SELECT COUNT(*) FROM version_snapshots WHERE scope='stable_zone'"
            )
            assert snap == 1
            # 内存: 旧 KB 标记 compressed, kb_chunk_count 归零(merged 前缀不同)
            assert cm.kb_chunk_count() == 0
        finally:
            await conn.close()

    asyncio.run(_run())


def test_react_loop_merge_skips_without_adapter():
    """无 compress_adapter 时合并跳过(降级不报错)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.inject_kb_chunks(conn, turn=1, content="KB 片段")
            for t in range(2, 5):
                await cm.append_user_message(conn, turn=t, content=f"u{t}")
                await cm.append_assistant_message(conn, turn=t, content=f"a{t}")
            await cm.reload_from_db(conn)
            adapter = _MockAdapter(responses=[
                ChatResult(content="final", used_provider="mock"),
            ])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                cfg={"context": {"compression": {"enabled": True, "merge_interval_turns": 5}}},
                # 不传 compress_adapter → 合并跳过
            )
            await loop.run_turn("第五轮")
            assert loop._turn == 5
            # KB 片段未被压缩, 无存档
            n_compressed = await conn.fetchval(
                "SELECT COUNT(*) FROM messages "
                "WHERE session_id=$1 AND zone='stable' AND compressed=TRUE",
                session_id,
            )
            assert n_compressed == 0
            snap = await conn.fetchval(
                "SELECT COUNT(*) FROM version_snapshots WHERE scope='stable_zone'"
            )
            assert snap == 0
        finally:
            await conn.close()

    asyncio.run(_run())
