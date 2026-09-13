"""0.6.0 D-1: MissionRunner 后台编排器测试(真实 DB f1test + mock adapter)。

覆盖(设计文档 A 阶段验收①②③ + D-1):
- 全成功路径: subagent(mock adapter) + script + wait 里程碑 → done + journal
- script 缺 script_code → 失败 escalate
- subagent 失败 → 预算未耗尽: journal fallback + escalated(保守, D-2 纠偏)
- 预算耗尽失败 → escalated(journal 无新 fallback)
- abort: 状态 cancelled → runner 退出且不覆盖终态
- 重启恢复: cleanup_missions_on_startup → escalated(幂等)
- spawn 条件更新失败(state=done) → False 且 registry 不泄漏
- build_delegation_prompt 宪章锚定(纯函数)
- WS 事件推送序列(按 F1-3 schema 契约)
"""
import asyncio
import json
import os

import asyncpg
import pytest

from private_agent.config import loader
from private_agent.core import mission_assembly as ma_mod
from private_agent.core import mission_runner as mr_mod
from private_agent.core.mission_runner import (
    MissionRunner,
    build_delegation_prompt,
    cleanup_missions_on_startup,
)
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

_FAST_SUB_CFG = {
    "heartbeat_interval_sec": 0.2,
    "heartbeat_timeout_sec": 1.0,
    "heartbeat_poll_sec": 0.2,
    "grace_sec": 1.0,
    "max_total_lifetime_sec": 999.0,
    "max_parallel": 3,
    "max_nesting_depth": 2,
    "cancel_wait_sec": 0.5,
    "max_restarts": 0,
}


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


@pytest.fixture(scope="module", autouse=True)
def _schema_fixture():
    _setup_schema()


@pytest.fixture(autouse=True)
def _sandbox_config():
    """code_execution_handler 依赖模块级沙箱配置(main.py 启动注入;
    测试按同源方式注入 config.yaml sandbox 节 —— B1 修复教训)。"""
    from private_agent.tools.builtins.code_execution import set_sandbox_config

    set_sandbox_config(loader.load_config().get("sandbox"))


@pytest.fixture(autouse=True)
def _patch_db_connect(monkeypatch):
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)
    monkeypatch.setattr(db, "connect", _fake_connect)


@pytest.fixture(autouse=True)
def _clean_tables():
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("TRUNCATE missions, subagents RESTART IDENTITY CASCADE")
        finally:
            await conn.close()
    asyncio.run(_run())
    # 清进程级注册表/任务表(跨测试隔离)
    ma_mod.mission_registry._count = 0
    mr_mod.mission_tasks.clear()


def _test_cfg() -> dict:
    cfg = loader.load_config()
    cfg["tools"]["subagent"] = dict(_FAST_SUB_CFG)
    return cfg


class _OkAdapter:
    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
        return ChatResult(content="里程碑产出: OK")


async def _create_session(conn) -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title) VALUES ('d1-runner') RETURNING id"
    )


async def _create_mission(
    conn, session_id: int, plan: list, budget: dict | None = None
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO missions (session_id, charter, plan, budget, state, journal)
        VALUES ($1, $2, $3, $4, 'planning', '[]'::jsonb)
        RETURNING id
        """,
        session_id,
        json.dumps({"goal": "验证长任务编排", "constraints": ["不得换数据源"]},
                   ensure_ascii=False),
        json.dumps(plan, ensure_ascii=False),
        json.dumps(budget or {"max_fallbacks": 2, "max_total_sec": 3600},
                   ensure_ascii=False),
    )


async def _get_mission(conn, mid: int) -> dict:
    row = await conn.fetchrow(
        "SELECT state, plan, journal, error FROM missions WHERE id=$1", mid
    )
    plan = row["plan"] or []
    journal = row["journal"] or []
    if isinstance(plan, str):
        plan = json.loads(plan)
    if isinstance(journal, str):
        journal = json.loads(journal)
    return {"state": row["state"], "plan": plan, "journal": journal,
            "error": row["error"]}


def _spawn_kwargs(session_id: int, events: list) -> dict:
    async def _sys(conn, sid):
        return "sub system prompt"

    def _af(model_id):
        return _OkAdapter()

    async def _sink(ev: dict) -> None:
        events.append(ev)

    return {
        "session_id": session_id,
        "event_sink": _sink,
        "system_prompt_factory": _sys,
        "adapter_factory": _af,
        "compress_adapter": None,
        "tools": [
            _tool("web_search"), _tool("file_write"), _tool("code_execution"),
        ],
    }


def _tool(name: str):
    from private_agent.tools.defs import ToolDef

    return ToolDef(name=name, description=name, parameters_schema={}, handler=None)


async def _wait_mission_done_async(mid: int, timeout: float = 20.0) -> str:
    """同一事件循环内轮询终态(与 runner task 同循环, 不可跨 asyncio.run)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        for _ in range(int(timeout / 0.2)):
            state = await conn.fetchval(
                "SELECT state FROM missions WHERE id=$1", mid
            )
            if state in ("done", "failed", "cancelled", "escalated"):
                return state
            await asyncio.sleep(0.2)
        raise AssertionError(f"mission #{mid} 未在 {timeout}s 内到终态")
    finally:
        await conn.close()


# ── 全成功路径 ──────────────────────────────────────────────────────────

def test_full_success_path_done():
    """subagent(mock 成功) + script(带 code) + wait → state=done + journal 留痕。"""
    _setup_schema()
    events: list = []

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [
                {"id": "m1", "milestone": "调研", "executor_type": "subagent",
                 "task_type": "search"},
                {"id": "m2", "milestone": "汇总", "executor_type": "script",
                 "script_code": "print('sum ok')"},
                {"id": "m3", "milestone": "间隔", "executor_type": "wait",
                 "wait_sec": 0.1},
            ]
            return await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
    async def _flow() -> tuple:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [
                {"id": "m1", "milestone": "调研", "executor_type": "subagent",
                 "task_type": "search"},
                {"id": "m2", "milestone": "汇总", "executor_type": "script",
                 "script_code": "print('sum ok')"},
                {"id": "m3", "milestone": "间隔", "executor_type": "wait",
                 "wait_sec": 0.1},
            ]
            mid = await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
        kw = _spawn_kwargs(session_id=sid, events=events)
        ok = await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        assert ok is True
        state = await _wait_mission_done_async(mid)
        m = None
        conn = await asyncpg.connect(TEST_DSN)
        try:
            m = await _get_mission(conn, mid)
        finally:
            await conn.close()
        kinds = [e["kind"] for e in m["journal"]]
        return state, m, ma_mod.mission_registry.running(), kinds, len(events)
    state, m, running, kinds, n_events = asyncio.run(_flow())
    assert state == "done"
    assert m["state"] == "done"
    assert all(ms["status"] == "done" for ms in m["plan"])
    assert "milestone_done" in kinds
    assert running == 0, "registry 名额必须释放"
    assert n_events > 0
    ev_types = [e["type"] for e in events]
    assert "mission_created" in ev_types
    assert "mission_done" in ev_types
    assert "mission_journal" in ev_types


# ── script 里程碑 ───────────────────────────────────────────────────────

def test_script_missing_code_escalates():
    """script 缺 script_code → 里程碑失败 → escalated(预算未耗尽记 fallback)。"""
    _setup_schema()
    events: list = []

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "s1", "milestone": "算", "executor_type": "script"}]
            return await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
    async def _flow() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "s1", "milestone": "算", "executor_type": "script"}]
            mid = await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid,
                                  **_spawn_kwargs(sid, events))
        await _wait_mission_done_async(mid)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            m = await _get_mission(conn, mid)
        finally:
            await conn.close()
        m["fallback_count"] = sum(
            1 for e in m["journal"] if e.get("kind") == "fallback"
        )
        return m
    m = asyncio.run(_flow())
    assert m["state"] == "escalated"
    assert m["fallback_count"] == 1, "失败应消耗一次改道预算(journal kind=fallback)"
    assert m["plan"][0]["status"] == "failed"


def test_budget_exhausted_escalates_without_extra_fallback():
    """预算已耗尽(max=1 且 journal 已有 1 次 fallback) → escalated 不再消耗。"""
    _setup_schema()
    events: list = []

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "s1", "milestone": "算", "executor_type": "script"}]
            mid = await _create_mission(conn, sid, plan,
                                        budget={"max_fallbacks": 1})
            # 预置: 已有 1 次改道记录(耗尽)
            await conn.execute(
                "UPDATE missions SET journal = journal || $2::jsonb WHERE id=$1",
                mid,
                json.dumps([{"kind": "fallback", "ts": "t", "detail": "预置"}],
                           ensure_ascii=False),
            )
            return mid
        finally:
            await conn.close()
    async def _flow() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "s1", "milestone": "算", "executor_type": "script"}]
            mid = await _create_mission(conn, sid, plan,
                                        budget={"max_fallbacks": 1})
            await conn.execute(
                "UPDATE missions SET journal = journal || $2::jsonb WHERE id=$1",
                mid,
                json.dumps([{"kind": "fallback", "ts": "t", "detail": "预置"}],
                           ensure_ascii=False),
            )
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid,
                                  **_spawn_kwargs(sid, events))
        await _wait_mission_done_async(mid)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            m = await _get_mission(conn, mid)
        finally:
            await conn.close()
        m["fallback_count"] = sum(
            1 for e in m["journal"] if e.get("kind") == "fallback"
        )
        return m
    m = asyncio.run(_flow())
    assert m["state"] == "escalated"
    assert m["fallback_count"] == 1, "预算耗尽不应再消耗"


# ── subagent 里程碑(mock adapter 失败) ──────────────────────────────────

def test_subagent_milestone_failure_escalates(monkeypatch):
    """mock adapter 抛错 → subagent failed → escalate。"""
    _setup_schema()
    events: list = []

    class _BadAdapter:
        provider_name = "mock"
        capability = ModelCapability(
            streaming=False, function_calling=True, vision=False, json_mode=False
        )

        async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
            raise RuntimeError("model exploded")

    kw = _spawn_kwargs(session_id=1, events=events)
    kw["adapter_factory"] = lambda model_id: _BadAdapter()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "m1", "milestone": "调研", "executor_type": "subagent"}]
            return await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
    async def _flow() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "m1", "milestone": "调研", "executor_type": "subagent"}]
            mid = await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        return await _wait_mission_done_async(mid, timeout=30)
    assert asyncio.run(_flow()) == "escalated"


# ── abort ───────────────────────────────────────────────────────────────

def test_abort_during_wait_milestone():
    """wait 里程碑期间用户 abort → runner 退出且不覆盖 cancelled 终态。"""
    _setup_schema()
    events: list = []

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "w1", "milestone": "等", "executor_type": "wait",
                     "wait_sec": 30}]
            return await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
    async def _flow() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [{"id": "w1", "milestone": "等", "executor_type": "wait",
                     "wait_sec": 30}]
            mid = await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid,
                                  **_spawn_kwargs(sid, events))
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 等 runner 进入 wait 里程碑(state=executing; 合跑时启动慢, 窗口 20s)
            st = None
            for _ in range(200):
                st = await conn.fetchval("SELECT state FROM missions WHERE id=$1", mid)
                if st == "executing":
                    break
                await asyncio.sleep(0.1)
            assert st == "executing", f"runner 未在窗口内启动(state={st!r})"
            await conn.execute(
                "UPDATE missions SET state='cancelled', completed_at=now() "
                "WHERE id=$1 AND state='executing'",
                mid,
            )
            # runner 应在轮询间隔内退出且不覆盖(同循环轮询)
            for _ in range(100):
                st = await conn.fetchval("SELECT state FROM missions WHERE id=$1", mid)
                if st != "executing":
                    break
                await asyncio.sleep(0.2)
            return await conn.fetchval("SELECT state FROM missions WHERE id=$1", mid)
        finally:
            await conn.close()
    state = asyncio.run(_flow())
    assert state == "cancelled"
    # runner 侧轮询间隔(2s)后才退出并释放名额 —— 等待而非立即断言
    async def _wait_release() -> int:
        for _ in range(50):
            if ma_mod.mission_registry.running() == 0:
                break
            await asyncio.sleep(0.2)
        return ma_mod.mission_registry.running()
    assert asyncio.run(_wait_release()) == 0


# ── 重启恢复 ────────────────────────────────────────────────────────────

def test_cleanup_missions_on_startup_escalates_and_idempotent():
    """executing/planning → escalated(interrupted); 二次调用幂等 0 行。"""
    _setup_schema()

    async def _run() -> tuple[int, int, str, str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            m1 = await _create_mission(conn, sid, [])
            await conn.execute("UPDATE missions SET state='executing' WHERE id=$1", m1)
            m2 = await _create_mission(conn, sid, [])
            n1 = await cleanup_missions_on_startup(conn, {})
            n2 = await cleanup_missions_on_startup(conn, {})
            s1 = await conn.fetchval("SELECT state FROM missions WHERE id=$1", m1)
            s2 = await conn.fetchval("SELECT state FROM missions WHERE id=$1", m2)
            return n1, n2, s1, s2
        finally:
            await conn.close()
    n1, n2, s1, s2 = asyncio.run(_run())
    assert n1 == 2
    assert n2 == 0, "幂等: 二次扫描无运行态可清"
    assert s1 == "escalated" and s2 == "escalated"


# ── spawn 守卫 ──────────────────────────────────────────────────────────

def test_spawn_rejects_non_runnable_state():
    """state=done → spawn 条件更新 0 行 → False 且 registry 不泄漏。"""
    _setup_schema()

    async def _run() -> tuple[bool, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            mid = await _create_mission(conn, sid, [])
            await conn.execute("UPDATE missions SET state='done' WHERE id=$1", mid)
            ok = await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid,
                                           **_spawn_kwargs(sid, []))
            return ok, ma_mod.mission_registry.running()
        finally:
            await conn.close()
    ok, running = asyncio.run(_run())
    assert ok is False
    assert running == 0, "条件更新失败必须回滚 registry 名额"


# ── 宪章锚定(纯函数) ────────────────────────────────────────────────────

def test_build_delegation_prompt_anchored_to_charter():
    prompt = build_delegation_prompt(
        {"goal": "整理缺陷清单", "constraints": ["不得换数据源"]},
        {"id": "m1", "milestone": "扫描页面", "dod": ["逐页验证"]},
    )
    assert "[任务目标] 整理缺陷清单" in prompt
    assert "不得换数据源" in prompt
    assert "[当前阶段] 扫描页面" in prompt
    assert "逐页验证" in prompt
    assert "不得自行更换" in prompt  # 防漂移指令内嵌


# ── 0.6.0 P3: V1/G5 payload 补齐 + W4 里程碑 role ──────────────────────────

def test_mission_created_payload_carries_goal_and_plan():
    """V1(G5 修复): mission_created 必须带 goal + plan(含 role/status),
    否则前端里程碑列表只能靠 REST 兜底补, 首帧恒空。"""
    _setup_schema()
    events: list = []

    async def _flow():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [
                {"id": "m1", "milestone": "调研", "executor_type": "subagent",
                 "task_type": "search", "role": "data_analysis"},
                {"id": "m2", "milestone": "间隔", "executor_type": "wait",
                 "wait_sec": 0.05},
            ]
            mid = await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
        kw = _spawn_kwargs(session_id=sid, events=events)
        kw["role_tools_resolver"] = _role_resolver
        assert await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        await _wait_mission_done_async(mid)
        return mid

    asyncio.run(_flow())
    created = [e for e in events if e["type"] == "mission_created"]
    assert created, "缺少 mission_created 事件"
    ev = created[0]
    assert ev["goal"] == "验证长任务编排"
    plan = ev["plan"]
    assert isinstance(plan, list) and len(plan) == 2
    assert plan[0]["id"] == "m1"
    assert plan[0]["role"] == "data_analysis"
    assert plan[0]["executor_type"] == "subagent"
    assert plan[0]["status"] in ("pending", "done")  # 事件发出时为 pending
    assert plan[1]["role"] is None


async def _role_resolver(role: str):
    assert role in ("office", "data_analysis", "frontend_design")
    return [_tool("file_read"), _tool("file_write")]


def test_role_milestone_assembles_role_skill_and_tools(monkeypatch):
    """W4: role 里程碑 → SubagentRunner(role_skill=角色, tools=角色白名单);
    子会话 locked_skill_name 切到该角色。"""
    _setup_schema()
    events: list = []

    async def _flow():
        recorded: list[dict] = []
        real_runner = mr_mod.SubagentRunner

        class _Recording(real_runner):
            def __init__(self, **kw):
                super().__init__(**kw)
                recorded.append(
                    {
                        "role_skill": kw.get("role_skill"),
                        "tools": [t.name for t in (kw.get("tools") or [])],
                    }
                )

        monkeypatch.setattr(mr_mod, "SubagentRunner", _Recording)

        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [
                {"id": "m1", "milestone": "转制商务 PPT",
                 "executor_type": "subagent", "role": "frontend_design"},
            ]
            mid = await _create_mission(conn, sid, plan)
            parent_ws = "D:/PA/rooms/20260912-090000-aabb"
            await conn.execute(
                "UPDATE sessions SET workspace=$2, kind='room' WHERE id=$1",
                sid, parent_ws,
            )
        finally:
            await conn.close()
        kw = _spawn_kwargs(session_id=sid, events=events)
        kw["role_tools_resolver"] = _role_resolver
        assert await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        state = await _wait_mission_done_async(mid)
        return sid, state, recorded

    sid, state, recorded = asyncio.run(_flow())
    assert state == "done"
    assert len(recorded) == 1
    assert recorded[0]["role_skill"] == "frontend_design"
    # 工具集按角色解析(非父会话工具), 且解析先于建行
    assert sorted(recorded[0]["tools"]) == ["file_read", "file_write"]
    # 子会话行: locked_skill_name 切到角色, workspace 继承房间目录
    async def _check():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sub_sid = await conn.fetchval(
                "SELECT session_id FROM subagents ORDER BY id DESC LIMIT 1"
            )
            return await conn.fetchrow(
                "SELECT kind, locked_skill_name, workspace "
                "FROM sessions WHERE id=$1",
                sub_sid,
            )
        finally:
            await conn.close()

    row = asyncio.run(_check())
    assert row["locked_skill_name"] == "frontend_design"
    assert row["workspace"] == "D:/PA/rooms/20260912-090000-aabb"


def test_role_milestone_without_resolver_fails_without_side_effects():
    """role 里程碑但未装配解析器 → 里程碑快速失败, **不建子代理行**
    (解析先于副作用), 预算耗尽后 escalate。"""
    _setup_schema()
    events: list = []

    async def _flow():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await _create_session(conn)
            plan = [
                {"id": "m1", "milestone": "转 PPT",
                 "executor_type": "subagent", "role": "frontend_design"},
            ]
            mid = await _create_mission(conn, sid, plan)
        finally:
            await conn.close()
        kw = _spawn_kwargs(session_id=sid, events=events)
        # 不注入 role_tools_resolver
        assert await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        # 注意: 等待必须在**同一事件循环**内 —— asyncio.run 返回会取消后台
        # runner task(否则 runner 尚未执行任何一步, 测的是空转)。
        state = await _wait_mission_done_async(mid, timeout=30.0)
        return mid, state

    mid, state = asyncio.run(_flow())
    assert state == "escalated"

    async def _no_rows():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval("SELECT COUNT(*) FROM subagents")
        finally:
            await conn.close()

    assert asyncio.run(_no_rows()) == 0
    # 失败原因可读(含角色名) —— 落在 journal 的 escalated 条目, 供主持人/用户判断
    async def _reason():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            raw = await conn.fetchval("SELECT journal FROM missions WHERE id=$1", mid)
            journal = json.loads(raw) if isinstance(raw, str) else (raw or [])
            return [e.get("detail", "") for e in journal if e.get("kind") == "escalated"]
        finally:
            await conn.close()

    reasons = asyncio.run(_reason())
    assert any("frontend_design" in r for r in reasons), reasons
