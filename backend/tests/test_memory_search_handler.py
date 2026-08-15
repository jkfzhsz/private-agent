"""memory_search 工具测试 - 检索匹配。

2026-08-13 新增: 复现"记忆能写不能读"故障(清和 3 次多词查询全部
"No memories found", 根因是整串子串匹配)并验证修复(空格分词 OR 匹配 +
候选集扩大)。遵循项目工具模式: handler 自建 DB 连接(memory_search 模式),
测试通过 PA_DB_* 环境变量将 handler 的连接指向测试库。
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from private_agent.memory.memories_repo import Memory, MemoriesRepo
from private_agent.storage import migrations
from private_agent.tools.builtins.memory_search import (
    MEMORY_SEARCH_TOOL,
    _memory_search_handler,
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


# 2026-08-13 故障复现用: 与 user_memories id=5(清和写入的健康知识)同构
_HEALTH_CONTENT = (
    "【健康生活要点·清和整理】膳食:每餐1/2蔬果+1/4全谷+1/4优质蛋白,"
    "控盐<5g/日、限糖限酒(男<25g/日女<15g/日),足量饮水;"
    "运动:每周≥150分钟中等强度有氧+≥2次抗阻训练、减少久坐;"
    "睡眠:7-9小时、规律节律、避免熬夜。"
    "数据源自个人健康知识库(2026-08-13 PG写入功能测试)。"
    "建议咨询专业医师,此为健康教育常识不构成诊疗意见。"
)


async def _seed_health_memory(conn: "asyncpg.Connection") -> None:
    """插入健康记忆(scope=frontend_design, 与故障现场同构)。"""
    repo = MemoriesRepo(conn)
    await repo.insert(
        Memory(
            type="fact",
            content=_HEALTH_CONTENT,
            importance=0.6,
            scope="frontend_design",
        )
    )


def test_memory_search_tool_def():
    """工具定义: 名称 + query/scope/top_k 参数。"""
    assert MEMORY_SEARCH_TOOL.name == "memory_search"
    props = MEMORY_SEARCH_TOOL.parameters_schema["properties"]
    assert "query" in props
    assert "scope" in props
    assert "top_k" in props
    assert MEMORY_SEARCH_TOOL.safety_level == "none"


def test_memory_search_multiword_query_hits():
    """修复回归: 带空格多词 query 命中(故障现场: 此前必然 0 命中)。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_health_memory(conn)
            result = await _memory_search_handler(
                {
                    "query": "健康生活要点 餐盘法 睡眠 运动 PG写入测试",
                    "scope": "frontend_design",
                }
            )
            return result.output
        finally:
            await conn.close()

    output = asyncio.run(_run())
    assert "Found 1 active memory" in output
    assert "健康生活要点" in output


def test_memory_search_or_semantics():
    """OR 语义: 多词 query 中任一词命中即返回(原实现整串匹配必失败)。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_health_memory(conn)
            # "餐盘法" 不在 content 中, 但 "睡眠" 在 → OR 语义应命中
            result = await _memory_search_handler(
                {"query": "餐盘法 睡眠", "scope": "frontend_design"}
            )
            return result.output
        finally:
            await conn.close()

    output = asyncio.run(_run())
    assert "Found 1 active memory" in output
    assert "睡眠:7-9小时" in output


def test_memory_search_no_word_overlap():
    """词面完全不重叠: 诚实返回 No memories found(非 bug, 无语义检索的固有限制)。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_health_memory(conn)
            # 故障第 3 次查询词: 四大支柱/餐盘法/蔬果全谷/运动睡眠
            # 在 content 中确实都不存在 → OR 匹配也 0 命中, 属预期行为
            result = await _memory_search_handler(
                {
                    "query": "健康生活四大支柱 餐盘法 蔬果全谷 运动睡眠",
                    "scope": "frontend_design",
                }
            )
            return result.output
        finally:
            await conn.close()

    output = asyncio.run(_run())
    assert "No memories found" in output


def test_memory_search_single_word_query():
    """单词 query(无空格): 整串匹配行为保持(向后兼容)。"""
    _setup_schema()

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_health_memory(conn)
            result = await _memory_search_handler(
                {"query": "每周≥150分钟", "scope": "frontend_design"}
            )
            return result.output
        finally:
            await conn.close()

    output = asyncio.run(_run())
    assert "Found 1 active memory" in output


def test_memory_search_no_results():
    """无命中: 返回 No memories found。"""
    _setup_schema()

    async def _run() -> str:
        result = await _memory_search_handler(
            {"query": "nonexistent-keyword-xyz", "scope": "frontend_design"}
        )
        return result.output

    output = asyncio.run(_run())
    assert "No memories found" in output


def test_memory_search_requires_query():
    """缺 query: 返回错误。"""
    _setup_schema()

    async def _run() -> str:
        result = await _memory_search_handler({})
        return result.error or ""

    output = asyncio.run(_run())
    assert "No query provided" in output
