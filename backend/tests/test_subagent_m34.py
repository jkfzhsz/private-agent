"""V1.5 项-1(ADR-012) M3 协议+前端 / M4 加固 —— 后端测试。

覆盖:
- M3: GET /admin/subagents DB 轮询兜底端点(序列化/过滤/参数校验)
- M3: WS subagent_heartbeat 事件推送(含 phase)
- M4: max_restarts 自动重启(重试成功/耗尽/默认关闭)
- M4: react_events 埋点(stalled/killed 可观测, event_type='subagent')
"""
import asyncio
import json
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.config import loader
from private_agent.core.subagent import SubagentRunner
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations
from private_agent.tools.builtins.delegate_subtask import (
    build_delegate_subtask_tool,
)

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

_FAST_CFG = {
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
def _patch_db_connect(monkeypatch):
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


@pytest.fixture(autouse=True)
def _clean_subagents():
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("TRUNCATE subagents RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_run())


def _test_cfg() -> dict:
    cfg = loader.load_config()
    cfg["tools"]["subagent"] = dict(_FAST_CFG)
    return cfg


class _MockAdapter:
    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None) -> ChatResult:
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        r = self._responses[self._idx]
        self._idx += 1
        return r


class _HungAdapter:
    provider_name = "mock"
    capability = _MockAdapter.capability

    async def chat(self, messages, tools=None, max_tokens=None) -> ChatResult:
        await asyncio.sleep(3600)
        return ChatResult(content="unreachable")


def _make_runner(
    *,
    conn,
    cfg,
    subagent_id: int,
    prompt: str,
    parent_session_id: int,
    parent_turn: int,
    adapter,
    events: list | None = None,
):
    async def _sys(conn, sid):
        return "sub system prompt"

    def _adapter_factory(model_id):
        return adapter

    async def _sink(ev: dict) -> None:
        if events is not None:
            events.append(ev)

    return SubagentRunner(
        cfg=cfg,
        subagent_id=subagent_id,
        task_id="t1",
        prompt=prompt,
        parent_session_id=parent_session_id,
        parent_turn=parent_turn,
        tools=[],
        event_sink=_sink,
        system_prompt_factory=_sys,
        adapter_factory=_adapter_factory,
    )


async def _new_parent_session(conn) -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ('parent', 'mock') "
        "RETURNING id"
    )


async def _insert_pending_subagent(conn, parent_session_id: int) -> int:
    return await conn.fetchval(
        "INSERT INTO subagents (session_id, parent_turn, parent_task, prompt, "
        "model_id, status) VALUES ($1, 1, 't1', 'do something', 'mock', 'pending') "
        "RETURNING id",
        parent_session_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
# M3: GET /admin/subagents DB 轮询兜底端点
# ──────────────────────────────────────────────────────────────────────────────


def test_admin_subagents_endpoint():
    """GET /admin/subagents: 字段序列化 + parent_turn 过滤 + session_id 必选。"""

    async def _seed() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            await conn.execute(
                "INSERT INTO subagents (session_id, parent_turn, parent_task, "
                "prompt, model_id, status, result, tool_calls) "
                "VALUES ($1, 1, 't1', 'do A', 'mock', 'succeeded', 'result A', 2)",
                parent,
            )
            await conn.execute(
                "INSERT INTO subagents (session_id, parent_turn, parent_task, "
                "prompt, model_id, status, error) "
                "VALUES ($1, 2, 't2', 'do B', 'mock', 'failed', 'heartbeat_timeout')",
                parent,
            )
            return parent
        finally:
            await conn.close()

    parent = asyncio.run(_seed())
    from private_agent.main import app

    client = TestClient(app)
    headers = {"X-Admin-Token": "test-admin-token"}
    resp = client.get(f"/admin/subagents?session_id={parent}", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    by_task = {r["parent_task"]: r for r in rows}
    assert by_task["t1"]["status"] == "succeeded"
    assert by_task["t1"]["result"] == "result A"
    assert by_task["t1"]["tool_calls"] == 2
    assert by_task["t2"]["status"] == "failed"
    assert by_task["t2"]["error"] == "heartbeat_timeout"
    # parent_turn 过滤
    resp2 = client.get(
        f"/admin/subagents?session_id={parent}&parent_turn=1", headers=headers
    )
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1
    assert resp2.json()[0]["parent_task"] == "t1"
    # session_id 必选(缺参 → FastAPI 422)
    resp3 = client.get("/admin/subagents", headers=headers)
    assert resp3.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# M3: WS subagent_heartbeat 事件推送
# ──────────────────────────────────────────────────────────────────────────────


def test_subagent_heartbeat_ws_event():
    """runner 心跳循环推 subagent_heartbeat(含 phase), 业务正常完成。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)
            events: list[dict] = []

            class _SlowAdapter:
                provider_name = "mock"
                capability = _MockAdapter.capability

                async def chat(self, messages, tools=None, max_tokens=None):
                    await asyncio.sleep(1.2)  # 留出多个心跳周期
                    return ChatResult(content="done", used_provider="m")

            runner = _make_runner(
                conn=conn, cfg=_test_cfg(), subagent_id=sid, prompt="p",
                parent_session_id=parent, parent_turn=1, adapter=_SlowAdapter(),
                events=events,
            )
            await asyncio.wait_for(runner.run(), timeout=30)
            hbs = [e for e in events if e.get("type") == "subagent_heartbeat"]
            assert len(hbs) >= 1, f"expect heartbeat events, got {events}"
            assert hbs[0]["subagent_id"] == sid
            assert "phase" in hbs[0]
            row = await conn.fetchrow(
                "SELECT status, last_heartbeat_at FROM subagents WHERE id=$1", sid
            )
            assert row["status"] == "succeeded"
            assert row["last_heartbeat_at"] is not None
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# M4: max_restarts 自动重启(默认 0 关, 显式开启时业务异常自动重试)
# ──────────────────────────────────────────────────────────────────────────────


def test_max_restarts_retries_then_succeeds():
    """max_restarts=1: 首次模型异常 → 重启(restart_attempts=1) → 二次成功。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)
            attempts = {"n": 0}

            class _FlakyAdapter:
                provider_name = "mock"
                capability = _MockAdapter.capability

                async def chat(self, messages, tools=None, max_tokens=None):
                    attempts["n"] += 1
                    if attempts["n"] == 1:
                        raise RuntimeError("transient upstream failure")
                    return ChatResult(content="retry ok", used_provider="m")

            cfg = _test_cfg()
            cfg["tools"]["subagent"]["max_restarts"] = 1
            runner = _make_runner(
                conn=conn, cfg=cfg, subagent_id=sid, prompt="p",
                parent_session_id=parent, parent_turn=1, adapter=_FlakyAdapter(),
            )
            await asyncio.wait_for(runner.run(), timeout=30)
            row = await conn.fetchrow(
                "SELECT status, result, restart_attempts FROM subagents WHERE id=$1",
                sid,
            )
            assert row["status"] == "succeeded"
            assert "retry ok" in row["result"]
            assert row["restart_attempts"] == 1
            # 重启埋点入父会话 react_events(kind='restart')
            ev = await conn.fetchval(
                "SELECT payload FROM react_events WHERE session_id=$1 "
                "AND event_type='subagent' ORDER BY id DESC LIMIT 1",
                parent,
            )
            assert ev is not None
            payload = json.loads(ev) if isinstance(ev, str) else ev
            assert payload.get("kind") == "restart"
            assert payload.get("subagent_id") == sid
        finally:
            await conn.close()

    asyncio.run(_run())


def test_max_restarts_exhausted_marks_failed():
    """max_restarts=1 且两次都失败 → failed + restart_attempts=1(耗尽)。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)

            class _AlwaysFail:
                provider_name = "mock"
                capability = _MockAdapter.capability

                async def chat(self, messages, tools=None, max_tokens=None):
                    raise RuntimeError("always broken")

            cfg = _test_cfg()
            cfg["tools"]["subagent"]["max_restarts"] = 1
            runner = _make_runner(
                conn=conn, cfg=cfg, subagent_id=sid, prompt="p",
                parent_session_id=parent, parent_turn=1, adapter=_AlwaysFail(),
            )
            await asyncio.wait_for(runner.run(), timeout=30)
            row = await conn.fetchrow(
                "SELECT status, error, restart_attempts FROM subagents WHERE id=$1",
                sid,
            )
            assert row["status"] == "failed"
            assert "always broken" in row["error"]
            assert row["restart_attempts"] == 1
        finally:
            await conn.close()

    asyncio.run(_run())


def test_restart_disabled_by_default():
    """max_restarts 默认 0: 失败不重启(restart_attempts 保持 0)。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)

            class _Fail:
                provider_name = "mock"
                capability = _MockAdapter.capability

                async def chat(self, messages, tools=None, max_tokens=None):
                    raise RuntimeError("no restart")

            runner = _make_runner(
                conn=conn, cfg=_test_cfg(), subagent_id=sid, prompt="p",
                parent_session_id=parent, parent_turn=1, adapter=_Fail(),
            )
            await asyncio.wait_for(runner.run(), timeout=30)
            row = await conn.fetchrow(
                "SELECT status, restart_attempts FROM subagents WHERE id=$1", sid
            )
            assert row["status"] == "failed"
            assert row["restart_attempts"] == 0
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# M4: react_events 埋点(stalled/killed 可观测)
# ──────────────────────────────────────────────────────────────────────────────


def test_watchdog_emits_react_events():
    """watchdog kill 路径: stalled + killed 埋点入父会话 react_events。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        handler_conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            events: list[dict] = []

            async def _sink(ev):
                events.append(ev)

            async def _sys(c, sid):
                return "sub system prompt"

            cfg = _test_cfg()
            cfg["tools"]["subagent"] = {
                **_FAST_CFG,
                "heartbeat_interval_sec": 60.0,  # 心跳几乎不刷 → 模拟心跳停
                "heartbeat_timeout_sec": 1.0,
                "heartbeat_poll_sec": 0.2,
                "grace_sec": 1.0,
                "cancel_wait_sec": 0.5,
                "max_total_lifetime_sec": 999.0,
            }

            def _af(model_id):
                return _HungAdapter()

            tool = build_delegate_subtask_tool(
                conn=handler_conn, cfg=cfg, session_id=parent, event_sink=_sink,
                tools=[], system_prompt_factory=_sys, adapter_factory=_af,
            )
            handler_task = asyncio.create_task(
                tool.handler({"subtasks": [{"id": "t1", "prompt": "挂起"}]})
            )
            await asyncio.sleep(1.5)  # 等 runner 进入挂起
            await conn.execute(
                "UPDATE subagents SET last_heartbeat_at=now()-interval '10 seconds' "
                "WHERE parent_task='t1'",
            )
            result = await asyncio.wait_for(handler_task, timeout=30)
            assert result.error is None
            assert "heartbeat_timeout" in result.output
            rows = await conn.fetch(
                "SELECT payload FROM react_events WHERE session_id=$1 "
                "AND event_type='subagent' ORDER BY id",
                parent,
            )
            kinds = []
            for r in rows:
                p = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
                kinds.append(p.get("kind"))
            assert "stalled" in kinds, f"expect stalled event, got {kinds}"
            assert "killed" in kinds, f"expect killed event, got {kinds}"
        finally:
            await handler_conn.close()
            await conn.close()

    asyncio.run(_run())
