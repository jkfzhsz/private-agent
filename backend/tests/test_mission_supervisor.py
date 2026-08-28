"""0.6.0 D-2: 监督轮测试 —— 纯函数(prompt/解析) + redelegate 集成闭环。

覆盖(设计文档 B 阶段验收①④ / V4):
- parse: 正常 JSON / ```json 包裹 / 非法 action / 空输出 → wait_user 保守回退
- build_supervision_user_prompt: 宪章/计划/台账/失败详情齐全
- 集成: 里程碑失败 → 监督 redelegate → 重派成功 → state=done(预算消耗 1)
- 集成: 监督 wait_user → escalated
- 集成: 监督调用异常 → escalated(保守)
"""
import asyncio
import json
import os

import asyncpg
import pytest

from private_agent.config import loader
from private_agent.core import mission_assembly as ma_mod
from private_agent.core import mission_runner as mr_mod
from private_agent.core.mission_runner import MissionRunner
from private_agent.core.mission_supervisor import (
    build_supervision_user_prompt,
    parse_supervision_decision,
    supervise_failure,
)
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations
from private_agent.tools.defs import ToolDef

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

_FAST_SUB_CFG = {
    "heartbeat_interval_sec": 0.2, "heartbeat_timeout_sec": 1.0,
    "heartbeat_poll_sec": 0.2, "grace_sec": 1.0,
    "max_total_lifetime_sec": 999.0, "max_parallel": 3,
    "max_nesting_depth": 2, "cancel_wait_sec": 0.5, "max_restarts": 0,
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
    ma_mod.mission_registry._count = 0
    mr_mod.mission_tasks.clear()


def _test_cfg() -> dict:
    cfg = loader.load_config()
    cfg["tools"]["subagent"] = dict(_FAST_SUB_CFG)
    return cfg


class _CapAdapter:
    """capability 适配(基于子代理/监督调用序取预设结果)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, behavior):
        self._behavior = behavior  # list[Exception | ChatResult], 按调用序

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
        item = self._behavior.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _tool(name: str) -> ToolDef:
    return ToolDef(name=name, description=name, parameters_schema={}, handler=None)


def _spawn_kwargs(session_id: int, events: list, behavior: list) -> dict:
    """adapter_factory 每次调用发一个新 _CapAdapter(共享同一 behavior 队列)。"""
    queue = list(behavior)

    def _af(model_id):
        return _CapAdapter(queue)

    async def _sys(conn, sid):
        return "sys"

    async def _sink(ev: dict) -> None:
        events.append(ev)

    return {
        "session_id": session_id,
        "event_sink": _sink,
        "system_prompt_factory": _sys,
        "adapter_factory": _af,
        "compress_adapter": None,
        "tools": [_tool("web_search")],
    }


async def _create_mission(conn, session_id: int, plan: list, budget: dict | None = None) -> int:
    return await conn.fetchval(
        "INSERT INTO missions (session_id, charter, plan, budget, state, journal) "
        "VALUES ($1, $2, $3, $4, 'planning', '[]'::jsonb) RETURNING id",
        session_id,
        json.dumps({"goal": "验证监督纠偏", "constraints": ["不得换数据源"]}, ensure_ascii=False),
        json.dumps(plan, ensure_ascii=False),
        json.dumps(budget or {"max_fallbacks": 2, "max_total_sec": 3600}, ensure_ascii=False),
    )


async def _wait_terminal(mid: int, timeout: float = 25.0) -> str:
    conn = await asyncpg.connect(TEST_DSN)
    try:
        for _ in range(int(timeout / 0.2)):
            st = await conn.fetchval("SELECT state FROM missions WHERE id=$1", mid)
            if st in ("done", "failed", "cancelled", "escalated"):
                return st
            await asyncio.sleep(0.2)
        raise AssertionError("mission 未到终态")
    finally:
        await conn.close()


# ── 纯函数 ──────────────────────────────────────────────────────────────

def test_parse_decision_normal_and_wrapped():
    assert parse_supervision_decision('{"action": "redelegate", "reason": "网络抖动"}')["action"] == "redelegate"
    wrapped = '好的，裁决如下:\n```json\n{"action": "wait_user", "reason": "偏离目标"}\n```'
    assert parse_supervision_decision(wrapped)["action"] == "wait_user"


def test_parse_decision_invalid_falls_back_wait_user():
    assert parse_supervision_decision("")["action"] == "wait_user"
    assert parse_supervision_decision("no json here")["action"] == "wait_user"
    assert parse_supervision_decision('{"action": "nuke"}')["action"] == "wait_user"
    assert parse_supervision_decision('{"action": "redelegate"')["action"] == "wait_user"  # 截断 JSON


def test_parse_adjust_plan_with_fix():
    """adjust_plan 合法: 带 milestone_fix; 缺 fix → 保守回退 wait_user。"""
    d = parse_supervision_decision(
        '{"action": "adjust_plan", "reason": "描述不清", '
        '"milestone_fix": {"id": "m1", "milestone": "修正后扫描", '
        '"prompt_template": "改用镜像重试"}}'
    )
    assert d["action"] == "adjust_plan"
    assert d["milestone_fix"]["id"] == "m1"
    assert d["milestone_fix"]["prompt_template"] == "改用镜像重试"
    # 缺 milestone_fix → 保守回退
    d2 = parse_supervision_decision('{"action": "adjust_plan", "reason": "x"}')
    assert d2["action"] == "wait_user"


def test_redelegate_adjust_plan_updates_plan_and_succeeds():
    """D-3 集成: 监督 adjust_plan → plan 里程碑被修正 + 重派成功 → done。"""
    _setup_schema()
    events: list = []
    behavior = [
        RuntimeError("boom"),                                   # 第 1 次子代理失败
        ChatResult(content=json.dumps({
            "action": "adjust_plan", "reason": "里程碑描述不清",
            "milestone_fix": {"id": "m1", "milestone": "修正后的调研",
                              "prompt_template": "按修正指令执行"},
        })),                                                    # 监督: adjust_plan
        ChatResult(content="修正后重派成功"),                     # 第 2 次子代理成功
    ]
    kw = _spawn_kwargs(1, events, behavior)

    async def _flow() -> tuple:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title) VALUES ('d3-adjust') RETURNING id"
            )
            mid = await _create_mission(conn, sid, [
                {"id": "m1", "milestone": "原描述", "executor_type": "subagent"}
            ])
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        state = await _wait_terminal(mid)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow("SELECT plan FROM missions WHERE id=$1", mid)
            plan = row["plan"] if not isinstance(row["plan"], str) else json.loads(row["plan"])
            return state, plan
        finally:
            await conn.close()
    state, plan = asyncio.run(_flow())
    assert state == "done"
    assert plan[0]["milestone"] == "修正后的调研"
    assert plan[0]["prompt_template"] == "按修正指令执行"
    assert plan[0]["status"] == "done"


def test_user_prompt_contains_all_sections():
    prompt = build_supervision_user_prompt(
        {"goal": "整理缺陷清单", "constraints": ["不得换数据源"]},
        [{"id": "m1", "milestone": "扫描", "executor_type": "subagent", "status": "failed"}],
        [{"kind": "fallback", "detail": "上次失败"}],
        {"id": "m1", "milestone": "扫描"},
        "连接超时",
    )
    assert "整理缺陷清单" in prompt and "不得换数据源" in prompt
    assert "[m1] 扫描" in prompt and "failed" in prompt
    assert "上次失败" in prompt and "连接超时" in prompt


# ── 集成: redelegate 重试成功 ────────────────────────────────────────────

def test_redelegate_then_success_done():
    """失败 → 监督 redelegate → 重派成功 → done; 预算消耗 1 + journal supervision。"""
    _setup_schema()
    events: list = []
    behavior = [
        RuntimeError("model exploded"),                      # 第 1 次子代理: 失败
        ChatResult(content='{"action": "redelegate", "reason": "网络抖动, 方向正确"}'),  # 监督
        ChatResult(content="重派成功产出"),                    # 第 2 次子代理: 成功
    ]
    kw = _spawn_kwargs(1, events, behavior)

    async def _flow() -> tuple:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title) VALUES ('d2-rede') RETURNING id"
            )
            mid = await _create_mission(conn, sid, [
                {"id": "m1", "milestone": "调研", "executor_type": "subagent"}
            ])
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        state = await _wait_terminal(mid)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow("SELECT plan, journal FROM missions WHERE id=$1", mid)
            plan = row["plan"] if not isinstance(row["plan"], str) else json.loads(row["plan"])
            journal = row["journal"] if not isinstance(row["journal"], str) else json.loads(row["journal"])
            return state, plan, journal
        finally:
            await conn.close()
    state, plan, journal = asyncio.run(_flow())
    assert state == "done"
    assert plan[0]["status"] == "done"
    kinds = [e["kind"] for e in journal]
    assert "fallback" in kinds and "supervision" in kinds
    # W8: mission_report 消息已落库(msg_kind 隔离)
    async def _report_count() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "SELECT count(*) FROM messages WHERE msg_kind='mission_report'"
            )
        finally:
            await conn.close()
    assert asyncio.run(_report_count()) >= 1


def test_supervisor_wait_user_escalates():
    """监督 wait_user → escalated(即使预算未耗尽)。"""
    _setup_schema()
    events: list = []
    behavior = [
        RuntimeError("boom"),
        ChatResult(content='{"action": "wait_user", "reason": "里程碑偏离目标"}'),
    ]
    kw = _spawn_kwargs(1, events, behavior)

    async def _flow() -> tuple:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title) VALUES ('d2-wait') RETURNING id"
            )
            mid = await _create_mission(conn, sid, [
                {"id": "m1", "milestone": "调研", "executor_type": "subagent"}
            ])
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        state = await _wait_terminal(mid)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            journal = await conn.fetchval("SELECT journal FROM missions WHERE id=$1", mid)
            j = journal if not isinstance(journal, str) else json.loads(journal)
            fb = sum(1 for e in j if e.get("kind") == "fallback")
            sup = [e for e in j if e.get("kind") == "supervision"]
            return state, fb, sup
        finally:
            await conn.close()
    state, fb, sup = asyncio.run(_flow())
    assert state == "escalated"
    assert fb == 1, "失败应先消耗预算再监督判定"
    assert sup and "wait_user" in sup[-1]["detail"]


def test_supervisor_crash_falls_back_escalate():
    """监督 chat 异常 → 保守 escalated(不误自动纠偏)。"""
    _setup_schema()
    events: list = []

    class _ExplodeAdapter:
        provider_name = "mock"
        capability = ModelCapability(
            streaming=False, function_calling=True, vision=False, json_mode=False
        )

        async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
            raise RuntimeError("supervisor down")

    kw = _spawn_kwargs(1, events, [])
    kw["adapter_factory"] = lambda model_id: _ExplodeAdapter()

    async def _flow() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title) VALUES ('d2-crash') RETURNING id"
            )
            mid = await _create_mission(conn, sid, [
                {"id": "m1", "milestone": "调研", "executor_type": "subagent"}
            ])
        finally:
            await conn.close()
        await MissionRunner.spawn(cfg=_test_cfg(), mission_id=mid, **kw)
        return await _wait_terminal(mid, timeout=30)
    assert asyncio.run(_flow()) == "escalated"


def test_supervise_failure_api_direct():
    """supervise_failure 直接调用: 正常 JSON → redelegate; 异常 adapter → wait_user。"""
    class _Ok:
        async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
            assert messages[0]["role"] == "system"
            return ChatResult(content='{"action":"redelegate","reason":"ok"}')

    d = asyncio.run(supervise_failure(_Ok(), {}, [], [], {"id": "m1"}, "x"))
    assert d["action"] == "redelegate"

    class _Bad:
        async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
            raise RuntimeError("down")

    d = asyncio.run(supervise_failure(_Bad(), {}, [], [], {"id": "m1"}, "x"))
    assert d["action"] == "wait_user"
