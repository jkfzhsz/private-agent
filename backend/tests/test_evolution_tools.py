"""Phase 4 Task 4.1 - 进化调度工具测试(lessons_stats + review_queue_summary)。

遵循项目工具模式: handler 自建 DB/文件连接(search_lessons/monitor_tools 模式),
无 ctx 依赖。测试通过 PA_DB_* 环境变量将 handler 的连接指向测试库。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import asyncpg
import pytest

from private_agent.eval.repos import ReviewQueueRepo
from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson
from private_agent.storage import migrations
from private_agent.tools.builtins.evolution_tools import (
    LESSONS_STATS_DEF,
    REVIEW_QUEUE_SUMMARY_DEF,
    _lessons_stats_handler,
    _review_queue_summary_handler,
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


async def _seed_lessons(conn: "asyncpg.Connection") -> None:
    """插入多条经验, 覆盖不同 scope 与 lesson_type。"""
    repo = EvolutionRepo(conn)
    await repo.add(SkillLesson(
        scope="office",
        lesson_category="domain_skill",
        task_summary="用 pandas 清洗销售数据",
        lesson_type="success",
        lesson_content="先检查 dtype",
        tool_chain=["code_execution"],
        importance=0.8,
    ))
    await repo.add(SkillLesson(
        scope="office",
        lesson_category="domain_skill",
        task_summary="pandas groupby 报错",
        lesson_type="failure",
        lesson_content="分组前需 dropna",
        tool_chain=["code_execution"],
        importance=0.6,
    ))
    await repo.add(SkillLesson(
        scope="data_analysis",
        lesson_category="domain_skill",
        task_summary="DCF 估值",
        lesson_type="success",
        lesson_content="折现率取 WACC",
        tool_chain=[],
        importance=0.9,
    ))


# ── lessons_stats ──

def test_lessons_stats_tool_def():
    """工具定义: 名称 + 只读安全等级 + 无参数。"""
    assert LESSONS_STATS_DEF.name == "lessons_stats"
    assert LESSONS_STATS_DEF.safety_level == "readonly"
    props = LESSONS_STATS_DEF.parameters_schema["properties"]
    assert props == {}


def test_lessons_stats_returns_per_scope_counts():
    """返回各场景经验数 + 类型分布。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_lessons(conn)
            result = await _lessons_stats_handler({})
            return result.output
        finally:
            await conn.close()

    output = asyncio.run(_run())
    assert "office" in output
    assert "data_analysis" in output
    assert "frontend_design" in output
    assert "monitor" in output
    # office 有 2 条(1 success + 1 failure)
    assert "2" in output
    assert "success" in output or "成功" in output
    assert "failure" in output or "失败" in output


def test_lessons_stats_empty_repo():
    """空经验库: 各场景均显示 0 条。"""
    _setup_schema()

    async def _run() -> str:
        result = await _lessons_stats_handler({})
        return result.output

    output = asyncio.run(_run())
    assert "office" in output
    assert "0" in output


# ── review_queue_summary ──

def test_review_queue_summary_tool_def():
    """工具定义: 名称 + 只读安全等级。"""
    assert REVIEW_QUEUE_SUMMARY_DEF.name == "review_queue_summary"
    assert REVIEW_QUEUE_SUMMARY_DEF.safety_level == "readonly"


def test_review_queue_summary_returns_pending_items():
    """返回待审核失败案例摘要。"""
    _setup_schema()

    async def _run() -> str:
        # 用临时 JSON 文件模拟审核队列
        queue_file = tempfile.mktemp(suffix=".json")
        try:
            repo = ReviewQueueRepo(queue_file=queue_file)
            await repo.add({
                "source_session_id": 1,
                "scope": "office",
                "failure_reason": "[tool_error] file_read 超时",
                "failure_type": "tool_error",
            })
            await repo.add({
                "source_session_id": 2,
                "scope": "frontend_design",
                "failure_reason": "[iteration_exhausted] 步数超限",
                "failure_type": "iteration_exhausted",
            })
            # monkeypatch: 让 handler 用我们的 queue_file
            import private_agent.tools.builtins.evolution_tools as mod
            orig_build = mod._build_review_queue_repo
            mod._build_review_queue_repo = lambda cfg: ReviewQueueRepo(queue_file=queue_file)
            try:
                result = await _review_queue_summary_handler({})
                return result.output
            finally:
                mod._build_review_queue_repo = orig_build
        finally:
            if os.path.exists(queue_file):
                os.unlink(queue_file)

    output = asyncio.run(_run())
    assert "2" in output  # 2 条待审核
    assert "tool_error" in output
    assert "office" in output


def test_review_queue_summary_empty():
    """空审核队列: 返回"无待处理"。"""
    _setup_schema()

    async def _run() -> str:
        queue_file = tempfile.mktemp(suffix=".json")
        try:
            import private_agent.tools.builtins.evolution_tools as mod
            orig_build = mod._build_review_queue_repo
            mod._build_review_queue_repo = lambda cfg: ReviewQueueRepo(queue_file=queue_file)
            try:
                result = await _review_queue_summary_handler({})
                return result.output
            finally:
                mod._build_review_queue_repo = orig_build
        finally:
            if os.path.exists(queue_file):
                os.unlink(queue_file)

    output = asyncio.run(_run())
    assert "无" in output or "空" in output or "0" in output
