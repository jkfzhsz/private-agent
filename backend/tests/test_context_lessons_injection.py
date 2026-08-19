"""经验注入到会话上下文测试（Task 2.1）。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 2.1
- 会话启动（ensure_initial）时将本场景经验注入 Stable Zone
- 双轨隔离: scope=office → 只注入 lesson_category='domain_skill' 经验
- 注入预算: 最多 max_lessons 条, 总 token ≤ max_tokens（修订 4）
"""
import asyncio
import os

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
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


async def _create_session(conn: "asyncpg.Connection") -> int:
    """插入 sessions 记录,返回 id。"""
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-session",
        "mock-glm",
    )


def test_lessons_injected_into_stable_zone():
    """会话启动后本场景经验应出现在 Stable Zone 并经 get_messages 暴露。"""
    _setup_schema()

    async def _run() -> tuple[list[dict], ContextManager]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            repo = EvolutionRepo(conn)
            await repo.add(SkillLesson(
                scope="office",
                task_summary="清洗销售数据",
                lesson_type="success",
                lesson_content="先检查 dtype 再清洗",
                tool_chain=["code_execution"],
                importance=0.8,
            ))
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
                scene="office",
                evolution_repo=repo,
            )
            await cm.ensure_initial(conn)
            return cm.get_messages(), cm
        finally:
            await conn.close()

    msgs, cm = asyncio.run(_run())
    stable_msgs = cm.stable_zone.messages
    assert any("先检查 dtype" in m.get("content", "") for m in stable_msgs)
    all_content = " ".join(m.get("content", "") for m in msgs)
    assert "先检查 dtype" in all_content
    assert "[历史经验]" in all_content


def test_lessons_not_injected_cross_scope():
    """office 场景不应注入 data_analysis 场景的经验。"""
    _setup_schema()

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            repo = EvolutionRepo(conn)
            await repo.add(SkillLesson(
                scope="data_analysis",
                task_summary="基金分析",
                lesson_type="success",
                lesson_content="用夏普比率评估基金",
                importance=0.9,
            ))
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
                scene="office",
                evolution_repo=repo,
            )
            await cm.ensure_initial(conn)
            return cm.get_messages()
        finally:
            await conn.close()

    msgs = asyncio.run(_run())
    all_content = " ".join(m.get("content", "") for m in msgs)
    assert "夏普比率" not in all_content
    assert "[历史经验]" not in all_content


def test_lessons_injection_respects_max_lessons():
    """注入条数受 max_lessons 预算控制(默认 3 条, importance 降序)。"""
    _setup_schema()

    async def _run() -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            repo = EvolutionRepo(conn)
            for i in range(5):
                await repo.add(SkillLesson(
                    scope="office",
                    task_summary=f"任务{i}",
                    lesson_type="success",
                    lesson_content=f"经验内容{i}",
                    importance=1.0 - i * 0.1,
                ))
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
                scene="office",
                evolution_repo=repo,
            )
            await cm.ensure_initial(conn)
            stable_content = " ".join(
                m.get("content", "") for m in cm.stable_zone.messages
            )
            return stable_content.split("- [success]")
        finally:
            await conn.close()

    entries = asyncio.run(_run())
    # 5 条经验, 只注入 importance 最高的 3 条
    injected = [e for e in entries if "任务" in e]
    assert len(injected) == 3
    assert "任务0" in injected[0]
    assert "任务4" not in "".join(injected)


def test_lessons_injection_respects_token_budget():
    """注入总 token 受 max_tokens 预算控制(默认 500, 超限停止追加)。"""
    _setup_schema()

    async def _run() -> list[str]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            repo = EvolutionRepo(conn)
            await repo.add(SkillLesson(
                scope="office",
                task_summary="超长任务",
                lesson_type="success",
                lesson_content="很长的经验内容" * 600,  # 远超 500 token
                importance=0.9,
            ))
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
                scene="office",
                evolution_repo=repo,
            )
            await cm.ensure_initial(conn)
            stable_content = " ".join(
                m.get("content", "") for m in cm.stable_zone.messages
            )
            return stable_content.split("- [success]")
        finally:
            await conn.close()

    entries = asyncio.run(_run())
    # 单条经验超过预算 → 不注入(预算在追加前检查)
    injected = [e for e in entries if "超长任务" in e]
    assert len(injected) == 0
