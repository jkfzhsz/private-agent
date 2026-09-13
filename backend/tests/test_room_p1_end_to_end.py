"""会议室 P1 端到端 —— 「建房 → 主持人委派角色成员 → 产物落到房间目录」闭环。

设计文档 §8 P1 验收: 「CLI 可全量验证: 建房 → 主持人委派 role=frontend_design
→ 断言产物落在房间目录」。

本文件把三段真实链路串起来(仅桩掉"模型调用"这一个不可离线复现的环节):
1. **真实 HTTP 端点** `POST /admin/rooms` 建房(TestClient + admin.router);
2. **真实委派 handler** `_delegate_handler`(未桩化)+ **真实 SubagentRunner**
   子类(只覆写 `run()`, 保留真实 `_create_sub_session`);
3. **真实提示词装配** `main._get_system_prompt` + **真实 ReactLoop 配置读取**。

断言的核心事实: 成员(清和)写入目标 = 房间目录, 且其系统提示词明确要求把
产物写进 `artifacts/`。这两条同时成立, "产物交接"才成立。
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import private_agent.tools.builtins.delegate_subtask as ds
from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.core import room as room_core
from private_agent.core.subagent import SubagentRunner
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

GOAL = "做一份 Q3 经营分析汇报 PPT"
HOST_ROLE = "office"
MEMBER_ROLE = "frontend_design"


def _norm(p: object) -> str:
    """路径分隔符归一化(Windows 反斜杠 → 正斜杠), 供断言比较使用。"""
    return str(p).replace("\\", "/")


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


@pytest.fixture(autouse=True)
def _real_skills_dir(monkeypatch):
    """SkillLoader 默认按 CWD 找 `./skills/`; 测试 CWD 不保证是 backend, 故显式
    指向真实技能目录(与 test_room_core 同做法)。这是**测试环境**问题, 不涉及
    生产路径: 生产由 run_sidecar 设定 CWD。

    三个角色技能必须真实可加载 —— 角色工具白名单与人格都来自它, 桩掉就失去
    端到端意义。
    """
    from pathlib import Path as _Path

    from private_agent.skills.loader import SkillLoader

    real = _Path(__file__).resolve().parents[1] / "skills"

    def _fake_from_cfg(cfg):
        return SkillLoader(dev_dir=str(real))

    monkeypatch.setattr(SkillLoader, "from_cfg", _fake_from_cfg)


@pytest.fixture
def client(monkeypatch, tmp_path):
    """真实 admin 路由 + 真实测试库; rooms_root 指向 tmp(避免污染 D:\\PA)。"""
    _setup_schema()
    app = FastAPI()
    app.include_router(router)

    async def _fake_connect(*_a, **_k):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config
    rooms_root = tmp_path / "rooms"

    def _fake_load_config(*_a, **_k):
        cfg = original_load()
        system = {**(cfg.get("system") or {})}
        system["rooms_root"] = str(rooms_root)
        return {**cfg, "system": system}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)
    return TestClient(app)


def _create_room(client) -> dict:
    resp = client.post(
        "/admin/rooms",
        json={
            "goal": GOAL,
            "members": [HOST_ROLE, MEMBER_ROLE],
            "host_role": HOST_ROLE,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def delegate_with_real_session(monkeypatch):
    """真实 SubagentRunner(仅覆写 run) + 等待式 watchdog(替代轮询)。

    覆写 `run()` 只跳过"模型调用"这一不可离线复现的环节, 角色解析、子会话
    创建、工作区继承、room_meta 继承全部走真实实现。
    """

    class _RealSessionRunner(SubagentRunner):
        async def run(self):
            self._conn = await asyncpg.connect(TEST_DSN)
            try:
                return await self._create_sub_session()
            finally:
                await self._conn.close()

    async def _await_all(**kw):
        tasks = list((kw.get("tasks_by_id") or {}).values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    monkeypatch.setattr(ds, "SubagentRunner", _RealSessionRunner)
    monkeypatch.setattr(ds, "_watchdog_wait", _await_all)


def _run_delegation(room_id: int, *, role: str | None) -> dict:
    """跑一遍真实委派 handler, 返回捕获到的子代理装配信息。

    连接与子代理任务都在**同一个事件循环**内创建(asyncpg 连接绑定创建它的
    loop, 跨 loop 复用会报错)。
    """
    captured: list[dict] = []

    class _CaptureRunner(SubagentRunner):
        async def run(self):
            self._conn = await asyncpg.connect(TEST_DSN)
            try:
                self._sub_session_id = await self._create_sub_session()
                await self._conn.execute(
                    "UPDATE subagents SET session_id=$1, status='succeeded', "
                    "result='ok', finished_at=now() WHERE id=$2",
                    self._sub_session_id,
                    self._subagent_id,
                )
                captured.append(
                    {
                        "subagent_id": self._subagent_id,
                        "sub_session_id": self._sub_session_id,
                        "role_skill": self._role_skill,
                        "tools": self._tools,
                        "cfg": self._cfg,
                    }
                )
                return self._subagent_id
            finally:
                await self._conn.close()

    class _PermissiveRegistry:
        async def acquire(self, *a, **k):
            return True

        async def release(self, *a, **k):
            return None

    async def _await_all(**kw):
        tasks = list((kw.get("tasks_by_id") or {}).values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _sink(_ev):
        return None

    async def _resolve(role_name: str) -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return sorted(
                await room_core.resolve_role_whitelist({}, conn, role_name)
            )
        finally:
            await conn.close()

    subtask: dict = {"id": "ppt", "prompt": "把分析结论转成一页 PPT"}
    if role:
        subtask["role"] = role

    ds_class, ds_wait, ds_registry = (
        ds.SubagentRunner,
        ds._watchdog_wait,
        ds.subagent_type_registry,
    )
    ds.SubagentRunner = _CaptureRunner
    ds._watchdog_wait = _await_all
    ds.subagent_type_registry = _PermissiveRegistry()
    try:
        async def _main():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await ds._delegate_handler(
                    conn=conn,
                    cfg={},
                    session_id=room_id,
                    event_sink=_sink,
                    tools=["T_parent"],
                    args={"subtasks": [subtask]},
                    sc=ds.subagent_cfg(
                        {"tools": {"subagent": {"max_parallel": 1}}}
                    ),
                    system_prompt_factory=None,
                    adapter_factory=None,
                    compress_adapter=None,
                    role_tools_resolver=_resolve,
                )
            finally:
                await conn.close()

        asyncio.run(_main())
    finally:
        ds.SubagentRunner = ds_class
        ds._watchdog_wait = ds_wait
        ds.subagent_type_registry = ds_registry
    assert captured, "委派未产生子会话"
    return captured[0]


# ────────────────────────────────────────────────────────────────────────────
# 端到端
# ────────────────────────────────────────────────────────────────────────────


class TestMeetingRoomP1ClosedLoop:
    def test_room_creation_lays_out_shared_dir(self, client):
        """第 1 段: 建房 → 房间目录骨架 + 会话行。"""
        body = _create_room(client)
        from pathlib import Path

        room_dir = Path(body["room_dir"])
        assert (room_dir / "artifacts").is_dir()
        assert (room_dir / "notes").is_dir()
        readme = (room_dir / "README.md").read_text(encoding="utf-8")
        assert GOAL in readme
        assert "子瞻(office)" in readme

        async def _row():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await conn.fetchrow(
                    "SELECT kind, locked_skill_name, workspace, room_meta "
                    "FROM sessions WHERE id=$1",
                    body["id"],
                )
            finally:
                await conn.close()

        row = asyncio.run(_row())
        assert row["kind"] == "room"
        assert row["locked_skill_name"] == HOST_ROLE
        assert str(row["workspace"]) == str(room_dir)
        assert room_core.parse_room_meta(row["room_meta"])["members"] == [
            HOST_ROLE,
            MEMBER_ROLE,
        ]

    def test_host_prompt_knows_shared_dir_and_delegation(
        self, client, monkeypatch
    ):
        """第 2 段: 主持人提示词含房间目录分工与角色委派方式。"""
        import private_agent.main as main_mod

        body = _create_room(client)

        async def _prompt():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await main_mod._get_system_prompt({}, body["id"], conn)
            finally:
                await conn.close()

        prompt = asyncio.run(_prompt())
        assert "[会议室约定]" in prompt
        assert f"{_norm(body['room_dir'])}/artifacts" in _norm(prompt)
        assert "delegate_subtask" in prompt
        assert MEMBER_ROLE in prompt

    def test_member_session_writes_into_room_dir(
        self, client, delegate_with_real_session
    ):
        """第 3 段(核心): 主持人委派 role=frontend_design → 成员子会话的写入
        目标 = 房间目录, 且提示词要求产物落 artifacts/。"""
        import private_agent.main as main_mod
        from private_agent.core.react_loop import ReactLoop

        body = _create_room(client)
        room_dir = _norm(body["room_dir"])
        got = _run_delegation(body["id"], role=MEMBER_ROLE)

        assert got["role_skill"] == MEMBER_ROLE
        # 角色工具白名单已按角色解析(非父会话工具)
        assert got["tools"] != ["T_parent"]
        assert got["tools"], "角色工具白名单为空(skill 未加载?)"

        async def _sub_row():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await conn.fetchrow(
                    "SELECT kind, locked_skill_name, workspace, room_meta "
                    "FROM sessions WHERE id=$1",
                    got["sub_session_id"],
                )
            finally:
                await conn.close()

        sub = asyncio.run(_sub_row())
        assert sub["kind"] == "sub"
        assert sub["locked_skill_name"] == MEMBER_ROLE
        assert str(sub["workspace"]).replace("\\", "/") == room_dir
        assert room_core.parse_room_meta(sub["room_meta"])["goal"] == GOAL

        # 真实 ReactLoop 会用 cfg.system.workspace_root 强制 file_write.data_dir
        # (react_loop.py:1249-1273) —— 因此该值等于房间目录即"产物落房间目录"。
        assert _norm(got["cfg"]["system"]["workspace_root"]) == room_dir
        loop = ReactLoop(
            session_id=got["sub_session_id"],
            context_manager=None,
            adapter=None,
            tools=[],
            conn=None,
            cfg=got["cfg"],
        )
        assert _norm(loop._workspace_label) == room_dir

        # 成员提示词必须写清"产物进 artifacts/"
        async def _prompt():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await main_mod._get_system_prompt(
                    {}, got["sub_session_id"], conn
                )
            finally:
                await conn.close()

        prompt = asyncio.run(_prompt())
        assert "[会议室约定]" in prompt
        assert f"{room_dir}/artifacts" in _norm(prompt)
        assert "**成员**" in prompt
        assert "delegate_subtask" not in prompt

    def test_delegation_without_role_keeps_parent_identity(
        self, client, delegate_with_real_session
    ):
        """零回归: 房间内不指定 role 的委派 = 继承主持人角色(既有语义)。"""
        body = _create_room(client)
        got = _run_delegation(body["id"], role=None)

        assert got["role_skill"] is None
        assert got["tools"] == ["T_parent"]

        async def _sub_row():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await conn.fetchrow(
                    "SELECT locked_skill_name, workspace FROM sessions WHERE id=$1",
                    got["sub_session_id"],
                )
            finally:
                await conn.close()

        sub = asyncio.run(_sub_row())
        assert sub["locked_skill_name"] == HOST_ROLE
