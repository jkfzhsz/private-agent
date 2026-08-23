"""0.5.1 A-1(2026-08-15) - context_injected 上下文注入审计。

DSH "model-visible means logged": 凡进入模型视野的注入(Stable Zone
记忆/经验/KB、hook additionalContext、状态栏)落 react_events, 供排查
"模型到底看到了什么"取证。仅落库不推 WS。

覆盖:
- context_injected 事件类型注册(insert 成功)
- ContextManager._emit_context_injected 落库 payload 完整(source/bytes/preview/msg_id)
- derive 一致性: 注入消息可经 messages 表 + react_events 联合重构
"""
import asyncio
import os

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.storage import migrations, react_events

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_and_create_session() -> int:
    """建表 + 创建 session,返回 session_id。"""
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, status) VALUES ($1, $2) RETURNING id",
                "test session",
                "active",
            )
            return session_id
        finally:
            await conn.close()

    return asyncio.run(_run())


def test_insert_context_injected_event_type_registered():
    """context_injected 事件类型通过 CHECK 约束注册(insert 成功)。"""
    session_id = _setup_and_create_session()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await react_events.insert_react_event(
                conn,
                session_id=session_id,
                turn=0,
                event_type="context_injected",
                payload={"source": "stable_memory", "bytes": 10, "preview": "..."},
            )
        finally:
            await conn.close()

    assert asyncio.run(_run()) > 0


def test_emit_context_injected_persists_full_payload():
    """_emit_context_injected 落库, payload 含 source/bytes/preview/msg_id。"""
    session_id = _setup_and_create_session()
    content = "[KB Context]\n测试知识片段(超 200 字时 preview 截断省略号)"

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            # 先插一条 stable 注入消息(取 msg_id 回查锚点)
            msg_id = await conn.fetchval(
                """
                INSERT INTO messages (session_id, turn, role, content, zone)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                session_id, 0, "user", "[KB Context]\n测试知识片段", "stable",
            )
            await cm._emit_context_injected(
                conn, source="stable_kb", content=content,
                turn=0, msg_id=msg_id,
            )
            row = await conn.fetchrow(
                "SELECT event_type, payload, turn FROM react_events "
                "WHERE session_id=$1 AND event_type='context_injected' "
                "ORDER BY id DESC LIMIT 1",
                session_id,
            )
            return {
                "event_type": row["event_type"],
                "payload": row["payload"],
                "turn": row["turn"],
            }
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result["event_type"] == "context_injected"
    assert result["turn"] == 0
    p = result["payload"]
    # asyncpg JSONB 可能返回 str, 统一解析
    import json
    if isinstance(p, str):
        p = json.loads(p)
    assert p["source"] == "stable_kb"
    assert p["bytes"] == len(content.encode("utf-8"))
    assert p["preview"] == content[:200]
    assert p["msg_id"] > 0


def test_derive_injected_content_from_events_and_messages():
    """derive 一致性: 注入内容可经 messages 表 + context_injected 事件重构。

    DSH 不变量: model-visible means logged —— 模型视野中的注入,
    必须能从事件流 + 消息表还原(排查"模型到底看到了什么"取证)。
    """
    session_id = _setup_and_create_session()

    async def _run() -> bool:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            injected = "[KB Context]\n公司信贷审批要点: 抵押率、行业限额"
            msg_id = await conn.fetchval(
                """
                INSERT INTO messages (session_id, turn, role, content, zone)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                session_id, 3, "user", injected, "stable",
            )
            await cm._emit_context_injected(
                conn, source="stable_kb", content=injected,
                turn=3, msg_id=msg_id,
            )
            # 重构: 事件 preview + messages 全文
            row = await conn.fetchrow(
                "SELECT payload FROM react_events "
                "WHERE session_id=$1 AND event_type='context_injected'",
                session_id,
            )
            import json
            p = row["payload"]
            if isinstance(p, str):
                p = json.loads(p)
            full = await conn.fetchval(
                "SELECT content FROM messages WHERE id=$1", p["msg_id"]
            )
            # 事件 preview 是全文前缀(200 字截断)
            return str(full).startswith(str(p["preview"]))
        finally:
            await conn.close()

    assert asyncio.run(_run()) is True


def test_emit_context_injected_failure_does_not_break_injection():
    """审计失败静默(不抛出) —— 审计是增强不是门禁。"""
    session_id = _setup_and_create_session()

    async def _run() -> bool:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            # 用不存在的 session_id 触发外键失败 → 审计应静默吞掉
            cm.session_id = 999999
            await cm._emit_context_injected(
                conn, source="stable_memory", content="x", turn=0,
            )
            return True
        finally:
            await conn.close()

    assert asyncio.run(_run()) is True
