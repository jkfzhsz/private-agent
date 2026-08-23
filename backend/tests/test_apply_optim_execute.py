"""阶段1-b(agent-upgrader 设计文档 §3): apply_optim 真执行。

覆盖:
- approved + plan → 按 plan 真执行白名单动作(code_execution/file_write)
- 非白名单工具跳过并记录
- 非 approved 状态拒绝
- 无 plan 时回退低风险 category 路径

2026-08-16 补覆盖(monitor 自测第二轮): 白名单其余路径与异常分支 ——
- file_read 只读执行
- code_execution 执行 + ctx.session_id 自动注入
- 非 dict 步骤跳过
- 步骤失败/异常记录(状态仍 applied)
- 缺 id / id 不存在报错
"""
import asyncio
import json

import asyncpg
import pytest

from private_agent.tools.builtins.monitor_tools import _apply_optim_handler

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture(autouse=True)
def _patch_db_to_test(monkeypatch):
    """工具 handler 内 db.connect 指向测试库。"""
    from private_agent.storage import db

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


async def _seed_optim(conn, status="approved", plan=None, category="code"):
    """插入一条 optim_log 并返回 id。"""
    plan_json = json.dumps(plan or [], ensure_ascii=False)
    return await conn.fetchval(
        "INSERT INTO optim_log (proposal, category, plan_json, status) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        f"test optim #{status}", category, plan_json, status,
    )


async def _run_schema():
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


def test_apply_optim_executes_plan_file_write(monkeypatch):
    """approved + plan(file_write) → 真执行 file_write_handler。"""
    asyncio.run(_run_schema())

    captured: dict = {}

    async def _fake_fw(args):
        captured["args"] = dict(args)
        from private_agent.tools.defs import ToolResult
        return ToolResult(output=f"written:{args.get('path')}")

    monkeypatch.setattr(
        "private_agent.tools.builtins.file_write.file_write_handler",
        _fake_fw,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[{"tool": "file_write",
                             "args": {"path": "/tmp/x.txt", "content": "hi"}}]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            # 状态应为 applied
            row = await conn.fetchrow(
                "SELECT status, result FROM optim_log WHERE id = $1", oid
            )
            return result, row
        finally:
            await conn.close()

    result, row = asyncio.run(_run())
    assert result.error is None, result.error
    assert "written:" in result.output
    assert "step1[file_write] OK" in result.output
    assert row["status"] == "applied"
    assert captured["args"]["path"] == "/tmp/x.txt"


def test_apply_optim_skips_non_whitelist_tool(monkeypatch):
    """plan 含非白名单工具 → 跳过并记录, 白名单步骤仍执行。"""
    asyncio.run(_run_schema())

    async def _fake_fw(args):
        from private_agent.tools.defs import ToolResult
        return ToolResult(output="ok")

    monkeypatch.setattr(
        "private_agent.tools.builtins.file_write.file_write_handler",
        _fake_fw,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[
                    {"tool": "dangerous_tool", "args": {}},
                    {"tool": "file_write",
                     "args": {"path": "/tmp/y.txt", "content": "x"}},
                ]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            return result
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result.error is None, result.error
    assert "1 步" in result.output  # 仅 file_write 执行
    assert "跳过 1 步" in result.output
    assert "不在白名单" in result.output


def test_apply_optim_rejects_non_approved(monkeypatch):
    """pending/rejected/applied → 拒绝执行。"""
    asyncio.run(_run_schema())

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            for status in ("pending", "rejected", "applied"):
                oid = await _seed_optim(conn, status=status)
                result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
                assert result.error is not None, f"{status} 应被拒绝"
                assert "仅 approved 可执行" in result.error
        finally:
            await conn.close()

    asyncio.run(_run())


def test_apply_optim_fallback_no_plan(monkeypatch):
    """无 plan + 低风险 category → 回退记录执行摘要(保留旧行为)。"""
    asyncio.run(_run_schema())

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(conn, plan=[], category="context.compression")
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            row = await conn.fetchrow(
                "SELECT status, result FROM optim_log WHERE id = $1", oid
            )
            return result, row
        finally:
            await conn.close()

    result, row = asyncio.run(_run())
    assert result.error is None, result.error
    assert "无 plan" in result.output
    assert row["status"] == "applied"


def test_list_optim_log_parses_plan_json(monkeypatch):
    """list_optim_log 对 asyncpg JSONB(str) 的 plan_json 解析为 list。"""
    import asyncio
    asyncio.run(_run_schema())

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            plan = [{"tool": "code_execution", "args": {"code": "print(1)"}}]
            oid = await _seed_optim(conn, plan=plan)
            # 直接调 admin 的解析辅助(与 API 同源)
            from private_agent.api.admin import _parse_plan_json
            raw = await conn.fetchval(
                "SELECT plan_json FROM optim_log WHERE id = $1", oid
            )
            assert isinstance(raw, str), "asyncpg JSONB 应为 str"
            parsed = _parse_plan_json(raw)
            assert parsed == plan
            # 空/非法输入兜底
            assert _parse_plan_json(None) == []
            assert _parse_plan_json("not-json") == []
            assert _parse_plan_json([{"x": 1}]) == [{"x": 1}]
        finally:
            await conn.close()

    asyncio.run(_run())


# ── 2026-08-16 补覆盖: 白名单其余路径(file_read/code_execution)与异常分支 ──


def test_apply_optim_executes_plan_file_read(monkeypatch):
    """approved + plan(file_read) → 真执行 file_read_handler(只读白名单)。"""
    asyncio.run(_run_schema())

    captured: dict = {}

    async def _fake_fr(args):
        captured["args"] = dict(args)
        from private_agent.tools.defs import ToolResult
        return ToolResult(output="read:/tmp/z.txt")

    monkeypatch.setattr(
        "private_agent.tools.builtins.file_read.file_read_handler",
        _fake_fr,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[{"tool": "file_read",
                             "args": {"path": "/tmp/z.txt"}}]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            row = await conn.fetchrow(
                "SELECT status FROM optim_log WHERE id = $1", oid
            )
            return result, row
        finally:
            await conn.close()

    result, row = asyncio.run(_run())
    assert result.error is None, result.error
    assert "step1[file_read] OK" in result.output
    assert row["status"] == "applied"
    assert captured["args"]["path"] == "/tmp/z.txt"


def test_apply_optim_code_execution_injects_session_id(monkeypatch):
    """code_execution 步骤: 自动注入 ctx.session_id(沙箱会话隔离)。"""
    asyncio.run(_run_schema())

    captured: dict = {}

    async def _fake_ce(args):
        captured["args"] = dict(args)
        from private_agent.tools.defs import ToolResult
        return ToolResult(output="ran")

    monkeypatch.setattr(
        "private_agent.tools.builtins.code_execution.code_execution_handler",
        _fake_ce,
    )

    class _Ctx:
        session_id = 7

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[{"tool": "code_execution",
                             "args": {"code": "print(1)"}}]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=_Ctx())
            return result
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result.error is None, result.error
    assert "step1[code_execution] OK" in result.output
    # 未显式传 session_id → handler 注入 ctx 的 session_id
    assert captured["args"]["session_id"] == "7"
    assert captured["args"]["code"] == "print(1)"


def test_apply_optim_skips_invalid_step(monkeypatch):
    """plan 含非 dict 步骤 → 跳过并记录, 合法步骤仍执行。"""
    asyncio.run(_run_schema())

    async def _fake_fw(args):
        from private_agent.tools.defs import ToolResult
        return ToolResult(output="ok")

    monkeypatch.setattr(
        "private_agent.tools.builtins.file_write.file_write_handler",
        _fake_fw,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[
                    42,  # 非 dict
                    "str-step",  # 非 dict
                    {"tool": "file_write",
                     "args": {"path": "/tmp/a.txt", "content": "1"}},
                ]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            return result
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result.error is None, result.error
    assert "1 步" in result.output
    assert "跳过 2 步" in result.output
    assert "非法步骤" in result.output


def test_apply_optim_records_step_failure(monkeypatch):
    """步骤返回 error → 记录失败, 状态仍 applied(执行摘要含失败原因)。"""
    asyncio.run(_run_schema())

    async def _fake_fw(args):
        from private_agent.tools.defs import ToolResult
        return ToolResult(output="", error="disk full")

    monkeypatch.setattr(
        "private_agent.tools.builtins.file_write.file_write_handler",
        _fake_fw,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[{"tool": "file_write",
                             "args": {"path": "/tmp/b.txt"}}]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            row = await conn.fetchrow(
                "SELECT status, result FROM optim_log WHERE id = $1", oid
            )
            return result, row
        finally:
            await conn.close()

    result, row = asyncio.run(_run())
    assert result.error is None, result.error
    assert "0 步" in result.output  # 无成功步骤
    assert "失败: disk full" in result.output
    assert row["status"] == "applied"  # 执行已落库, 状态照常流转


def test_apply_optim_records_step_exception(monkeypatch):
    """步骤抛异常 → 记录异常, 不中断整体执行。"""
    asyncio.run(_run_schema())

    async def _boom(args):
        raise RuntimeError("handler crashed")

    monkeypatch.setattr(
        "private_agent.tools.builtins.file_write.file_write_handler",
        _boom,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            oid = await _seed_optim(
                conn, plan=[{"tool": "file_write",
                             "args": {"path": "/tmp/c.txt"}}]
            )
            result = await _apply_optim_handler({"optim_id": oid}, ctx=None)
            return result
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result.error is None, result.error
    assert "执行异常" in result.output
    assert "RuntimeError" in result.output


def test_apply_optim_missing_or_unknown_id(monkeypatch):
    """缺 optim_id / id 不存在 → 明确报错。"""
    asyncio.run(_run_schema())

    async def _run():
        # 缺 id
        r1 = await _apply_optim_handler({}, ctx=None)
        assert r1.error is not None
        assert "optim_id required" in r1.error
        # id 不存在
        r2 = await _apply_optim_handler({"optim_id": 999999}, ctx=None)
        assert r2.error is not None
        assert "不存在" in r2.error

    asyncio.run(_run())
