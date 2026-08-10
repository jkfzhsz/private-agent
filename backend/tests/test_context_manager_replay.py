"""M4 m4-eval-runner-replay AC-1, AC-2 - ContextManager 扩展测试。

Source: spec/m4-eval-runner-replay AC-1, AC-2 + plan step 2, 3, 11
- reload_from_db: 完整重载三区消息(Frozen+Stable+Active),与 build_initial 后状态一致
- replace_frozen_zone: 替换 Frozen Zone 后 frozen_hash 重新计算,sessions 表 locked_skill_version 更新
"""
import asyncio
import os
from typing import Any

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL, ToolDef

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


async def _create_session(conn: "asyncpg.Connection", title: str = "test-ctx") -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        title,
        "mock-glm",
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC-1: reload_from_db
# ──────────────────────────────────────────────────────────────────────────────


def test_reload_from_db_rebuilds_three_zones():
    """reload_from_db 完整重载三区消息(Frozen+Stable+Active)(AC-1)。

    流程:
    1. ctx1 build_initial + append_user_message + append_assistant_message + append_tool_message
    2. ctx2(空内存)调 reload_from_db
    3. 验证 ctx2 三区消息与 ctx1 一致
    """
    _setup_schema()

    async def _run() -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict], list[dict]]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)

            # ctx1: 构建完整三区
            ctx1 = ContextManager(
                session_id=session_id,
                system_prompt="sys-1",
                tools=[ECHO_TOOL],
            )
            await ctx1.build_initial(conn)
            # 模拟 stable(注入记忆,内存+DB 同步)
            await conn.execute(
                "INSERT INTO messages (session_id, turn, role, content, zone) "
                "VALUES ($1, $2, $3, $4, $5)",
                session_id, 0, "user", "stable-memory", "stable",
            )
            ctx1.stable_zone.messages = [{"role": "user", "content": "stable-memory"}]
            await ctx1.append_user_message(conn, turn=1, content="hello")
            await ctx1.append_assistant_message(
                conn, turn=1, content="hi", tool_calls=[{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{\"text\":\"hi\"}"}}]
            )
            await ctx1.append_tool_message(
                conn, turn=1, tool_call_id="c1", content="hi", name="echo"
            )

            # ctx2: 空内存,调 reload_from_db
            ctx2 = ContextManager(
                session_id=session_id,
                system_prompt="sys-1",
                tools=[ECHO_TOOL],
            )
            await ctx2.reload_from_db(conn)

            return (
                ctx1.frozen_zone.messages, ctx2.frozen_zone.messages,
                ctx1.stable_zone.messages, ctx2.stable_zone.messages,
                ctx1.active_zone.messages, ctx2.active_zone.messages,
            )
        finally:
            await conn.close()

    (f1, f2, s1, s2, a1, a2) = asyncio.run(_run())
    # Frozen Zone 一致
    assert f2 == f1
    assert f2[0]["role"] == "system"
    # Stable Zone 语义一致(reload_from_db 恢复 role/content + 内部字段
    # turn/msg_id/zone, 供 §4.15 KB 计数与 §3.10.3 合并使用)
    assert len(s2) == len(s1)
    for before, after in zip(s1, s2):
        assert after["role"] == before["role"]
        assert after["content"] == before["content"]
        assert after["msg_id"] is not None
        assert after["zone"] == "stable"
    assert s2[0]["role"] == "user"
    assert s2[0]["content"] == "stable-memory"
    # Active Zone 一致(含 user/assistant/tool 三类)
    assert a2 == a1
    assert len(a2) == 3
    assert a2[0]["role"] == "user"
    assert a2[1]["role"] == "assistant"
    assert a2[2]["role"] == "tool"


def test_reload_from_db_empty_session_returns_empty_zones():
    """reload_from_db 对无消息会话返回空三区(不抛异常)。"""
    _setup_schema()

    async def _run() -> ContextManager:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            ctx = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await ctx.reload_from_db(conn)
            return ctx
        finally:
            await conn.close()

    ctx = asyncio.run(_run())
    assert ctx.frozen_zone.messages == []
    assert ctx.stable_zone.messages == []
    assert ctx.active_zone.messages == []


# ──────────────────────────────────────────────────────────────────────────────
# AC-2: replace_frozen_zone
# ──────────────────────────────────────────────────────────────────────────────


def test_replace_frozen_zone_recomputes_hash_and_updates_sessions():
    """replace_frozen_zone 替换 Frozen Zone 后 frozen_hash 重新计算,sessions 表更新(AC-2)。

    流程:
    1. ctx1 build_initial(system_prompt="sys-1", tools=[])
    2. 调 replace_frozen_zone(system_prompt="sys-2", tools=[ECHO_TOOL], skill_version="0.2.0")
    3. 验证 frozen_zone.messages 内容更新为新 system_prompt+tools
    4. 验证 frozen_hash 重新计算(与 sys-2 + ECHO_TOOL 一致)
    5. 验证 sessions.frozen_hash 与 ctx.compute_frozen_hash() 一致
    6. 验证 sessions.locked_skill_version 更新为 "0.2.0"
    7. 验证 messages 表 frozen zone 仅一条(删除旧的,插入新的)
    """
    _setup_schema()

    async def _run() -> tuple[ContextManager, str, str, str, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)

            ctx = ContextManager(
                session_id=session_id,
                system_prompt="sys-1",
                tools=[],
            )
            await ctx.build_initial(conn)
            old_hash = ctx.compute_frozen_hash()

            # 替换 Frozen Zone
            await ctx.replace_frozen_zone(
                conn,
                system_prompt="sys-2",
                tools=[ECHO_TOOL],
                skill_version="0.2.0",
            )
            new_hash = ctx.compute_frozen_hash()

            # 查 sessions 表
            row = await conn.fetchrow(
                "SELECT frozen_hash, locked_skill_version FROM sessions WHERE id=$1",
                session_id,
            )
            # 查 messages 表 frozen zone 行数
            frozen_count = await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return ctx, old_hash, new_hash, row["frozen_hash"], row["locked_skill_version"], frozen_count
        finally:
            await conn.close()

    ctx, old_hash, new_hash, db_hash, db_version, frozen_count = asyncio.run(_run())
    # frozen_hash 重新计算(sys-2 + ECHO_TOOL ≠ sys-1)
    assert new_hash != old_hash
    # 内存 frozen_zone 更新
    assert ctx.frozen_zone.messages[0]["role"] == "system"
    assert "sys-2" in ctx.frozen_zone.messages[0]["content"]
    assert "echo" in ctx.frozen_zone.messages[0]["content"]
    # DB sessions.frozen_hash 与内存一致
    assert db_hash == new_hash
    # DB sessions.locked_skill_version 更新
    assert db_version == "0.2.0"
    # messages 表 frozen zone 仅一条(删旧建新)
    assert frozen_count == 1


def test_replace_frozen_zone_without_skill_version_only_updates_hash():
    """replace_frozen_zone 不传 skill_version 时,仅更新 frozen_hash,不动 locked_skill_version(AC-2)。"""
    _setup_schema()

    async def _run() -> tuple[str | None, str | None]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            # 预设 locked_skill_version="0.1.0"
            await conn.execute(
                "INSERT INTO sessions (id, title, model_id, locked_skill_version) "
                "VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO UPDATE SET locked_skill_version=$4",
                session_id, "test", "mock-glm", "0.1.0",
            )
            ctx = ContextManager(
                session_id=session_id,
                system_prompt="sys-1",
                tools=[],
            )
            await ctx.build_initial(conn)

            # 替换 Frozen Zone(不传 skill_version)
            await ctx.replace_frozen_zone(
                conn,
                system_prompt="sys-2",
                tools=[],
            )
            row = await conn.fetchrow(
                "SELECT frozen_hash, locked_skill_version FROM sessions WHERE id=$1",
                session_id,
            )
            return row["frozen_hash"], row["locked_skill_version"]
        finally:
            await conn.close()

    db_hash, db_version = asyncio.run(_run())
    # frozen_hash 已更新(非空)
    assert db_hash is not None
    assert len(db_hash) == 64  # SHA-256 hex
    # locked_skill_version 保持原值(未被覆盖)
    assert db_version == "0.1.0"
