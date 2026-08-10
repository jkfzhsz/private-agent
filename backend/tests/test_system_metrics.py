"""0.6.0 P1 监控数据链路测试(四窗口架构)。

覆盖(见 docs/next-phase-plan-2026-08-08-four-windows.md §4):
- system_metrics / optim_log 表迁移幂等
- MetricsCollector.collect_once 采集落库(系统级/服务级/会话级)
- MetricsCollector.query 历史查询(范围/名称/会话过滤)
- MetricsCollector.latest_summary 摘要文本(主智能体注入用)
- 监控工具: system_metrics_query / system_status / optim_plan / apply_optim
- optim_log 审批流 API(GET 列表 / PUT 审批)
"""

import pytest
import asyncpg

from private_agent.core.metrics_collector import MetricsCollector

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等迁移, 含 P1 system_metrics/optim_log)。"""
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


@pytest.fixture
async def conn(schema):
    conn = await asyncpg.connect(TEST_DSN)
    yield conn
    await conn.close()


@pytest.fixture(autouse=True)
def _patch_db_to_test(monkeypatch):
    """工具 handler 内 db.connect 指向测试库(默认连生产 private_agent)。"""
    from private_agent.storage import db

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


# ── 表迁移 ───────────────────────────────────────────────────────────────


async def test_monitor_tables_created(schema):
    """system_metrics / optim_log 表已建(新部署)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        for t in ("system_metrics", "optim_log"):
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_tables "
                "WHERE schemaname='public' AND tablename=$1)",
                t,
            )
            assert exists, f"{t} 表缺失"
        # 幂等: 再跑一次 migrate_all
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


# ── 采集器 ───────────────────────────────────────────────────────────────


async def test_collect_once_inserts_metrics(conn):
    """collect_once 采集并落库(含运行时计数器)。"""
    collector = MetricsCollector(db=None, interval_sec=60)
    collector.runtime_stats["ws_conns"] = 2.0
    collector.runtime_stats["active_turns"] = 1.0
    metrics = await collector.collect_once(conn)
    assert "ws_conns" in metrics
    assert metrics["ws_conns"] == 2.0
    # 落库
    rows = await conn.fetch(
        "SELECT name, value FROM system_metrics ORDER BY id"
    )
    names = {r["name"]: r["value"] for r in rows}
    assert names["ws_conns"] == 2.0
    assert names["active_turns"] == 1.0
    # kind=system 必须有 ws_conns
    kinds = await conn.fetch(
        "SELECT DISTINCT kind FROM system_metrics"
    )
    assert "system" in {r["kind"] for r in kinds}


async def test_collect_session_metrics(conn):
    """react_events 聚合: 会话级指标(工具调用/失败)。"""
    # 造一条会话 + react_events
    sid = await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ('m', 'mock') RETURNING id"
    )
    for et in ("final", "tool_call", "tool_error", "delta"):
        await conn.execute(
            "INSERT INTO react_events (session_id, turn, event_type, payload, created_at) "
            "VALUES ($1, 1, $2, '{}', now())",
            sid,
            et,
        )
    collector = MetricsCollector(db=None)
    await collector.collect_once(conn)
    rows = await conn.fetch(
        "SELECT name, value, session_id FROM system_metrics "
        "WHERE kind='session'"
    )
    assert len(rows) >= 3  # turn_count/tool_calls/tool_failures
    by_name = {r["name"]: r for r in rows}
    assert by_name["turn_count"]["value"] >= 1
    assert by_name["tool_calls"]["value"] >= 1
    assert by_name["tool_failures"]["value"] >= 1
    # 会话归属正确
    assert by_name["turn_count"]["session_id"] == sid


async def test_query_filters(conn):
    """query 支持名称/时间范围/会话过滤。"""
    collector = MetricsCollector(db=None)
    collector.runtime_stats["ws_conns"] = 1.0
    await collector.collect_once(conn)
    rows = await collector.query(conn, names=["ws_conns"], since_hours=1)
    assert len(rows) == 1
    assert rows[0]["name"] == "ws_conns"
    # 不存在的名称 → 空
    empty = await collector.query(conn, names=["nonexistent"], since_hours=1)
    assert empty == []


async def test_latest_summary(conn):
    """latest_summary 生成摘要文本(注入主智能体)。"""
    collector = MetricsCollector(db=None)
    collector.runtime_stats["ws_conns"] = 4.0
    collector.runtime_stats["active_turns"] = 2.0
    await collector.collect_once(conn)
    summary = await collector.latest_summary(conn, since_hours=1)
    assert summary.startswith("[System Metrics]")
    assert "ws" in summary


# ── 监控工具 ─────────────────────────────────────────────────────────────


async def test_optim_plan_tool(conn, monkeypatch):
    """optim_plan 落库 optim_log(pending)。"""
    from private_agent.tools.builtins.monitor_tools import _optim_plan_handler

    # mock ctx
    class _Ctx:
        session_id = 1

    result = await _optim_plan_handler(
        {"proposal": "建议调整上下文压缩阈值", "category": "context"}, ctx=_Ctx()
    )
    assert result.error is None
    assert "已提交" in (result.output or "")
    row = await conn.fetchrow(
        "SELECT id, proposal, status FROM optim_log ORDER BY id DESC LIMIT 1"
    )
    assert row["status"] == "pending"
    assert "压缩" in row["proposal"]
    # 缺 proposal → 报错
    err = await _optim_plan_handler({}, ctx=_Ctx())
    assert err.error is not None


async def test_apply_optim_requires_approved(conn, monkeypatch):
    """apply_optim 仅执行 approved 状态, 且限低风险类别。"""
    from private_agent.tools.builtins.monitor_tools import _apply_optim_handler

    # pending → 拒绝执行
    pid = await conn.fetchval(
        "INSERT INTO optim_log (proposal, category, status) "
        "VALUES ('测试建议', 'context', 'pending') RETURNING id"
    )
    r = await _apply_optim_handler({"optim_id": pid})
    assert r.error is not None
    assert "仅 approved" in r.error
    # 批准后执行
    await conn.execute(
        "UPDATE optim_log SET status='approved' WHERE id=$1", pid
    )
    r2 = await _apply_optim_handler({"optim_id": pid})
    assert r2.error is None
    row = await conn.fetchrow(
        "SELECT status, result FROM optim_log WHERE id=$1", pid
    )
    assert row["status"] == "applied"
    assert row["result"] is not None


async def test_apply_optim_rejects_high_risk_category(conn):
    """高危类别(file/tool)超出 V1 白名单 → 拒绝。"""
    from private_agent.tools.builtins.monitor_tools import _apply_optim_handler

    hid = await conn.fetchval(
        "INSERT INTO optim_log (proposal, category, status) "
        "VALUES ('改文件', 'tool', 'approved') RETURNING id"
    )
    r = await _apply_optim_handler({"optim_id": hid})
    assert r.error is not None
    assert "白名单" in r.error


# ── 审批流 API ───────────────────────────────────────────────────────────


async def test_optim_log_api(conn):
    """GET /admin/optim-log + PUT 审批流转。"""
    from fastapi.testclient import TestClient

    # 注入 db 连接为测试库
    from private_agent.storage import db
    from private_agent.api import admin as admin_mod

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db, "connect", _fake_connect)

    # 插入一条 pending
    await conn.execute(
        "INSERT INTO optim_log (proposal, category) VALUES ('建议1', 'performance')"
    )
    from private_agent.main import app

    client = TestClient(app)
    # GET 列表
    resp = client.get(
        "/admin/optim-log", headers={"X-Admin-Token": "test-admin-token"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert any("建议1" in x["proposal"] for x in data)
    # PUT 审批 approved
    target = next(x for x in data if "建议1" in x["proposal"])
    put = client.put(
        f"/admin/optim-log/{target['id']}",
        headers={"X-Admin-Token": "test-admin-token"},
        json={"status": "approved"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "approved"
    # 非法状态 → 400
    bad = client.put(
        f"/admin/optim-log/{target['id']}",
        headers={"X-Admin-Token": "test-admin-token"},
        json={"status": "invalid"},
    )
    assert bad.status_code == 400
    monkeypatch.undo()


# ── 0.6.0 P3: monitor 会话装配 ───────────────────────────────────────────


async def test_monitor_session_prompt(schema):
    """kind='monitor' 会话 system_prompt 用专属监控提示词 + 指标摘要。"""
    from private_agent.main import _monitor_system_prompt

    conn = await asyncpg.connect(TEST_DSN)
    try:
        # 造 monitor 会话(注: sessions 表无 kind 列时插入用 DEFAULT 'main')
        sid = await conn.fetchval(
            "INSERT INTO sessions (title, model_id, kind) "
            "VALUES ('monitor', 'mock', 'monitor') RETURNING id"
        )
        prompt = await _monitor_system_prompt({}, sid, conn)
        # 专属提示词命中
        assert "主智能体" in prompt or "系统监控" in prompt
        # 无指标数据时摘要为空, 提示词仍完整
        assert prompt.strip() != ""
    finally:
        await conn.close()


async def test_monitor_tools_registered_only_for_monitor(conn):
    """监控工具仅在 monitor 会话注册, 通用内置 10 个不含监控工具。"""
    from private_agent.tools.builtins import (
        register_all_builtins, register_monitor_tools,
    )
    from private_agent.tools.registry import ToolRegistry

    r = ToolRegistry()
    register_all_builtins(r)
    names = {t.name for t in r.list_tools()}
    assert "system_metrics_query" not in names  # 场景会话不暴露
    assert "optim_plan" not in names

    r2 = ToolRegistry()
    register_monitor_tools(r2)
    names2 = {t.name for t in r2.list_tools()}
    assert "system_metrics_query" in names2
    assert "apply_optim" in names2
