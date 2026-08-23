"""V2 上下文工程 - reasoning_content 回传链路测试(AI-Agents-in-Depth §2.3.1)。

DeepSeek V4 系强制要求 assistant 消息(含 tool_calls 的)原样回传
reasoning_content。覆盖:
- append_assistant_message 持久化 reasoning_content(含 tool_calls 分支)
- reload_from_db 恢复 reasoning_content
- get_messages 剥离内部字段(zone/turn/msg_id 不进 API)但保留 reasoning_content
- 空 reasoning_content 不写入消息 dict(旧消息兼容)
"""
import asyncio
import json
import os

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema():
    async def _run():
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
        "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
    )


def test_append_assistant_persists_reasoning_content():
    """append_assistant_message 持久化 reasoning_content(无 tool_calls)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.append_assistant_message(
                conn, turn=1, content="final answer",
                reasoning_content="思考过程...",
            )
            row = await conn.fetchrow(
                "SELECT role, content, reasoning_content, zone FROM messages "
                "WHERE session_id=$1 AND role='assistant'",
                session_id,
            )
            assert row is not None
            assert row["content"] == "final answer"
            assert row["reasoning_content"] == "思考过程..."
            assert row["zone"] == "active"
            # 内存同步: 消息 dict 含 reasoning_content(供 API 透传)
            mem = cm.active_zone.messages[-1]
            assert mem["reasoning_content"] == "思考过程..."
        finally:
            await conn.close()

    asyncio.run(_run())


def test_append_assistant_with_tool_calls_persists_reasoning():
    """含 tool_calls 的 assistant 消息同样持久化 reasoning_content。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            tool_calls = [
                {"id": "call_1", "type": "function",
                 "function": {"name": "echo", "arguments": "{}"}}
            ]
            await cm.append_assistant_message(
                conn, turn=1, content="", tool_calls=tool_calls,
                reasoning_content="为什么调用 echo",
            )
            row = await conn.fetchrow(
                "SELECT content, reasoning_content, tool_calls FROM messages "
                "WHERE session_id=$1 AND role='assistant'",
                session_id,
            )
            assert row["reasoning_content"] == "为什么调用 echo"
            tc = json.loads(row["tool_calls"])
            assert tc[0]["function"]["name"] == "echo"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_reload_from_db_restores_reasoning_content():
    """reload_from_db 恢复 reasoning_content(续聊后模型可见历史思考)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.append_user_message(conn, turn=1, content="u1")
            await cm.append_assistant_message(
                conn, turn=1, content="a1", reasoning_content="r1"
            )
            await cm.reload_from_db(conn)
            active = cm.active_zone.messages
            assert len(active) == 2
            asst = active[1]
            assert asst["reasoning_content"] == "r1"
            # 内部字段恢复: turn/msg_id(压缩滑动窗口依赖)
            assert asst["turn"] == 1
            assert asst["msg_id"] is not None
        finally:
            await conn.close()

    asyncio.run(_run())


def test_get_messages_strips_meta_keeps_reasoning():
    """get_messages 剥离内部 metadata 但保留 OpenAI 兼容字段(含 reasoning_content)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.append_user_message(conn, turn=1, content="u1")
            await cm.append_assistant_message(
                conn, turn=1, content="a1", reasoning_content="r1"
            )
            msgs = cm.get_messages()
            asst = msgs[-1]
            assert asst["reasoning_content"] == "r1"
            # 内部字段不进 API(蓝图 §3.2 硬约束)
            assert "turn" not in asst
            assert "msg_id" not in asst
            assert "zone" not in asst
            assert "compressed" not in asst
            # OpenAI 字段保留
            assert asst["role"] == "assistant"
            assert asst["content"] == "a1"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_get_messages_with_meta_keeps_internal_fields():
    """get_messages_with_meta 保留内部字段(供压缩/归档使用, 不进 API)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            await cm.append_user_message(conn, turn=1, content="u1")
            meta_msgs = cm.get_messages_with_meta()
            user_msg = meta_msgs[-1]
            assert user_msg["turn"] == 1
            assert user_msg["msg_id"] is not None
        finally:
            await conn.close()

    asyncio.run(_run())
