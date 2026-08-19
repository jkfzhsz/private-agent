"""§4.15 [MVP] KB 片段注入 Stable Zone 测试。

覆盖:
- inject_kb_chunks: 注入 stable + [KB Context] 前缀 + 截断
- kb_chunk_count: 计数器统计(未压缩片段)
- reload_from_db: 恢复 stable 内部字段 + 计数一致
- ReactLoop 集成: search_knowledge 执行后 stable zone 含 KB 片段
"""
import asyncio
import os

import asyncpg

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
        "test-kb", "mock",
    )


def test_inject_kb_chunks_adds_to_stable():
    """inject_kb_chunks 注入 stable zone + [KB Context] 前缀 + 计数。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.inject_kb_chunks(conn, turn=1, content="贵州茅台 2025 年报营收 1500 亿")
            assert cm.kb_chunk_count() == 1
            stable_msg = cm.stable_zone.messages[-1]
            assert stable_msg["content"].startswith("[KB Context]")
            assert stable_msg["zone"] == "stable"
            # 入库校验
            row = await conn.fetchrow(
                "SELECT role, content, zone FROM messages "
                "WHERE session_id=$1 AND zone='stable'",
                session_id,
            )
            assert row is not None
            assert row["content"].startswith("[KB Context]")
            # get_messages 包含 KB 片段(供模型长期参考)
            msgs = cm.get_messages()
            assert any("[KB Context]" in (m.get("content") or "") for m in msgs)
        finally:
            await conn.close()

    asyncio.run(_run())


def test_inject_kb_chunks_truncates_long_content():
    """超长 KB 片段截断(防 stable zone 膨胀)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            long_content = "x" * 30000
            await cm.inject_kb_chunks(conn, turn=1, content=long_content)
            stored = cm.stable_zone.messages[-1]["content"]
            # 12000 截断 + 前缀
            assert len(stored) <= 12000 + len("[KB Context]\n")
        finally:
            await conn.close()

    asyncio.run(_run())


def test_reload_restores_stable_kb_count():
    """reload_from_db 恢复 stable 内部字段 + kb 计数一致。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.inject_kb_chunks(conn, turn=1, content="片段 A")
            await cm.inject_kb_chunks(conn, turn=2, content="片段 B")
            assert cm.kb_chunk_count() == 2
            # 重载(模拟续聊)
            cm2 = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm2.reload_from_db(conn)
            assert cm2.kb_chunk_count() == 2
            stable_msgs = cm2.stable_zone.messages
            assert all(m.get("msg_id") is not None for m in stable_msgs)
        finally:
            await conn.close()

    asyncio.run(_run())


class _MockAdapter:
    provider_name = "mock"

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
        r = self._responses[self._idx]
        self._idx += 1
        return r


def test_react_loop_injects_kb_to_stable():
    """ReactLoop 集成: search_knowledge 执行后 KB 片段注入 stable zone。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)

            from private_agent.tools.defs import ToolDef

            async def _kb_handler(args):
                from private_agent.tools.defs import ToolResult
                return ToolResult(output="[检索结果] 贵州茅台股价 1700 元")

            kb_tool = ToolDef(
                name="search_knowledge",
                description="知识库检索",
                parameters_schema={"type": "object", "properties": {}},
                handler=_kb_handler,
            )
            adapter = _MockAdapter(responses=[
                ChatResult(
                    content="", used_provider="mock",
                    tool_calls=[{
                        "id": "call_1", "type": "function",
                        "function": {"name": "search_knowledge", "arguments": "{}"},
                    }],
                ),
                ChatResult(content="查询完成", used_provider="mock"),
            ])
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[kb_tool],
                conn=conn,
                cfg={"context": {"status_bar": {"enabled": False}}},
            )
            await loop.run_turn("查一下茅台股价")
            assert cm.kb_chunk_count() >= 1
            # tool message 也存在(本轮模型消费)
            tool_msgs = [m for m in cm.active_zone.messages if m.get("role") == "tool"]
            assert len(tool_msgs) == 1
        finally:
            await conn.close()

    asyncio.run(_run())
