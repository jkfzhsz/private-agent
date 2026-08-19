"""search_lessons 工具测试 - 经验检索。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 1.4
遵循项目工具模式: handler 自建 DB 连接(memory_search 模式), 无 ctx 依赖。
测试通过 PA_DB_* 环境变量将 handler 的连接指向测试库(与 TEST_DSN 一致)。
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from private_agent.skills.evolution_repo import SkillLesson, EvolutionRepo
from private_agent.storage import migrations
from private_agent.tools.builtins.search_lessons import (
    SEARCH_LESSONS_TOOL,
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


async def _seed_lesson(conn: "asyncpg.Connection") -> None:
    """插入一条经验, 供检索。"""
    repo = EvolutionRepo(conn)
    await repo.add(SkillLesson(
        scope="office",
        lesson_category="domain_skill",
        task_summary="用 pandas 清洗销售数据",
        lesson_type="success",
        lesson_content="先检查 dtype，日期列用 pd.to_datetime(errors='coerce')",
        tool_chain=["file_read", "code_execution"],
        importance=0.8,
    ))


def test_search_lessons_tool_def():
    """工具定义: 名称 + keyword/scope 参数。"""
    assert SEARCH_LESSONS_TOOL.name == "search_lessons"
    props = SEARCH_LESSONS_TOOL.parameters_schema["properties"]
    assert "keyword" in props
    assert "scope" in props
    assert SEARCH_LESSONS_TOOL.safety_level == "readonly"


def test_search_lessons_returns_results():
    """检索命中: 返回格式化经验列表。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_lesson(conn)
            result = await _search_lessons_handler(
                {"keyword": "pandas", "scope": "office"}
            )
            return result.output
        finally:
            await conn.close()

    output = asyncio.run(_run())
    assert "pandas" in output
    assert "先检查 dtype" in output


def test_search_lessons_no_results():
    """检索无命中: 返回"无相关经验"。"""
    _setup_schema()

    async def _run() -> str:
        result = await _search_lessons_handler(
            {"keyword": "nonexistent-keyword", "scope": "office"}
        )
        return result.output

    output = asyncio.run(_run())
    assert "无相关经验" in output
