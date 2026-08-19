"""monitor(无涯)状态感知环节 handler 层测试。

覆盖缺口(2026-08-16 盘点): 现有 test_system_metrics.py 只测了
MetricsCollector 本身(collect_once/query/latest_summary), 但两个工具
handler —— system_status(即时采集) 与 system_metrics_query(历史查询) ——
的 handler 层 0 覆盖(输出格式/空数据/collector 未初始化/参数透传)。
另补 optim_plan 未覆盖的边界: category 默认值 / plan JSON 落库 / session_id 关联。

设计要点:
- 用假 collector 替换 _collector()(不依赖 main.app.state 注入与真实 psutil),
  handler 内 db.connect 仍指向测试库, 与既有测试同构。
- 验证的是"工具边界行为"(输出/报错/透传), 而非采集器内部逻辑。
"""
from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest

from private_agent.tools.builtins import monitor_tools

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


class _FakeCollector:
    """假采集器: 记录调用参数, 返回预设数据。"""

    def __init__(self, metrics=None, rows=None):
        self.metrics = metrics or {}
        self.rows = rows or []
        self.calls: list[tuple] = []

    async def collect_once(self, conn):
        self.calls.append(("collect_once", None))
        return self.metrics

    async def query(self, conn, **kwargs):
        self.calls.append(("query", kwargs))
        return self.rows


@pytest.fixture
def _patch_db_to_test(monkeypatch):
    """handler 内 db.connect 指向测试库(默认连生产 private_agent)。"""
    from private_agent.storage import db

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


async def _setup_schema():
    """重建测试库 schema(幂等迁移, 含 system_metrics/optim_log 表)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


# ── system_status(即时采集) ─────────────────────────────────────────────


def test_system_status_returns_collected_metrics(_patch_db_to_test, monkeypatch):
    """即时采集: 输出各指标名与数值(按名排序)。"""
    asyncio.run(_setup_schema())
    fake = _FakeCollector(metrics={
        "cpu_percent": 39.0, "ram_percent": 95.0, "ws_conns": 1.0,
    })
    monkeypatch.setattr(monitor_tools, "_collector", lambda: fake)

    result = asyncio.run(monitor_tools._system_status_handler({}))
    assert result.error in (None, ""), result.error
    out = result.output or ""
    assert out.startswith("system_status @ ")
    assert "- cpu_percent: 39.00" in out
    assert "- ram_percent: 95.00" in out
    assert "- ws_conns: 1.00" in out


def test_system_status_collector_missing(_patch_db_to_test, monkeypatch):
    """采集器未注入(非 monitor 运行时): 应报错, 不崩溃。"""
    monkeypatch.setattr(monitor_tools, "_collector", lambda: None)

    result = asyncio.run(monitor_tools._system_status_handler({}))
    assert result.error is not None
    assert "collector" in result.error


# ── system_metrics_query(历史查询) ──────────────────────────────────────


def _mk_row(name, value, ts="2026-08-16T10:00:00+00:00", sid=None):
    from datetime import datetime, timezone

    return {
        "name": name,
        "value": value,
        "ts": datetime.fromisoformat(ts),
        "session_id": sid,
    }


def test_system_metrics_query_summary_output(_patch_db_to_test, monkeypatch):
    """历史查询: 摘要输出(共 N 条 + 各指标最近值)。"""
    asyncio.run(_setup_schema())
    fake = _FakeCollector(rows=[
        _mk_row("cpu_percent", 39.0),
        _mk_row("ram_percent", 95.0),
        _mk_row("ws_conns", 1.0),
    ])
    monkeypatch.setattr(monitor_tools, "_collector", lambda: fake)

    result = asyncio.run(monitor_tools._system_metrics_query_handler({}))
    assert result.error in (None, ""), result.error
    out = result.output or ""
    assert "共 3 条" in out
    assert "- cpu_percent: 39.00" in out
    assert "- ram_percent: 95.00" in out
    assert "- ws_conns: 1.00" in out


def test_system_metrics_query_empty(_patch_db_to_test, monkeypatch):
    """查询范围内无数据: 返回占位提示, 不报错。"""
    asyncio.run(_setup_schema())
    fake = _FakeCollector(rows=[])
    monkeypatch.setattr(monitor_tools, "_collector", lambda: fake)

    result = asyncio.run(monitor_tools._system_metrics_query_handler({}))
    assert result.error in (None, ""), result.error
    assert "无指标数据" in (result.output or "")


def test_system_metrics_query_passes_filters(_patch_db_to_test, monkeypatch):
    """names/since_hours/session_id/limit 透传给 collector.query。"""
    asyncio.run(_setup_schema())
    fake = _FakeCollector(rows=[_mk_row("cpu_percent", 39.0)])
    monkeypatch.setattr(monitor_tools, "_collector", lambda: fake)

    asyncio.run(monitor_tools._system_metrics_query_handler({
        "names": ["cpu_percent"],
        "since_hours": 1,
        "session_id": 7,
        "limit": 5,
    }))
    assert fake.calls, "应调用 collector.query"
    _, kwargs = fake.calls[0]
    assert kwargs["names"] == ["cpu_percent"]
    assert kwargs["since_hours"] == 1.0
    assert kwargs["session_id"] == 7
    assert kwargs["limit"] == 5


def test_system_metrics_query_defaults(_patch_db_to_test, monkeypatch):
    """默认参数: since_hours=24, limit=200, names=None。"""
    asyncio.run(_setup_schema())
    fake = _FakeCollector(rows=[])
    monkeypatch.setattr(monitor_tools, "_collector", lambda: fake)

    asyncio.run(monitor_tools._system_metrics_query_handler({}))
    _, kwargs = fake.calls[0]
    assert kwargs["names"] is None
    assert kwargs["since_hours"] == 24.0
    assert kwargs["limit"] == 200
    assert kwargs["session_id"] is None


def test_system_metrics_query_collector_missing(_patch_db_to_test, monkeypatch):
    """采集器未注入: 报错不崩溃。"""
    monkeypatch.setattr(monitor_tools, "_collector", lambda: None)

    result = asyncio.run(monitor_tools._system_metrics_query_handler({}))
    assert result.error is not None
    assert "collector" in result.error


# ── optim_plan 边界(补 test_system_metrics::test_optim_plan_tool 缺口) ──


def test_optim_plan_default_category_and_plan_json(_patch_db_to_test):
    """不传 category → 默认 performance; plan 以 JSON 落库; session_id 关联。"""
    asyncio.run(_setup_schema())

    class _Ctx:
        session_id = 42

    async def _run():
        result = await monitor_tools._optim_plan_handler(
            {
                "proposal": "测试: 压缩上下文阈值",
                "plan": [{"tool": "code_execution", "args": {"code": "print(1)"}}],
            },
            ctx=_Ctx(),
        )
        assert result.error in (None, ""), result.error
        assert "已提交" in (result.output or "")

        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT category, plan_json, session_id, status "
                "FROM optim_log ORDER BY id DESC LIMIT 1"
            )
            assert row["category"] == "performance"  # 默认值
            assert row["session_id"] == 42  # 上下文会话关联
            assert row["status"] == "pending"
            # asyncpg JSONB 返回 str, 解析后与入参一致
            assert json.loads(row["plan_json"]) == [
                {"tool": "code_execution", "args": {"code": "print(1)"}}
            ]
        finally:
            await conn.close()

    asyncio.run(_run())


def test_optim_plan_without_ctx(_patch_db_to_test):
    """无 ctx(非会话内调用): session_id 落 NULL, 仍可提交。"""
    asyncio.run(_setup_schema())

    async def _run():
        result = await monitor_tools._optim_plan_handler({"proposal": "无会话建议"})
        assert result.error in (None, ""), result.error

        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT session_id FROM optim_log ORDER BY id DESC LIMIT 1"
            )
            assert row["session_id"] is None
        finally:
            await conn.close()

    asyncio.run(_run())
