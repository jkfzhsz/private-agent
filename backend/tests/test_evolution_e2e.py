"""Phase 4 Task 4.3 - 自进化闭环端到端测试。

验证完整链路(组件级集成):
1. 反思产出经验 → 经验入库 → 下次任务注入经验(lesson → injection)
2. 在线失败 → 采集 → 审核队列(failure → review_queue)

注: test_cross_scene_artifact_pass_e2e 按修订 2 删除(场景协作延后 V2)。
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.eval.online_failure_collector import (
    FailureType,
    OnlineFailureCollector,
)
from private_agent.eval.repos import ReviewQueueRepo
from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

# 让自建连接的 handler 指向测试库
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


async def _create_session(
    conn: "asyncpg.Connection", *, locked_skill_name: str = "office"
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO sessions (title, model_id, locked_skill_name)
        VALUES ($1, $2, $3) RETURNING id
        """,
        "test-e2e-evolution",
        "mock-glm",
        locked_skill_name,
    )


def test_reflection_to_lesson_to_injection_e2e():
    """完整链路: 反思产出经验 → 入库 → 下次任务注入到 Stable Zone。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn, locked_skill_name="office")

            # 1. 模拟反思产出的经验入库(对应 ReactLoop._maybe_reflect → evolution_repo.add)
            repo = EvolutionRepo(conn)
            lesson_id = await repo.add(SkillLesson(
                scope="office",
                lesson_category="domain_skill",
                task_summary="用 pandas 清洗销售数据",
                lesson_type="success",
                lesson_content="先用 df.dtypes 检查列类型，日期列用 pd.to_datetime(errors='coerce')",
                tool_chain=["file_read", "code_execution"],
                importance=0.8,
            ))
            assert lesson_id > 0

            # 2. 模拟下次任务时经验被检索到(对应 search_lessons 工具路径)
            results = await repo.search_by_keyword(keyword="清洗", scope="office")
            assert len(results) == 1
            assert "dtypes" in results[0].lesson_content

            # 3. 模拟下次任务启动时经验被注入 Stable Zone
            #    (对应 ContextManager.ensure_initial → _inject_lessons)
            cm = ContextManager(
                session_id=session_id,
                system_prompt="sys",
                tools=[],
                scene="office",
                evolution_repo=repo,
            )
            await cm.ensure_initial(conn)

            # 4. 验证经验已注入 Stable Zone(同 test_context_lessons_injection 模式)
            stable_msgs = cm.stable_zone.messages
            all_msgs = cm.get_messages()
            return {
                "lesson_id": lesson_id,
                "stable_content": " ".join(m.get("content", "") for m in stable_msgs),
                "all_content": " ".join(m.get("content", "") for m in all_msgs),
            }
        finally:
            await conn.close()

    info = asyncio.run(_run())
    # 经验内容应出现在 stable zone 中
    assert "dtypes" in info["stable_content"]
    assert "[历史经验]" in info["all_content"]


def test_failure_to_review_queue_e2e():
    """完整链路: 在线失败 → 采集 → 审核队列可见 → 可检索。"""
    _setup_schema()

    async def _run() -> dict:
        # 用临时 JSON 文件作为审核队列存储(同 test_eval_review_queue_repo 模式)
        queue_file = tempfile.mktemp(suffix=".json")
        try:
            # 1. 构造采集器(同 main.py 装配模式: ReviewQueueRepo + OnlineFailureCollector)
            repo = ReviewQueueRepo(queue_file=queue_file)
            collector = OnlineFailureCollector(repo)

            # 2. 采集失败(对应 ReactLoop._collect_failure → collector.collect)
            item_id = await collector.collect(
                session_id=42,
                scope="office",
                user_message="读取文件",
                failure_type=FailureType.TOOL_ERROR,
                failure_detail="file_read 工具执行超时(30s)",
                react_events=[
                    {"event_type": "tool_call", "payload": {"tool_name": "file_read"}},
                    {"event_type": "error", "payload": {"message": "timeout"}},
                ],
                final_output="⚠️ 程序异常：工具执行超时",
            )
            assert item_id is not None

            # 3. 审核队列中可见(对应 review_queue_summary 工具路径)
            pending = await repo.list_pending()
            assert len(pending) == 1
            assert pending[0]["id"] == item_id
            assert "tool_error" in pending[0]["failure_reason"]
            assert pending[0]["scope"] == "office"
            assert pending[0]["source_session_id"] == 42

            # 4. 去重验证: 5 分钟内同 session+type 不重复采集
            dedup_id = await collector.collect(
                session_id=42,
                scope="office",
                user_message="读取文件",
                failure_type=FailureType.TOOL_ERROR,
                failure_detail="file_read 超时",
                react_events=[],
                final_output="错误",
            )
            assert dedup_id is None  # 去重跳过
            pending_after = await repo.list_pending()
            assert len(pending_after) == 1  # 仍然只有 1 条

            return {"item_id": item_id, "pending_count": len(pending)}
        finally:
            if os.path.exists(queue_file):
                os.unlink(queue_file)

    info = asyncio.run(_run())
    assert info["item_id"] >= 1
    assert info["pending_count"] == 1


def test_failure_to_review_queue_different_sessions_not_deduped():
    """不同 session 的同类型失败不去重(各自独立采集)。"""
    _setup_schema()

    async def _run() -> int:
        queue_file = tempfile.mktemp(suffix=".json")
        try:
            repo = ReviewQueueRepo(queue_file=queue_file)
            collector = OnlineFailureCollector(repo)

            # session 1 的工具失败
            id1 = await collector.collect(
                session_id=1, scope="office",
                user_message="task1", failure_type=FailureType.TOOL_ERROR,
                failure_detail="超时", react_events=[], final_output="错误",
            )
            # session 2 的同类型失败(不去重)
            id2 = await collector.collect(
                session_id=2, scope="office",
                user_message="task2", failure_type=FailureType.TOOL_ERROR,
                failure_detail="超时", react_events=[], final_output="错误",
            )
            pending = await repo.list_pending()
            return len(pending)
        finally:
            if os.path.exists(queue_file):
                os.unlink(queue_file)

    count = asyncio.run(_run())
    assert count == 2  # 两个不同 session 各采集一次
