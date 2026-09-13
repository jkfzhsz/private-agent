"""会议室角色委派 —— W1 角色透传测试(设计文档 §4.4 / §9)。

覆盖:
1. 委派协议 schema 暴露 role(枚举) —— 模型可见性;
2. 准入: role 仅会议室会话可用, 非房间/非法角色/无解析器一律拒绝;
3. **失败零副作用**: 角色工具解析失败时不留 pending 行、不占类型配额;
4. 装配正确性: role → SubagentRunner(role_skill=..., tools=角色工具集);
   缺省 role → 继承父会话工具与角色(零回归);
5. 子会话落库: locked_skill_name 切到角色、locked_skill_version 置 None、
   workspace 继承父会话(= 房间共享目录, 产物交接的机制基础)。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

import private_agent.tools.builtins.delegate_subtask as ds
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


# ────────────────────────────────────────────────────────────────────────────
# 测试替身
# ────────────────────────────────────────────────────────────────────────────


class _FakeConn:
    """最小 asyncpg 连接替身: 按 SQL 文本分派, 记录副作用(建行/执行)。"""

    def __init__(self, *, kind: str = "room", parent_turn: int = 7) -> None:
        self.kind = kind
        self.parent_turn = parent_turn
        self.inserted_subagents: list[tuple] = []
        self._next_id = 100

    async def fetchval(self, sql, *args):
        s = " ".join(sql.split())
        if "SELECT kind FROM sessions" in s:
            return self.kind
        if "MAX(turn)" in s and "FROM messages" in s:
            return self.parent_turn
        if "SELECT model_id FROM sessions" in s:
            return "mock-model"
        if "INSERT INTO subagents" in s:
            self.inserted_subagents.append(args)
            self._next_id += 1
            return self._next_id
        raise AssertionError(f"unexpected fetchval: {s}")

    async def execute(self, sql, *args):
        return "UPDATE 1"

    async def fetch(self, sql, *args):
        s = " ".join(sql.split())
        if "FROM subagents WHERE id = ANY" in s:
            ids = list(args[0]) if args else []
            return [
                {
                    "id": i,
                    "parent_task": f"t{i}",
                    "status": "succeeded",
                    "result": f"r{i}",
                    "error": None,
                    "tool_calls": 1,
                }
                for i in ids
            ]
        raise AssertionError(f"unexpected fetch: {s}")


@pytest.fixture
def stub_runner(monkeypatch):
    """桩化 SubagentRunner / watchdog / 类型配额 —— 只留角色装配逻辑。"""
    captured: list[dict] = []

    class _StubRunner:
        def __init__(self, **kw):
            captured.append(kw)
            self._sid = kw.get("subagent_id")

        async def run(self):
            return self._sid

    class _StubRegistry:
        async def acquire(self, *a, **k):
            return True

        async def release(self, *a, **k):
            return None

    async def _noop_watchdog(**kw):
        return None

    async def _noop_push(*a, **k):
        return None

    monkeypatch.setattr(ds, "SubagentRunner", _StubRunner)
    monkeypatch.setattr(ds, "subagent_type_registry", _StubRegistry())
    monkeypatch.setattr(ds, "_watchdog_wait", _noop_watchdog)
    monkeypatch.setattr(ds, "_safe_push", _noop_push)
    return captured


def _run_handler(
    conn,
    *,
    subtasks: list[dict],
    resolver=None,
    tools=None,
):
    async def _sink(ev):
        return None

    return asyncio.run(
        ds._delegate_handler(
            conn=conn,
            cfg={},
            session_id=1,
            event_sink=_sink,
            tools=list(tools or []),
            args={"subtasks": subtasks},
            sc=ds.subagent_cfg({}),
            system_prompt_factory=None,
            adapter_factory=None,
            compress_adapter=None,
            role_tools_resolver=resolver,
        )
    )


# ────────────────────────────────────────────────────────────────────────────
# 1. schema 可见性
# ────────────────────────────────────────────────────────────────────────────


class TestSchemaExposesRole:
    def test_delegate_schema_has_role_enum(self):
        item = ds.DELEGATE_SCHEMA["properties"]["subtasks"]["items"]
        role = item["properties"]["role"]
        assert role["type"] == "string"
        assert set(role["enum"]) == {
            "office",
            "data_analysis",
            "frontend_design",
        }

    def test_role_is_optional(self):
        """缺省必须可解析(否则既有调用全部失效 → 回归)。"""
        item = ds.DELEGATE_SCHEMA["properties"]["subtasks"]["items"]
        assert "role" not in item["required"]
        assert item["required"] == ["id", "prompt"]


# ────────────────────────────────────────────────────────────────────────────
# 2. 准入校验
# ────────────────────────────────────────────────────────────────────────────


class TestRoleAdmission:
    def test_rejected_outside_room(self, stub_runner):
        conn = _FakeConn(kind="main")
        res = _run_handler(
            conn,
            subtasks=[
                {"id": "a", "prompt": "转 PPT", "role": "frontend_design"}
            ],
            resolver=lambda r: None,
        )
        assert res.error and "仅会议室会话可用" in res.error
        assert conn.inserted_subagents == []
        assert stub_runner == []

    def test_rejected_for_subagent_session(self, stub_runner):
        """kind='sub' 走嵌套校验分支(先于角色校验)。"""
        conn = _FakeConn(kind="sub")
        res = _run_handler(
            conn,
            subtasks=[{"id": "a", "prompt": "p", "role": "office"}],
            resolver=lambda r: None,
        )
        assert res.error and "嵌套委派" in res.error

    def test_rejected_for_illegal_role(self, stub_runner):
        conn = _FakeConn(kind="room")
        res = _run_handler(
            conn,
            subtasks=[{"id": "a", "prompt": "p", "role": "monitor"}],
            resolver=lambda r: None,
        )
        assert res.error and "非法" in res.error
        assert conn.inserted_subagents == []

    def test_rejected_when_resolver_missing(self, stub_runner):
        """未注入解析器时不得放行(否则会以父会话工具冒充他角色)。"""
        conn = _FakeConn(kind="room")
        res = _run_handler(
            conn,
            subtasks=[{"id": "a", "prompt": "p", "role": "office"}],
            resolver=None,
        )
        assert res.error and "未装配角色工具解析器" in res.error
        assert conn.inserted_subagents == []

    def test_rejected_for_non_string_role(self, stub_runner):
        conn = _FakeConn(kind="room")
        res = _run_handler(
            conn,
            subtasks=[{"id": "a", "prompt": "p", "role": 123}],
        )
        assert res.error and "必须为字符串" in res.error


# ────────────────────────────────────────────────────────────────────────────
# 3. 失败零副作用
# ────────────────────────────────────────────────────────────────────────────


class TestResolverFailureHasNoSideEffects:
    def test_no_pending_rows_and_no_quota_consumed(self, stub_runner, monkeypatch):
        conn = _FakeConn(kind="room")
        acquired: list[str] = []

        class _Registry:
            async def acquire(self, typ, **kw):
                acquired.append(typ)
                return True

            async def release(self, *a, **k):
                return None

        monkeypatch.setattr(ds, "subagent_type_registry", _Registry())

        async def _boom(role):
            raise RuntimeError("skill 'frontend_design' 不存在")

        res = _run_handler(
            conn,
            subtasks=[
                {"id": "a", "prompt": "转 PPT", "role": "frontend_design"}
            ],
            resolver=_boom,
        )
        assert res.error and "工具装配失败" in res.error
        # 关键: 解析在副作用之前 → 未建行、未占配额
        assert conn.inserted_subagents == []
        assert acquired == []


# ────────────────────────────────────────────────────────────────────────────
# 4. 装配正确性
# ────────────────────────────────────────────────────────────────────────────


class TestRoleAssembly:
    def test_role_and_role_tools_passed_to_runner(self, stub_runner):
        conn = _FakeConn(kind="room")
        sentinel = [object()]
        seen_roles: list[str] = []

        async def _resolver(role):
            seen_roles.append(role)
            return sentinel

        res = _run_handler(
            conn,
            subtasks=[
                {"id": "ppt", "prompt": "转 PPT", "role": "frontend_design"}
            ],
            resolver=_resolver,
        )
        assert res.error is None
        assert seen_roles == ["frontend_design"]
        assert len(stub_runner) == 1
        assert stub_runner[0]["role_skill"] == "frontend_design"
        assert stub_runner[0]["tools"] is sentinel

    def test_default_role_inherits_parent_tools(self, stub_runner):
        """零回归: 不传 role → 父会话工具 + role_skill=None。"""
        conn = _FakeConn(kind="room")
        parent_tools = ["T1", "T2"]
        res = _run_handler(
            conn,
            subtasks=[{"id": "a", "prompt": "普通子任务"}],
            tools=parent_tools,
        )
        assert res.error is None
        assert stub_runner[0]["role_skill"] is None
        assert stub_runner[0]["tools"] == parent_tools

    def test_mixed_roles_aligned_per_subtask(self, stub_runner):
        """混合场景: 有 role 的用角色工具, 无 role 的用父会话工具。"""
        conn = _FakeConn(kind="room")
        parent_tools = ["P"]
        role_tools = ["R"]

        async def _resolver(role):
            return role_tools

        res = _run_handler(
            conn,
            subtasks=[
                {"id": "a", "prompt": "分析", "type": "analysis"},
                {
                    "id": "b",
                    "prompt": "转 PPT",
                    "type": "code",
                    "role": "frontend_design",
                },
            ],
            resolver=_resolver,
            tools=parent_tools,
        )
        assert res.error is None
        by_task = {r["task_id"]: r for r in stub_runner}
        assert by_task["a"]["tools"] == parent_tools
        assert by_task["a"]["role_skill"] is None
        assert by_task["b"]["tools"] == role_tools
        assert by_task["b"]["role_skill"] == "frontend_design"

    def test_same_role_resolved_once(self, stub_runner):
        """同一角色多子任务只解析一次(避免重复装配开销)。"""
        conn = _FakeConn(kind="room")
        calls: list[str] = []

        async def _resolver(role):
            calls.append(role)
            return ["R"]

        res = _run_handler(
            conn,
            subtasks=[
                {
                    "id": "a", "prompt": "分析", "type": "analysis",
                    "role": "frontend_design",
                },
                {
                    "id": "b", "prompt": "转 PPT", "type": "code",
                    "role": "frontend_design",
                },
            ],
            resolver=_resolver,
        )
        assert res.error is None
        assert calls == ["frontend_design"]


# ────────────────────────────────────────────────────────────────────────────
# 5. 子会话落库(真实 DB)
# ────────────────────────────────────────────────────────────────────────────


def _make_runner(*, parent_session_id: int, role_skill, subagent_id: int = 1):
    from private_agent.core.subagent import SubagentRunner

    async def _sink(ev):
        return None

    async def _spf(conn, sid):
        return "prompt"

    return SubagentRunner(
        cfg={},
        subagent_id=subagent_id,
        task_id="ppt",
        prompt="转 PPT",
        parent_session_id=parent_session_id,
        parent_turn=1,
        tools=[],
        event_sink=_sink,
        system_prompt_factory=_spf,
        adapter_factory=lambda m: None,
        role_skill=role_skill,
    )


class TestSubSessionRolePersistence:
    def test_role_override_writes_role_and_room_workspace(self):
        """房间会话的子代理: 角色切到成员, 工作区继承房间共享目录。"""
        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                parent = await conn.fetchval(
                    "INSERT INTO sessions (title, kind, locked_skill_name, "
                    "locked_skill_version, workspace) "
                    "VALUES ('room', 'room', 'office', '1.1.0', "
                    "'D:/PA/rooms/20260911-153012-abcd') RETURNING id"
                )
                runner = _make_runner(
                    parent_session_id=parent, role_skill="frontend_design"
                )
                runner._conn = conn
                sub_id = await runner._create_sub_session()

                row = await conn.fetchrow(
                    "SELECT kind, title, locked_skill_name, "
                    "locked_skill_version, workspace FROM sessions WHERE id=$1",
                    sub_id,
                )
                assert row["kind"] == "sub"
                assert row["locked_skill_name"] == "frontend_design"
                # 版本置 None(子代理为一次性会话, 不参与版本锁定语义)
                assert row["locked_skill_version"] is None
                # 工作区继承 = 房间共享目录 → 产物交接的机制基础
                assert row["workspace"] == "D:/PA/rooms/20260911-153012-abcd"
                assert (
                    runner._cfg["system"]["workspace_root"]
                    == "D:/PA/rooms/20260911-153012-abcd"
                )
                assert "frontend_design" in row["title"]
            finally:
                await conn.close()

        asyncio.run(_run_it())

    def test_no_role_override_keeps_parent_skill(self):
        """零回归: 不传 role_skill → 完全继承父会话角色与版本。"""
        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                parent = await conn.fetchval(
                    "INSERT INTO sessions (title, locked_skill_name, "
                    "locked_skill_version, workspace) "
                    "VALUES ('zizhan', 'office', '1.1.0', 'D:/PA/zizhan') "
                    "RETURNING id"
                )
                runner = _make_runner(
                    parent_session_id=parent, role_skill=None
                )
                runner._conn = conn
                sub_id = await runner._create_sub_session()

                row = await conn.fetchrow(
                    "SELECT locked_skill_name, locked_skill_version, workspace "
                    "FROM sessions WHERE id=$1",
                    sub_id,
                )
                assert row["locked_skill_name"] == "office"
                assert row["locked_skill_version"] == "1.1.0"
                assert row["workspace"] == "D:/PA/zizhan"
            finally:
                await conn.close()

        asyncio.run(_run_it())

    def test_room_parent_meta_is_inherited_by_sub_session(self):
        """W3: 房间会话的子代理必须继承 room_meta —— 成员侧系统提示词据此
        注入「会议室约定」(产物写 artifacts/ 而非房间根)。"""
        import json

        from private_agent.core import room as room_core

        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                meta = {
                    "host_role": "office",
                    "members": ["office", "frontend_design"],
                    "goal": "做一份 Q3 经营分析汇报 PPT",
                    "room_key": "20260911-153012-abcd",
                }
                parent = await conn.fetchval(
                    "INSERT INTO sessions (title, kind, locked_skill_name, "
                    "workspace, room_meta) "
                    "VALUES ('room', 'room', 'office', $1, $2::jsonb) "
                    "RETURNING id",
                    "D:/PA/rooms/20260911-153012-abcd",
                    json.dumps(meta, ensure_ascii=False),
                )
                runner = _make_runner(
                    parent_session_id=parent, role_skill="frontend_design"
                )
                runner._conn = conn
                sub_id = await runner._create_sub_session()

                raw = await conn.fetchval(
                    "SELECT room_meta FROM sessions WHERE id=$1", sub_id
                )
                got = room_core.parse_room_meta(raw)
                assert got["host_role"] == "office"
                assert got["members"] == ["office", "frontend_design"]
                assert got["goal"] == "做一份 Q3 经营分析汇报 PPT"
            finally:
                await conn.close()

        asyncio.run(_run_it())

    def test_non_room_parent_leaves_sub_meta_null(self):
        """零回归: 普通会话的子代理 room_meta 必须为 NULL(不得注入房间约定)。"""
        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                parent = await conn.fetchval(
                    "INSERT INTO sessions (title, kind, locked_skill_name, "
                    "workspace) "
                    "VALUES ('zizhan', 'main', 'office', 'D:/PA/zizhan') "
                    "RETURNING id"
                )
                runner = _make_runner(
                    parent_session_id=parent, role_skill=None
                )
                runner._conn = conn
                sub_id = await runner._create_sub_session()

                assert (
                    await conn.fetchval(
                        "SELECT room_meta FROM sessions WHERE id=$1", sub_id
                    )
                    is None
                )
            finally:
                await conn.close()

        asyncio.run(_run_it())
