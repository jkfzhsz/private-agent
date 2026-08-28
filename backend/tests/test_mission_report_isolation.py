"""0.6.0 F1-8: mission_report 消息隔离 —— 代码级验证不进主对话上下文。

验收(设计文档 B 阶段③/V1): mission_report 消息不出现在无涯后续 turn 的
上下文里(reload_from_db → get_messages / get_messages_with_meta 均无);
DB 行保留可回看(§八裁决 5); chat 消息零回归。

真实 DB 测试(PA_TEST_DSN 默认 private_agent_test; F1 批跑批时指向
private_agent_f1test 避免与全量基线互踩)。
"""
import asyncio
import os

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.storage import migrations
from private_agent.tools.builtins.mission_tools import append_mission_report

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


async def _create_session(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title) VALUES ('f1-8-isolation') RETURNING id"
    )


def _make_cm(session_id: int) -> ContextManager:
    return ContextManager(
        session_id=session_id,
        system_prompt="You are a helpful assistant.",
        tools=[],
    )


def test_mission_report_isolated_from_context():
    """mission_report 写入 → reload 后不出现在任何上下文视图(隔离核心验收)。"""
    _setup_schema()

    async def _run() -> tuple[list, list, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = _make_cm(session_id)
            await cm.build_initial(conn)
            # 普通对话消息 + mission_report 各一条
            await conn.execute(
                "INSERT INTO messages (session_id, turn, role, content, zone) "
                "VALUES ($1, 1, 'user', '普通对话消息', 'active')",
                session_id,
            )
            report_id = await append_mission_report(
                conn, session_id, 42, "里程碑 m1 完成"
            )
            await cm.reload_from_db(conn)
            visible = cm.get_messages()
            with_meta = cm.get_messages_with_meta()
            return visible, with_meta, report_id
        finally:
            await conn.close()

    visible, with_meta, report_id = asyncio.run(_run())
    contents_visible = [m.get("content", "") for m in visible]
    contents_meta = [m.get("content", "") for m in with_meta]
    # 隔离: mission_report 不在任何上下文视图(V1 验收前置)
    assert not any("里程碑 m1 完成" in c for c in contents_visible), contents_visible
    assert not any("里程碑 m1 完成" in c for c in contents_meta), contents_meta
    # 零回归: 普通消息与 frozen system 照常加载
    assert any("普通对话消息" in c for c in contents_visible)
    assert any("helpful assistant" in c for c in contents_visible)


def test_mission_report_row_persisted_for_review():
    """DB 行保留(msg_kind='mission_report')—— 断线重连/回看可查(§八裁决 5)。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            await append_mission_report(conn, session_id, 7, "escalated 等待裁决")
            row = await conn.fetchrow(
                "SELECT role, content, msg_kind, zone FROM messages "
                "WHERE session_id=$1 AND msg_kind='mission_report'",
                session_id,
            )
            return dict(row) if row else {}
        finally:
            await conn.close()

    row = asyncio.run(_run())
    assert row, "mission_report 行必须落库可回看"
    assert row["msg_kind"] == "mission_report"
    assert row["role"] == "assistant"
    assert row["zone"] == "active"
    assert "Mission #7" in row["content"]


def test_msg_kind_default_chat():
    """存量/普通写入路径 msg_kind 默认 'chat' —— 加载过滤零误伤。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            await conn.execute(
                "INSERT INTO messages (session_id, turn, role, content, zone) "
                "VALUES ($1, 1, 'user', 'hello', 'active')",
                session_id,
            )
            return await conn.fetchval(
                "SELECT msg_kind FROM messages WHERE session_id=$1", session_id
            )
        finally:
            await conn.close()

    assert asyncio.run(_run()) == "chat"
