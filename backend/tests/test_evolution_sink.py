"""阶段5(agent-upgrader §2.2 能力域⑤ 进化沉淀)测试。

覆盖:
- lessons_add: 主动经验沉淀(skill_lessons 落库 + scope/type/category 校验)
- search_lessons 增强: 仅 scope 列出 + 无参数报错
- eval_report: 评估报告(最近运行/低分样本/待审核队列)

遵循项目工具模式: handler 自建 DB 连接, 测试通过 PA_DB_* 指向测试库。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from private_agent.skills.evolution_repo import EvolutionRepo
from private_agent.storage import migrations
from private_agent.tools.builtins.eval_runner import (
    EVAL_REPORT_TOOL,
    _eval_report_handler,
)
from private_agent.tools.builtins.evolution_tools import (
    LESSONS_ADD_DEF,
    _lessons_add_handler,
)
from private_agent.tools.builtins.search_lessons import (
    _search_lessons_handler,
)

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

# 让 handler 自建连接(db.connect)指向测试库
os.environ["PA_DB_HOST"] = "localhost"
os.environ["PA_DB_PORT"] = "5432"
os.environ["PA_DB_NAME"] = "private_agent_test"
os.environ["PA_DB_USER"] = "postgres"
os.environ["PA_DB_PASSWORD"] = "123123"


def _setup_schema() -> None:
    """完整重建测试库(schema.sql + 增量迁移)—— 避免并发残留的"无 schema"态。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            sql = (
                Path(__file__).resolve().parents[1]
                / "private_agent/storage/schema.sql"
            ).read_text(encoding="utf-8")
            await conn.execute(sql)
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture(autouse=True)
def _patch_db_to_test(tmp_path, monkeypatch):
    """PA_USER_DATA 指向临时目录(避免污染真实用户配置)。"""
    monkeypatch.setenv("PA_USER_DATA", str(tmp_path))


# ──────────────────────────────────────────────────────────────────────────────
# lessons_add
# ──────────────────────────────────────────────────────────────────────────────


def test_lessons_add_ok():
    """合法沉淀 → 落库成功, 可检索到。"""
    _setup_schema()

    async def _run() -> str:
        r = await _lessons_add_handler({
            "scope": "monitor",
            "lesson_type": "failure",
            "lesson_content": "全量回归期间勿并发跑同库 pytest",
            "task_summary": "并发互踩教训",
            "importance": 0.9,
        })
        return r.error or r.output

    out = asyncio.run(_run())
    assert "经验已沉淀" in out
    assert "monitor" in out
    assert "双写" in out  # 引导 mempalace 双写


def test_lessons_add_default_category():
    """scope=office 缺省 lesson_category → domain_skill。"""
    _setup_schema()

    async def _run() -> str:
        r = await _lessons_add_handler({
            "scope": "office",
            "lesson_type": "success",
            "lesson_content": "报销单模板用 xxx 格式",
        })
        return r.error or r.output

    out = asyncio.run(_run())
    assert "经验已沉淀" in out


def test_lessons_add_invalid_scope():
    """非法 scope → 明确报错。"""
    _setup_schema()

    async def _run() -> str:
        r = await _lessons_add_handler({
            "scope": "bogus", "lesson_type": "failure",
            "lesson_content": "x",
        })
        return r.error or ""

    err = asyncio.run(_run())
    assert "scope 非法" in err


def test_lessons_add_invalid_type():
    """非法 lesson_type → 明确报错。"""
    _setup_schema()

    async def _run() -> str:
        r = await _lessons_add_handler({
            "scope": "monitor", "lesson_type": "mystery",
            "lesson_content": "x",
        })
        return r.error or ""

    err = asyncio.run(_run())
    assert "lesson_type 非法" in err


def test_lessons_add_category_mismatch():
    """scope=monitor 但 category=domain_skill → 校验失败。"""
    _setup_schema()

    async def _run() -> str:
        r = await _lessons_add_handler({
            "scope": "monitor", "lesson_type": "failure",
            "lesson_category": "domain_skill", "lesson_content": "x",
        })
        return r.error or ""

    err = asyncio.run(_run())
    assert "project_evolution" in err


def test_lessons_add_missing_content():
    """缺 lesson_content → 必填报错。"""
    _setup_schema()

    async def _run() -> str:
        r = await _lessons_add_handler({
            "scope": "monitor", "lesson_type": "failure",
        })
        return r.error or ""

    err = asyncio.run(_run())
    assert "必填" in err


# ──────────────────────────────────────────────────────────────────────────────
# search_lessons 增强(阶段5: 仅 scope 列出)
# ──────────────────────────────────────────────────────────────────────────────


def test_search_lessons_scope_only():
    """仅 scope → 列出该场景经验(阶段5 增强, 原 keyword 必填)。"""
    _setup_schema()

    async def _run() -> str:
        await _lessons_add_handler({
            "scope": "monitor", "lesson_type": "failure",
            "lesson_content": "测试库并发互踩",
        })
        r = await _search_lessons_handler({"scope": "monitor"})
        return r.error or r.output

    out = asyncio.run(_run())
    assert "测试库并发互踩" in out


def test_search_lessons_no_args():
    """无 keyword 无 scope → 报错。"""
    _setup_schema()

    async def _run() -> str:
        r = await _search_lessons_handler({})
        return r.error or ""

    err = asyncio.run(_run())
    assert "至少提供一个" in err


# ──────────────────────────────────────────────────────────────────────────────
# eval_report
# ──────────────────────────────────────────────────────────────────────────────


def test_eval_report_empty():
    """无评估运行 → 报告提示可先跑 mock 评测。"""
    _setup_schema()

    async def _run() -> str:
        r = await _eval_report_handler({"limit": 3, "threshold": 0.6})
        return r.error or r.output

    out = asyncio.run(_run())
    assert "暂无已完成评估运行" in out
    assert "无低于 60% 完成率的低分样本" in out


def test_eval_report_with_low_score_run():
    """有评估运行 + 低分样本 → 报告含运行与低分。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                """
                INSERT INTO eval_runs
                    (skill_name, skill_version, model_id, dataset_version,
                     eval_mode, mock_enabled, sample_results, finished_at)
                VALUES ($1, $2, $3, $4, 'offline', TRUE,
                        $5::jsonb, now())
                """,
                "monitor", "0.5.3", "deepseek-flash", "2026-08-16",
                '[{"sample_id": "monitor_01", '
                '"metrics": {"task_completion": {"completion_rate": 0.3}}}]',
            )
        finally:
            await conn.close()
        r = await _eval_report_handler({"limit": 3, "threshold": 0.6})
        return r.error or r.output

    out = asyncio.run(_run())
    assert "monitor" in out
    assert "低分样本" in out
    assert "monitor_01" in out
    assert "修复闭环" in out  # 引导自动修复


def test_lessons_add_tool_def():
    """lessons_add ToolDef: 权限与 schema 正确。"""
    assert LESSONS_ADD_DEF.name == "lessons_add"
    assert LESSONS_ADD_DEF.safety_level in ("full", "elevated")
    assert LESSONS_ADD_DEF.risk_level == "low"
    assert "lesson_content" in LESSONS_ADD_DEF.parameters_schema["required"]


def test_eval_report_tool_def():
    """eval_report ToolDef: 只读 + 参数正确。"""
    assert EVAL_REPORT_TOOL.name == "eval_report"
    assert EVAL_REPORT_TOOL.safety_level == "readonly"
