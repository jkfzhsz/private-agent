"""2026-08-13 修复E: monitor 跨会话查询工具测试。

验证 subagent_status / session_events 两个新工具能让全局智能体
跨会话查询子代理状态与事件流(问题3 第一环 —— 此前全局智能体
"调查子任务取消连事件都找不到")。
"""
import asyncio

import asyncpg
import pytest

from private_agent.tools.builtins.monitor_tools import (
    _session_events_handler,
    _subagent_status_handler,
)

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等迁移, 含 subagents/react_events 表)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def _patch_db_to_test(monkeypatch):
    """工具 handler 内 db.connect 指向测试库(默认连生产 private_agent)。"""
    from private_agent.storage import db

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


async def _seed():
    """插入: 一个父会话 + 一个子会话 + 一条 cancelled 子代理 + 一条 subagent 埋点。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        parent = await conn.fetchval(
            "INSERT INTO sessions (kind, title) VALUES ('main', 'test-parent') "
            "RETURNING id"
        )
        sub = await conn.fetchval(
            "INSERT INTO sessions (kind, title) VALUES ('sub', 'test-sub') "
            "RETURNING id"
        )
        await conn.execute(
            "INSERT INTO subagents (session_id, parent_turn, parent_task, prompt, "
            "status, tool_calls, error) "
            "VALUES ($1, 1, 'task1', 'p', 'cancelled', 5, 'cancelled')",
            sub,
        )
        await conn.execute(
            "INSERT INTO react_events (session_id, turn, event_type, payload) "
            "VALUES ($1, 1, 'subagent', '{\"kind\": \"cancelled\"}')",
            parent,
        )
        return parent
    finally:
        await conn.close()


def test_subagent_status_query(schema):
    """subagent_status 能跨会话查到 cancelled 子代理(含 tool_calls)。"""
    async def _run():
        await _seed()
        res = await _subagent_status_handler({"since_hours": 1})
        assert res.error in (None, ""), f"不应报错: {res.error}"
        assert "cancelled" in (res.output or "")
        assert "tool_calls=5" in (res.output or "")

    asyncio.run(_run())


def test_subagent_status_filter_by_status(schema):
    """subagent_status 支持按 status 过滤。"""
    async def _run():
        await _seed()
        res = await _subagent_status_handler({"since_hours": 1, "status": "succeeded"})
        assert "cancelled" not in (res.output or "") or "无子代理" in (res.output or "")

    asyncio.run(_run())


def test_session_events_query(schema):
    """session_events 能查到指定会话的 subagent 埋点事件。"""
    async def _run():
        parent = await _seed()
        res = await _session_events_handler(
            {"session_id": parent, "event_type": "subagent"}
        )
        assert res.error in (None, ""), f"不应报错: {res.error}"
        assert "subagent" in (res.output or "")

    asyncio.run(_run())


def test_session_events_missing_id(schema):
    """session_events 缺 session_id 应报错(不静默)。"""
    async def _run():
        res = await _session_events_handler({})
        assert "session_id required" in (res.error or "")

    asyncio.run(_run())
