"""Skill 经验沉淀仓库 - 自进化经验存储层（双轨进化）。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 1.1
对应参考文档第一类路线（经验/Skill 存储型）：
- AutoSkill 的"经验存储"理念
- EvoSkill 的 Proposer Agent 反思后落地经验
- CoEvoSkills 的 Add/Merge/Discard 机制

双轨进化（2026-08-11）：
- lesson_category='domain_skill'：领域智能体（子瞻/白圭/清和）的专业技巧经验
- lesson_category='project_evolution'：无涯（主智能体）的项目级进化经验
- lesson_category='cross_domain'：跨领域可迁移经验（scope='global'）

经验类型：
- success: 成功模式（可复用的工作流/工具链）
- failure: 失败教训（避免重复犯错）
- correction: 用户纠正（模型行为修正）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import asyncpg

from private_agent.observability.logging import setup_logger

logger = setup_logger(__name__)


# scope 与 lesson_category 的合法映射（应用层校验，与 DB CHECK 约束一致）
_SCOPE_CATEGORY_MAP = {
    "monitor": "project_evolution",
    "office": "domain_skill",
    "data_analysis": "domain_skill",
    "frontend_design": "domain_skill",
    "global": "cross_domain",
}


@dataclass
class SkillLesson:
    """单条经验记录（双轨：领域技巧 or 项目进化）。"""
    scope: str
    task_summary: str
    lesson_type: str  # success / failure / correction
    lesson_content: str
    lesson_category: str = "domain_skill"  # domain_skill / project_evolution / cross_domain
    tool_chain: list[str] = field(default_factory=list)
    source_session_id: int | None = None
    source_turn: int | None = None
    id: int | None = None
    is_active: bool = True
    importance: float = 0.5
    created_at: str | None = None


class EvolutionRepo:
    """经验沉淀仓库：CRUD + 检索 + Add/Merge/Discard（双轨支持）。"""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def add(self, lesson: SkillLesson) -> int:
        """添加经验记录，返回 id。

        应用层校验 scope 与 lesson_category 一致性（与 DB CHECK 约束互为防御）。
        """
        expected_category = _SCOPE_CATEGORY_MAP.get(lesson.scope)
        if expected_category and lesson.lesson_category != expected_category:
            raise ValueError(
                f"scope-category mismatch: scope={lesson.scope} requires "
                f"lesson_category={expected_category}, got {lesson.lesson_category}"
            )

        row = await self._conn.fetchrow(
            """
            INSERT INTO skill_lessons
                (scope, lesson_category, task_summary, lesson_type, lesson_content,
                 tool_chain, source_session_id, source_turn, importance)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            lesson.scope,
            lesson.lesson_category,
            lesson.task_summary,
            lesson.lesson_type,
            lesson.lesson_content,
            json.dumps(lesson.tool_chain),
            lesson.source_session_id,
            lesson.source_turn,
            lesson.importance,
        )
        lesson_id = row["id"]
        logger.info(
            "skill_lesson_added id=%s scope=%s category=%s type=%s",
            lesson_id, lesson.scope, lesson.lesson_category, lesson.lesson_type,
        )
        return lesson_id

    async def get(self, lesson_id: int) -> SkillLesson | None:
        """按 id 获取经验记录。"""
        row = await self._conn.fetchrow(
            "SELECT * FROM skill_lessons WHERE id = $1", lesson_id
        )
        if row is None:
            return None
        return self._row_to_lesson(row)

    async def search_by_scope(
        self, scope: str, limit: int = 10
    ) -> list[SkillLesson]:
        """按场景检索经验（按重要性 + 时间排序）。"""
        rows = await self._conn.fetch(
            """
            SELECT * FROM skill_lessons
            WHERE scope = $1 AND is_active = TRUE
            ORDER BY importance DESC, created_at DESC
            LIMIT $2
            """,
            scope, limit,
        )
        return [self._row_to_lesson(r) for r in rows]

    async def search_by_keyword(
        self, keyword: str, scope: str | None = None, limit: int = 10
    ) -> list[SkillLesson]:
        """按关键词检索经验（ILIKE 模糊匹配）。"""
        pattern = f"%{keyword}%"
        if scope:
            rows = await self._conn.fetch(
                """
                SELECT * FROM skill_lessons
                WHERE is_active = TRUE AND scope = $1
                  AND (task_summary ILIKE $2 OR lesson_content ILIKE $2)
                ORDER BY importance DESC, created_at DESC
                LIMIT $3
                """,
                scope, pattern, limit,
            )
        else:
            rows = await self._conn.fetch(
                """
                SELECT * FROM skill_lessons
                WHERE is_active = TRUE
                  AND (task_summary ILIKE $1 OR lesson_content ILIKE $1)
                ORDER BY importance DESC, created_at DESC
                LIMIT $2
                """,
                pattern, limit,
            )
        return [self._row_to_lesson(r) for r in rows]

    async def discard(self, lesson_id: int) -> None:
        """软删除经验记录（is_active = FALSE）。"""
        await self._conn.execute(
            "UPDATE skill_lessons SET is_active = FALSE, updated_at = now() WHERE id = $1",
            lesson_id,
        )
        logger.info("skill_lesson_discarded id=%s", lesson_id)

    async def merge(
        self, source_id: int, target_id: int, merged_content: str
    ) -> None:
        """合并两条经验：内容写入 target，source 软删除。"""
        async with self._conn.transaction():
            await self._conn.execute(
                """
                UPDATE skill_lessons
                SET lesson_content = $1, updated_at = now()
                WHERE id = $2
                """,
                merged_content, target_id,
            )
            await self._conn.execute(
                "UPDATE skill_lessons SET is_active = FALSE, updated_at = now() WHERE id = $1",
                source_id,
            )
            logger.info(
                "skill_lesson_merged source=%s target=%s", source_id, target_id
            )

    @staticmethod
    def _row_to_lesson(row: asyncpg.Record) -> SkillLesson:
        tool_chain = row["tool_chain"] if isinstance(row["tool_chain"], list) else []
        return SkillLesson(
            id=row["id"],
            scope=row["scope"],
            lesson_category=row["lesson_category"],
            task_summary=row["task_summary"],
            lesson_type=row["lesson_type"],
            lesson_content=row["lesson_content"],
            tool_chain=tool_chain,
            source_session_id=row["source_session_id"],
            source_turn=row["source_turn"],
            is_active=row["is_active"],
            importance=row["importance"],
            created_at=str(row["created_at"]) if row["created_at"] else None,
        )
