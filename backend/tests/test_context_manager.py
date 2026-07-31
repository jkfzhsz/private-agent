"""M1 Phase 3 - ContextManager 三区构建与持久化。

Source: spec/m1-react-loop AC-3 + Solution `core/context_manager.py`
- 蓝图 §3.1-3.2: Frozen/Stable/Active 三区分区
- 蓝图 §3.3: 启动构建 + 每轮构建
- spec AC-3: 会话启动后 messages 表有 Frozen Zone(system_prompt+工具定义)+ Stable Zone(空)+ Active Zone(空);每轮结束 Active 追加用户/助手消息
- spec Out of scope: 三区构建不含压缩(启动构建: Frozen=system_prompt+工具定义, Stable=空, Active=空)
- spec Assumptions: hash 字段预留(M1-b step 10 实现校验),本次只存字段不做校验
"""
import asyncio
import os

import asyncpg

from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL, DATETIME_TOOL
from private_agent.core.context_manager import ContextManager, Zone

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


async def _create_session(conn: asyncpg.Connection, system_prompt: str = "You are a helpful assistant.") -> int:
    """插入 sessions 记录,返回 id。"""
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-session",
        "mock-glm",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Zone dataclass
# ──────────────────────────────────────────────────────────────────────────────


def test_zone_dataclass_has_required_fields():
    """Zone 含 name/messages/hash 字段(hash 预留)。"""
    z = Zone(name="frozen")
    assert z.name == "frozen"
    assert z.messages == []
    assert z.hash is None  # M1-b 预留


def test_zone_messages_mutable():
    """Zone.messages 可追加(每轮构建会用到)。"""
    z = Zone(name="active")
    z.messages.append({"role": "user", "content": "hi"})
    assert len(z.messages) == 1


# ──────────────────────────────────────────────────────────────────────────────
# ContextManager.build_initial - 启动构建(蓝图 §3.3)
# ──────────────────────────────────────────────────────────────────────────────


def test_build_initial_persists_frozen_zone_system_message():
    """build_initial 将 system_prompt + 工具定义作为 role='system' zone='frozen' 入库。

    Source: spec AC-3 "会话启动后 messages 表有 Frozen Zone(system_prompt+工具定义)"
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="You are a helpful assistant.",
                tools=[ECHO_TOOL, DATETIME_TOOL],
            )
            await cm.build_initial(conn)
            rows = await conn.fetch(
                "SELECT role, zone, content FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["role"] == "system"
    assert rows[0]["zone"] == "frozen"
    # content 应包含 system_prompt 原文
    assert "You are a helpful assistant." in rows[0]["content"]


def test_build_initial_frozen_content_includes_tool_definitions():
    """Frozen Zone 的 system 消息应包含工具定义描述。

    Source: spec AC-3 "Frozen Zone(system_prompt+工具定义)"
    """
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="base prompt",
                tools=[ECHO_TOOL],
            )
            await cm.build_initial(conn)
            content = await conn.fetchval(
                "SELECT content FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return content
        finally:
            await conn.close()

    content = asyncio.run(_run())
    # content 应含 base prompt + echo 工具描述
    assert "base prompt" in content
    assert "echo" in content


def test_build_initial_updates_in_memory_frozen_zone():
    """build_initial 后 frozen_zone.messages 内存同步更新。"""
    _setup_schema()

    async def _run() -> ContextManager:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="hello",
                tools=[ECHO_TOOL],
            )
            await cm.build_initial(conn)
            return cm
        finally:
            await conn.close()

    cm = asyncio.run(_run())
    assert len(cm.frozen_zone.messages) == 1
    assert cm.frozen_zone.messages[0]["role"] == "system"
    assert "hello" in cm.frozen_zone.messages[0]["content"]


def test_build_initial_stable_and_active_zones_are_empty():
    """启动构建后 Stable/Active Zone 在内存中为空(spec Out of scope: 启动时为空)。

    Source: spec Out of scope "启动构建(Frozen=system_prompt+工具定义,Stable=空,Active=空)"
    """
    _setup_schema()

    async def _run() -> ContextManager:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="hello",
                tools=[],
            )
            await cm.build_initial(conn)
            return cm
        finally:
            await conn.close()

    cm = asyncio.run(_run())
    assert cm.stable_zone.messages == []
    assert cm.active_zone.messages == []


def test_build_initial_no_tools_still_persists_system_message():
    """无工具时 build_initial 仍持久化 system_prompt 到 Frozen Zone。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="system only",
                tools=[],
            )
            await cm.build_initial(conn)
            rows = await conn.fetch(
                "SELECT role, zone, content FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["content"] == "system only"


def test_build_initial_records_turn_zero():
    """Frozen Zone 消息 turn=0(系统初始化,非用户轮次)。"""
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await cm.build_initial(conn)
            turn = await conn.fetchval(
                "SELECT turn FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return turn
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 0



# ──────────────────────────────────────────────────────────────────────────────
# Behavior 3: build_per_turn - Active Zone 追加用户/助手消息(蓝图 §3.3 每轮构建)
# Source: spec AC-3 "每轮结束 Active Zone 追加用户/助手消息"
# ──────────────────────────────────────────────────────────────────────────────


def _build_cm_with_initial(conn: asyncpg.Connection, system_prompt: str = "sys") -> ContextManager:
    """helper:建 session + build_initial,返回 ContextManager(异步内调用)。"""
    raise NotImplementedError  # 仅占位,测试内联实现


def test_append_user_message_persists_to_active_zone():
    """append_user_message 将用户消息持久化到 messages 表(zone='active')。

    Source: spec AC-3 "Active Zone 追加用户消息"
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[ECHO_TOOL],
            )
            await cm.build_initial(conn)
            await cm.append_user_message(conn, turn=1, content="hello")
            rows = await conn.fetch(
                "SELECT role, zone, turn, content FROM messages WHERE session_id=$1 AND zone='active'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["role"] == "user"
    assert rows[0]["zone"] == "active"
    assert rows[0]["turn"] == 1
    assert rows[0]["content"] == "hello"


def test_append_user_message_updates_in_memory_active_zone():
    """append_user_message 后 active_zone.messages 内存同步追加。"""
    _setup_schema()

    async def _run() -> ContextManager:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await cm.build_initial(conn)
            await cm.append_user_message(conn, turn=1, content="hi")
            return cm
        finally:
            await conn.close()

    cm = asyncio.run(_run())
    assert len(cm.active_zone.messages) == 1
    assert cm.active_zone.messages[0] == {"role": "user", "content": "hi"}


def test_append_assistant_message_persists_with_content():
    """append_assistant_message 持久化助手消息(role='assistant', zone='active')。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await cm.build_initial(conn)
            await cm.append_assistant_message(conn, turn=1, content="hello back")
            rows = await conn.fetch(
                "SELECT role, zone, turn, content, tool_calls FROM messages WHERE session_id=$1 AND role='assistant'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    assert rows[0]["zone"] == "active"
    assert rows[0]["turn"] == 1
    assert rows[0]["content"] == "hello back"
    assert rows[0]["tool_calls"] is None


def test_append_assistant_message_with_tool_calls_persists_jsonb():
    """助手消息含 tool_calls 时持久化为 JSONB。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[ECHO_TOOL],
            )
            await cm.build_initial(conn)
            tool_calls = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text":"hi"}'},
                }
            ]
            await cm.append_assistant_message(
                conn, turn=1, content="", tool_calls=tool_calls
            )
            rows = await conn.fetch(
                "SELECT tool_calls FROM messages WHERE session_id=$1 AND role='assistant'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert rows[0]["tool_calls"] is not None
    # asyncpg 默认返回 JSONB 为 str,需手动 json.loads(ws_offset.py:58 同样处理)
    import json as _json
    tc = rows[0]["tool_calls"]
    if isinstance(tc, str):
        tc = _json.loads(tc)
    assert tc[0]["function"]["name"] == "echo"


def test_append_tool_message_persists_with_tool_call_id():
    """append_tool_message 持久化工具结果消息(role='tool', zone='active')。

    Source: spec AC-3 Active Zone 追加工具结果
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[ECHO_TOOL],
            )
            await cm.build_initial(conn)
            await cm.append_tool_message(
                conn,
                turn=1,
                tool_call_id="call_1",
                content="echo result",
                name="echo",
            )
            rows = await conn.fetch(
                "SELECT role, zone, tool_call_id, content, name FROM messages WHERE session_id=$1 AND role='tool'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["role"] == "tool"
    assert rows[0]["zone"] == "active"
    assert rows[0]["tool_call_id"] == "call_1"
    assert rows[0]["content"] == "echo result"
    assert rows[0]["name"] == "echo"


def test_get_messages_returns_frozen_plus_active():
    """get_messages 返回 Frozen + Stable + Active 合并后的消息列表。

    Source: spec Solution "get_messages"
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys prompt",
                tools=[],
            )
            await cm.build_initial(conn)
            await cm.append_user_message(conn, turn=1, content="u1")
            await cm.append_assistant_message(conn, turn=1, content="a1")
            return cm.get_messages()
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    # 1 frozen(system) + 0 stable + 2 active(user + assistant)
    assert len(msgs) == 3
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "u1"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "a1"


def test_get_messages_empty_active_returns_only_frozen():
    """Active 为空时 get_messages 仅返回 Frozen。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await cm.build_initial(conn)
            return cm.get_messages()
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    assert len(msgs) == 1
    assert msgs[0]["role"] == "system"


def test_build_per_turn_returns_merged_messages():
    """build_per_turn(conn, turn, user_content) 一次性完成:
    - 追加用户消息
    - 返回 Frozen+Stable+Active 合并消息列表(供 adapter.chat 使用)

    Source: spec Solution "build_per_turn"
    """
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await cm.build_initial(conn)
            return await cm.build_per_turn(conn, turn=1, user_content="hello")
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    # 1 frozen(system) + 1 active(user)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hello"


def test_build_per_turn_persists_user_message_and_updates_memory():
    """build_per_turn 副作用:持久化用户消息 + 内存 active_zone 更新。"""
    _setup_schema()

    async def _run() -> tuple[list[dict], ContextManager]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
            )
            await cm.build_initial(conn)
            msgs = await cm.build_per_turn(conn, turn=2, user_content="turn2")
            rows = await conn.fetch(
                "SELECT role, zone, turn, content FROM messages WHERE session_id=$1 AND zone='active'",
                session_id,
            )
            return [dict(r) for r in rows], cm
        finally:
            await conn.close()

    rows, cm = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["turn"] == 2
    assert rows[0]["content"] == "turn2"
    assert len(cm.active_zone.messages) == 1


# ──────────────────────────────────────────────────────────────────────────────
# ensure_initial - 幂等启动构建(P1 修复:避免重复 INSERT Frozen Zone)
# Source: spec AC-3 "会话启动后 messages 表有 Frozen Zone"(单条语义)
# ──────────────────────────────────────────────────────────────────────────────


def test_ensure_initial_first_call_persists_frozen_zone():
    """首次调用 ensure_initial 等同于 build_initial:持久化 Frozen Zone。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[ECHO_TOOL],
            )
            await cm.ensure_initial(conn)
            rows = await conn.fetch(
                "SELECT role, zone FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    assert rows[0]["role"] == "system"
    assert rows[0]["zone"] == "frozen"


def test_ensure_initial_second_call_does_not_duplicate_frozen_zone():
    """第二次调用 ensure_initial 不重复 INSERT Frozen Zone(P1 修复核心)。

    Source: spec AC-3 Frozen Zone 单条语义;P1 finding: main.py:163 每次都 build_initial。
    """
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[ECHO_TOOL],
            )
            await cm.ensure_initial(conn)
            # 模拟 main.py 第二次 user_message:新 ContextManager 实例,同一 session_id
            cm2 = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[ECHO_TOOL],
            )
            await cm2.ensure_initial(conn)
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE session_id=$1 AND zone='frozen'",
                session_id,
            )
            return count
        finally:
            await conn.close()

    count = asyncio.run(_run())
    assert count == 1, f"expected 1 frozen row, got {count}"


def test_ensure_initial_second_call_reloads_frozen_to_memory():
    """第二次调用 ensure_initial 从 DB reload Frozen Zone 到内存。

    保证 adapter.chat(messages) 能拿到 system prompt,而非空 frozen_zone。
    """
    _setup_schema()

    async def _run() -> ContextManager:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="original sys",
                tools=[ECHO_TOOL],
            )
            await cm.ensure_initial(conn)
            # 新实例:模拟 main.py 第二次 user_message
            cm2 = ContextManager(
                session_id=session_id,
                system_prompt="DIFFERENT",  # 故意不同,验证 reload 而非重新构造
                tools=[ECHO_TOOL],
            )
            await cm2.ensure_initial(conn)
            return cm2
        finally:
            await conn.close()

    cm2 = asyncio.run(_run())
    # frozen_zone.messages 应从 DB reload,内容是 "original sys" 而非 "DIFFERENT"
    assert len(cm2.frozen_zone.messages) == 1
    assert "original sys" in cm2.frozen_zone.messages[0]["content"]
    assert "DIFFERENT" not in cm2.frozen_zone.messages[0]["content"]
    # Stable/Active 重置为空
    assert cm2.stable_zone.messages == []
    assert cm2.active_zone.messages == []
