"""skill_lessons 表与 EvolutionRepo 的测试。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 1.1
- EvolutionRepo: add / get / search_by_scope / search_by_keyword / discard / merge
- 双轨约束: scope 与 lesson_category 一致性（应用层校验 + DB CHECK）

依赖: 真实 PostgreSQL(TEST_DSN)
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from private_agent.skills.evolution_repo import SkillLesson, EvolutionRepo
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


async def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
async def conn():
    await _setup_schema()
    c = await asyncpg.connect(TEST_DSN)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
def repo(conn: "asyncpg.Connection") -> EvolutionRepo:
    return EvolutionRepo(conn)


@pytest.fixture
def lesson_data() -> dict:
    return {
        "scope": "office",
        "task_summary": "用 pandas 清洗销售数据并生成月度汇总表",
        "lesson_type": "success",  # success / failure / correction
        "lesson_content": "清洗前先检查 dtype，日期列用 pd.to_datetime(errors='coerce')",
        "tool_chain": ["file_read", "code_execution", "file_write"],
        "source_session_id": 42,
        "source_turn": 5,
    }


async def test_add_and_get_lesson(repo: EvolutionRepo, lesson_data: dict):
    lesson_id = await repo.add(SkillLesson(**lesson_data))
    assert lesson_id > 0

    retrieved = await repo.get(lesson_id)
    assert retrieved is not None
    assert retrieved.scope == "office"
    assert retrieved.lesson_type == "success"
    assert "pd.to_datetime" in retrieved.lesson_content


async def test_search_lessons_by_scope(repo: EvolutionRepo, lesson_data: dict):
    await repo.add(SkillLesson(**lesson_data))
    await repo.add(SkillLesson(**{**lesson_data, "scope": "data_analysis"}))

    results = await repo.search_by_scope("office", limit=10)
    assert len(results) == 1
    assert results[0].scope == "office"


async def test_search_lessons_by_keyword(repo: EvolutionRepo, lesson_data: dict):
    await repo.add(SkillLesson(**lesson_data))

    results = await repo.search_by_keyword("pandas", scope="office", limit=10)
    assert len(results) == 1
    assert "pandas" in results[0].task_summary


async def test_discard_lesson(repo: EvolutionRepo, lesson_data: dict):
    lesson_id = await repo.add(SkillLesson(**lesson_data))

    await repo.discard(lesson_id)
    retrieved = await repo.get(lesson_id)
    assert retrieved is not None
    assert retrieved.is_active is False


async def test_merge_lessons(repo: EvolutionRepo, lesson_data: dict):
    a_id = await repo.add(SkillLesson(**lesson_data))
    b_id = await repo.add(SkillLesson(**{**lesson_data, "task_summary": "另一条经验"}))

    merged_content = "合并后的经验内容"
    await repo.merge(source_id=b_id, target_id=a_id, merged_content=merged_content)

    merged = await repo.get(a_id)
    assert merged is not None
    assert merged.lesson_content == merged_content
    discarded = await repo.get(b_id)
    assert discarded is not None
    assert discarded.is_active is False


async def test_add_rejects_scope_category_mismatch(repo: EvolutionRepo, lesson_data: dict):
    """双轨约束: monitor scope 必须配 project_evolution, office 必须配 domain_skill。"""
    with pytest.raises(ValueError):
        await repo.add(SkillLesson(
            **{**lesson_data, "scope": "monitor", "lesson_category": "domain_skill"}
        ))


async def test_default_lesson_category_is_domain_skill(repo: EvolutionRepo, lesson_data: dict):
    """默认 lesson_category='domain_skill'（office 场景）。"""
    lesson_id = await repo.add(SkillLesson(**lesson_data))
    retrieved = await repo.get(lesson_id)
    assert retrieved is not None
    assert retrieved.lesson_category == "domain_skill"


async def test_monitor_lesson_category_project_evolution(repo: EvolutionRepo, lesson_data: dict):
    """无涯(monitor)经验必须为 project_evolution。"""
    lesson_id = await repo.add(SkillLesson(**{
        **lesson_data,
        "scope": "monitor",
        "lesson_category": "project_evolution",
        "task_summary": "提取重复代码为工具函数",
    }))
    retrieved = await repo.get(lesson_id)
    assert retrieved is not None
    assert retrieved.lesson_category == "project_evolution"
