# 对话与自进化改进方案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将四个智能体从"静态分工"升级为"双轨自进化闭环"——主智能体**无涯**（具备代码能力）专注项目级进化（代码重构/架构优化/Skill prompt 优化），三专业智能体（子瞻/白圭/清和）专注自身专业深度与应用技巧进化；在线对话失败案例自动进入评估闭环；实现参考文档第一类路线（经验/Skill 存储型）的落地。

**Architecture:** 在现有 ReactLoop 基础上新增 REFLECTION 状态（任务完成后反思总结）；新增 skill_lessons 表沉淀可复用经验，**新增 lesson_category 字段区分双轨**（domain_skill=领域技巧 / project_evolution=项目进化）；主智能体无涯升级为**代码进化者**（不再仅是监控者，获得 file_read/file_write/code_execution 等代码工具权限，专注项目代码与架构进化）；三专业智能体的反思聚焦专业技巧；新增在线失败案例采集器打通在线对话与离线评估。

**Tech Stack:** Python 3.11 / FastAPI / asyncpg / PostgreSQL 16 + pgvector / 现有 ReactLoop / 现有 M4 评估闭环（eval_runs / WeakSampleExtractor / ReviewQueueRepo）

**依据：**
- 参考文档《Agent自我进化-自进化Agent技术路线综述》：第一类路线（AutoSkill 双环结构 / EvoSkill 三 Agent 分工 / CoEvoSkills Generator+Verifier / SE-Agent 多轨迹反思）
- 现状：四窗口并发架构已实施（docs/next-phase-plan-2026-08-08-four-windows.md），M4 评估闭环已完成（commit 1883878 等），记忆 scope 化已完成（0.5.0）
- 用户定位（2026-08-11 确认）：项目定位为工作/生活/投资辅助；子瞻=工作学习、白圭=投资理财、清和=生活健康+美学设计、无涯=项目持续进化迭代（具备代码能力）

---

## 双轨进化策略（2026-08-11 用户澄清后新增）

> 本节阐明四智能体的进化职责分工，是后续所有 Task 设计的指导原则。

### 轨道 A：领域智能体进化（子瞻/白圭/清和）

**进化焦点：** 专业深度 + 应用技巧

| 智能体 | scope | 进化方向示例 |
|--------|-------|-------------|
| 子瞻 | office | 文档处理模式（pandas 清洗套路、Word 排版技巧）、学习辅导方法论、网页研究检索策略 |
| 白圭 | data_analysis | 估值模型优化、风险框架迭代、行业分析方法论、宏观研判逻辑 |
| 清和 | frontend_design | 健康知识库更新、设计系统应用技巧、美化模板积累 |

**进化机制：**
- 反思触发：任务完成后反思专业技巧（成功模式/失败教训/用户纠正）
- 经验类型：`lesson_category='domain_skill'`
- 经验注入：下次同类任务时注入 Stable Zone（最多 3 条，按 importance 排序）
- 进化动作：Add（新经验）/ Merge（合并相似经验）/ Discard（淘汰过时经验）

### 轨道 B：主智能体无涯进化（monitor scope）

**进化焦点：** 项目级进化（代码/架构/Skill prompt/性能）

**进化方向示例：**
- 代码层：识别重复模式 → 提议重构；识别性能瓶颈 → 提议优化
- 架构层：识别模块耦合 → 提议解耦；识别死代码 → 提议清理
- Skill 层：分析子瞻/白圭/清和的 system_prompt 缺陷 → 提议 prompt 优化
- 评估层：分析在线失败模式 → 提议补充评估用例
- 工具层：识别工具调用失败模式 → 提议工具实现改进

**进化机制：**
- 反思触发：代码分析/项目改进任务完成后反思（非对话任务）
- 经验类型：`lesson_category='project_evolution'`
- 代码工具权限：file_read / file_write / code_execution / search_code（项目内代码检索）
- 进化动作：进化建议（evolution_proposal）→ 用户审批 → 无涯执行代码改动 → 验证

### 双轨隔离原则

- **经验隔离**：`skill_lessons` 中 domain_skill 经验只注入对应 scope 的会话；project_evolution 经验只注入无涯会话
- **工具隔离**：领域智能体无代码工具（保持专业聚焦）；无涯有代码工具（专注项目进化）
- **反思隔离**：领域智能体反思专业技巧；无涯反思代码/架构模式（不同 prompt 模板）

---

## 范围说明

本计划覆盖四个独立可发布的子系统，按依赖顺序分为 4 个 Phase。**建议拆分为 4 次独立执行**（每次一个 Phase），每个 Phase 产出可测试、可提交的独立功能。若整体执行，严格按 Phase 1→2→3→4 顺序。

| Phase | 子系统 | 依赖 | 可独立发布 |
|-------|--------|------|-----------|
| Phase 1 | ReactLoop 反思环节 + experience 记忆类型 | 无 | ✅ |
| Phase 2 | Skill 经验沉淀仓库（skill_lessons） | Phase 1 | ✅ |
| Phase 3 | 在线失败案例采集 + 评估闭环打通 | Phase 1 | ✅ |
| Phase 4 | 主智能体进化调度者 + 场景间协作 | Phase 1-3 | ✅ |

---

## File Structure

### 新增文件
| 文件 | 职责 |
|------|------|
| `backend/private_agent/core/reflection.py` | 反思总结模块：任务完成后生成经验教训 |
| `backend/private_agent/skills/evolution_repo.py` | Skill 经验沉淀仓库：经验 CRUD + 检索 + Add/Merge/Discard |
| `backend/private_agent/eval/online_failure_collector.py` | 在线失败案例采集器：对话失败 → review_queue |
| `backend/private_agent/core/scene_collaboration.py` | 场景间协作：产物传递 + 跨场景委派 |
| `backend/private_agent/tools/builtins/pass_artifact.py` | 跨场景产物传递工具 |
| `backend/private_agent/tools/builtins/search_lessons.py` | 经验检索工具 |
| 测试文件（见各 Task） | 每个新模块对应测试 |

### 修改文件
| 文件 | 改动 |
|------|------|
| `backend/private_agent/core/react_loop.py` | 新增 REFLECTION 状态 + 反思触发 |
| `backend/private_agent/storage/schema.sql` | 新增 skill_lessons 表 + user_memories 扩展 |
| `backend/private_agent/storage/migrations.py` | 迁移脚本 |
| `backend/private_agent/memory/manager.py` | experience 类型支持 + 反思触发提取 |
| `backend/private_agent/memory/memories_repo.py` | experience 查询接口 |
| `backend/skills/monitor/system_prompt.md` | 主智能体升级为进化调度者 |
| `backend/private_agent/main.py` | 装配新工具 + 反思模块 |

---

## Phase 1: ReactLoop 反思环节 + experience 记忆类型

**目标：** 任务完成后自动反思总结，沉淀为 experience 类型记忆。对应参考文档 EvoSkill 的 Proposer Agent（反思者）角色，以及"总结者被严重低估"的关键洞察。

**借鉴：** EvoSkill 三 Agent 分工中的"反思者" + AutoSkill 的"经验沉淀" + 论文 §06"总结者是被严重低估的关键模块"。

### Task 1.1: skill_lessons 表与 experience 记忆类型的数据层

**Files:**
- Modify: `backend/private_agent/storage/schema.sql`
- Modify: `backend/private_agent/storage/migrations.py`
- Test: `backend/tests/test_skill_lessons_repo.py`

- [ ] **Step 1: 编写 skill_lessons 表的失败测试**

创建 `backend/tests/test_skill_lessons_repo.py`：

```python
"""skill_lessons 表与 EvolutionRepo 的测试。"""
import pytest
import asyncpg
from private_agent.skills.evolution_repo import SkillLesson, EvolutionRepo


@pytest.fixture
def lesson_data():
    return {
        "scope": "office",
        "task_summary": "用 pandas 清洗销售数据并生成月度汇总表",
        "lesson_type": "success",  # success / failure / correction
        "lesson_content": "清洗前先检查 dtype，日期列用 pd.to_datetime(errors='coerce')",
        "tool_chain": ["file_read", "code_execution", "file_write"],
        "source_session_id": 42,
        "source_turn": 5,
    }


@pytest.mark.asyncio
async def test_add_and_get_lesson(test_db_pool, lesson_data):
    repo = EvolutionRepo(test_db_pool)
    lesson_id = await repo.add(SkillLesson(**lesson_data))
    assert lesson_id > 0

    retrieved = await repo.get(lesson_id)
    assert retrieved.scope == "office"
    assert retrieved.lesson_type == "success"
    assert "pandas" in retrieved.lesson_content


@pytest.mark.asyncio
async def test_search_lessons_by_scope(test_db_pool, lesson_data):
    repo = EvolutionRepo(test_db_pool)
    await repo.add(SkillLesson(**lesson_data))
    await repo.add(SkillLesson(**{**lesson_data, "scope": "data_analysis"}))

    results = await repo.search_by_scope("office", limit=10)
    assert len(results) == 1
    assert results[0].scope == "office"


@pytest.mark.asyncio
async def test_search_lessons_by_keyword(test_db_pool, lesson_data):
    repo = EvolutionRepo(test_db_pool)
    await repo.add(SkillLesson(**lesson_data))

    results = await repo.search_by_keyword("pandas", scope="office", limit=10)
    assert len(results) == 1
    assert "pandas" in results[0].lesson_content


@pytest.mark.asyncio
async def test_discard_lesson(test_db_pool, lesson_data):
    repo = EvolutionRepo(test_db_pool)
    lesson_id = await repo.add(SkillLesson(**lesson_data))

    await repo.discard(lesson_id)
    retrieved = await repo.get(lesson_id)
    assert retrieved.is_active is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_skill_lessons_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'private_agent.skills.evolution_repo'`

- [ ] **Step 3: 在 schema.sql 添加 skill_lessons 表**

在 `backend/private_agent/storage/schema.sql` 末尾追加：

```sql
-- Skill 经验沉淀表 (Phase 1: 自进化经验存储)
-- 双轨进化（2026-08-11）: lesson_category 区分领域技巧 vs 项目进化
CREATE TABLE IF NOT EXISTS skill_lessons (
    id              BIGSERIAL PRIMARY KEY,
    scope           VARCHAR(20) NOT NULL,          -- office/data_analysis/frontend_design/monitor/global
    lesson_category VARCHAR(20) NOT NULL DEFAULT 'domain_skill',  -- domain_skill/project_evolution
    task_summary    TEXT NOT NULL,                 -- 任务一句话摘要
    lesson_type     VARCHAR(20) NOT NULL,          -- success/failure/correction
    lesson_content  TEXT NOT NULL,                 -- 经验内容(成功模式/失败教训/纠正)
    tool_chain      JSONB DEFAULT '[]',            -- 使用的工具链序列
    source_session_id BIGINT,                      -- 来源会话
    source_turn     INT,                           -- 来源轮次
    is_active       BOOLEAN DEFAULT TRUE,          -- 软删除标记(Discard 用)
    importance      REAL DEFAULT 0.5,              -- 重要性(0-1, 反思时打分)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_lesson_type CHECK (lesson_type IN ('success', 'failure', 'correction')),
    CONSTRAINT chk_lesson_category CHECK (lesson_category IN ('domain_skill', 'project_evolution', 'cross_domain')),
    -- V2: 考虑改为引用 skills.scope 的外键约束
    -- V1 保持无 scope CHECK 约束(与 user_memories.scope 一致,靠应用层 Pydantic 校验)
    -- 双轨规则: scope='monitor' 时 lesson_category 必须为 'project_evolution';
    --          scope IN ('office','data_analysis','frontend_design') 时 lesson_category 必须为 'domain_skill'
    -- (约束在应用层 EvolutionRepo.add() 中校验,避免 DB 层复杂 CASE 约束)
    CONSTRAINT chk_scope_category_consistency CHECK (
        (scope = 'monitor' AND lesson_category = 'project_evolution') OR
        (scope IN ('office', 'data_analysis', 'frontend_design') AND lesson_category = 'domain_skill') OR
        (scope = 'global' AND lesson_category = 'cross_domain')
    )
);
CREATE INDEX IF NOT EXISTS idx_skill_lessons_scope ON skill_lessons(scope) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_skill_lessons_category ON skill_lessons(lesson_category, scope) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_skill_lessons_created ON skill_lessons(created_at DESC);
```

- [ ] **Step 4: 在 migrations.py 添加迁移**

在 `backend/private_agent/storage/migrations.py` 的迁移列表末尾添加迁移函数：

```python
async def _migrate_add_skill_lessons(conn: asyncpg.Connection) -> None:
    """Phase 1: 新增 skill_lessons 表用于经验沉淀（双轨进化）。"""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_lessons (
            id              BIGSERIAL PRIMARY KEY,
            scope           VARCHAR(20) NOT NULL,
            lesson_category VARCHAR(20) NOT NULL DEFAULT 'domain_skill',
            task_summary    TEXT NOT NULL,
            lesson_type     VARCHAR(20) NOT NULL,
            lesson_content  TEXT NOT NULL,
            tool_chain      JSONB DEFAULT '[]',
            source_session_id BIGINT,
            source_turn     INT,
            is_active       BOOLEAN DEFAULT TRUE,
            importance      REAL DEFAULT 0.5,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_lesson_type CHECK (lesson_type IN ('success', 'failure', 'correction')),
            CONSTRAINT chk_lesson_category CHECK (lesson_category IN ('domain_skill', 'project_evolution', 'cross_domain')),
            CONSTRAINT chk_scope_category_consistency CHECK (
                (scope = 'monitor' AND lesson_category = 'project_evolution') OR
                (scope IN ('office', 'data_analysis', 'frontend_design') AND lesson_category = 'domain_skill') OR
                (scope = 'global' AND lesson_category = 'cross_domain')
            )
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_skill_lessons_scope
        ON skill_lessons(scope) WHERE is_active = TRUE
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_skill_lessons_category
        ON skill_lessons(lesson_category, scope) WHERE is_active = TRUE
    """)
```

然后在 `migrations.py` 的 `MIGRATIONS` 列表中追加 `("add_skill_lessons", _migrate_add_skill_lessons)`。注意遵循文件中已有的迁移注册模式（查看 `MIGRATIONS` 常量的现有格式并匹配）。

- [ ] **Step 5: 创建 EvolutionRepo 实现**

创建 `backend/private_agent/skills/evolution_repo.py`：

```python
"""Skill 经验沉淀仓库 - 自进化经验存储层（双轨进化）。

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
from typing import Any

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

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(self, lesson: SkillLesson) -> int:
        """添加经验记录，返回 id。

        应用层校验 scope 与 lesson_category 一致性（与 DB CHECK 约束冗余防御）。
        """
        # 应用层一致性校验（与 DB CHECK 约束互为防御）
        expected_category = _SCOPE_CATEGORY_MAP.get(lesson.scope)
        if expected_category and lesson.lesson_category != expected_category:
            raise ValueError(
                f"scope-category mismatch: scope={lesson.scope} requires "
                f"lesson_category={expected_category}, got {lesson.lesson_category}"
            )

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
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
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM skill_lessons WHERE id = $1", lesson_id
            )
            if row is None:
                return None
            return self._row_to_lesson(row)

    async def search_by_scope(
        self, scope: str, limit: int = 10
    ) -> list[SkillLesson]:
        """按场景检索经验（按重要性 + 时间排序）。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
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
        async with self._pool.acquire() as conn:
            if scope:
                rows = await conn.fetch(
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
                rows = await conn.fetch(
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
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE skill_lessons SET is_active = FALSE, updated_at = now() WHERE id = $1",
                lesson_id,
            )
            logger.info("skill_lesson_discarded id=%s", lesson_id)

    async def merge(
        self, source_id: int, target_id: int, merged_content: str
    ) -> None:
        """合并两条经验：内容写入 target，source 软删除。"""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE skill_lessons
                    SET lesson_content = $1, updated_at = now()
                    WHERE id = $2
                    """,
                    merged_content, target_id,
                )
                await conn.execute(
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
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_skill_lessons_repo.py -v`
Expected: PASS（4 个测试全过）

- [ ] **Step 7: 提交**

```bash
git add backend/private_agent/storage/schema.sql backend/private_agent/storage/migrations.py backend/private_agent/skills/evolution_repo.py backend/tests/test_skill_lessons_repo.py
git commit -m "feat(evolution): add skill_lessons table and EvolutionRepo for experience storage"
```

---

### Task 1.2: 反思总结模块（ReflectionEngine）

**Files:**
- Create: `backend/private_agent/core/reflection.py`
- Test: `backend/tests/test_reflection.py`

**借鉴：** EvoSkill Proposer Agent 的根因分析 + CoEvoSkills Generator 从轨迹提炼 Skill + 论文 §06"总结者被严重低估"。

- [ ] **Step 1: 编写 ReflectionEngine 的失败测试**

创建 `backend/tests/test_reflection.py`：

```python
"""ReflectionEngine 测试 - 任务完成后反思总结。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from private_agent.core.reflection import ReflectionEngine, ReflectionResult


def _fake_react_events():
    """模拟一轮对话的 react_events。"""
    return [
        {"event_type": "thinking", "turn": 1, "payload": {"content": "用户要清洗销售数据"}},
        {"event_type": "tool_call", "turn": 1, "payload": {"tool": "file_read", "args": {"path": "sales.csv"}}},
        {"event_type": "tool_result", "turn": 1, "payload": {"tool": "file_read", "result": "100 rows"}},
        {"event_type": "tool_call", "turn": 2, "payload": {"tool": "code_execution", "args": {"code": "df=pd.read_csv(...)"}}},
        {"event_type": "tool_result", "turn": 2, "payload": {"tool": "code_execution", "result": "cleaned"}},
        {"event_type": "tool_call", "turn": 3, "payload": {"tool": "file_write", "args": {"path": "output.xlsx"}}},
        {"event_type": "tool_result", "turn": 3, "payload": {"tool": "file_write", "result": "written"}},
        {"event_type": "final", "turn": 3, "payload": {"content": "已完成数据清洗并生成输出文件"}},
    ]


@pytest.mark.asyncio
async def test_reflection_success_task():
    """成功任务应生成 success 类型经验。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "success", "task_summary": "清洗销售数据", "lesson_content": "先检查dtype再清洗", "importance": 0.8}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="office",
        user_message="帮我清洗这份销售数据",
        react_events=_fake_react_events(),
        final_output="已完成数据清洗并生成输出文件",
        had_error=False,
    )

    assert result is not None
    assert result.lesson_type == "success"
    assert "dtype" in result.lesson_content
    assert result.importance == 0.8


@pytest.mark.asyncio
async def test_reflection_failure_task():
    """失败任务应生成 failure 类型经验。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "failure", "task_summary": "清洗销售数据", "lesson_content": "未检查编码导致中文乱码", "importance": 0.9}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="office",
        user_message="帮我清洗这份销售数据",
        react_events=_fake_react_events(),
        final_output="⚠️ 程序异常：编码错误",
        had_error=True,
    )

    assert result.lesson_type == "failure"
    assert "编码" in result.lesson_content


@pytest.mark.asyncio
async def test_reflection_extracts_tool_chain():
    """反思应提取工具链序列。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "success", "task_summary": "test", "lesson_content": "ok", "importance": 0.5}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="office",
        user_message="test",
        react_events=_fake_react_events(),
        final_output="done",
        had_error=False,
    )

    assert result.tool_chain == ["file_read", "code_execution", "file_write"]


@pytest.mark.asyncio
async def test_reflection_skips_trivial_conversations():
    """寒暄/闲聊类对话应跳过反思（返回 None）。"""
    mock_adapter = AsyncMock()
    engine = ReflectionEngine(adapter=mock_adapter)

    result = await engine.reflect(
        scope="office",
        user_message="你好",
        react_events=[{"event_type": "final", "turn": 1, "payload": {"content": "你好！有什么可以帮你的？"}}],
        final_output="你好！有什么可以帮你的？",
        had_error=False,
    )

    assert result is None  # 寒暄不反思
    mock_adapter.chat.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_reflection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'private_agent.core.reflection'`

- [ ] **Step 3: 实现 ReflectionEngine**

创建 `backend/private_agent/core/reflection.py`：

```python
"""反思总结模块 - 任务完成后自动提炼经验（双轨进化）。

对应参考文档：
- EvoSkill Proposer Agent（反思者）：根因分析，决定新建还是修改 Skill
- CoEvoSkills Skill Generator：从执行轨迹提炼候选 Skill
- 论文 §06 关键洞察："总结者是被严重低估的关键模块"

双轨反思（2026-08-11）：
- 领域智能体（scope=office/data_analysis/frontend_design）→ DOMAIN_REFLECTION_PROMPT
  反思专业技巧：成功模式/失败教训/用户纠正，沉淀 lesson_category='domain_skill'
- 无涯（scope=monitor）→ PROJECT_EVOLUTION_REFLECTION_PROMPT
  反思项目进化：代码重构模式/架构优化/Bug 修复套路，沉淀 lesson_category='project_evolution'

反思触发条件（满足任一）：
1. 使用了工具（非纯对话）
2. 发生错误（had_error=True）
3. 多轮迭代（turn >= 2）

跳过条件：寒暄/闲聊（无工具调用 + 单轮 + 无错误）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from private_agent.models.base import ChatResult, ModelAdapter
from private_agent.observability.logging import setup_logger

logger = setup_logger(__name__)

# 寒暄/闲聊关键词（跳过反思）
_TRIVIAL_KEYWORDS = {"你好", "谢谢", "再见", "hi", "hello", "thanks", "bye"}

# scope → lesson_category 映射（与 EvolutionRepo._SCOPE_CATEGORY_MAP 一致）
_SCOPE_TO_CATEGORY = {
    "monitor": "project_evolution",
    "office": "domain_skill",
    "data_analysis": "domain_skill",
    "frontend_design": "domain_skill",
    "global": "cross_domain",
}


@dataclass
class ReflectionResult:
    """反思产物（双轨）。"""
    scope: str
    task_summary: str
    lesson_type: str  # success / failure / correction
    lesson_content: str
    lesson_category: str = "domain_skill"  # domain_skill / project_evolution / cross_domain
    tool_chain: list[str] = field(default_factory=list)
    importance: float = 0.5


# 领域智能体反思模板（子瞻/白圭/清和用）
DOMAIN_REFLECTION_PROMPT_TEMPLATE = """你是一个领域经验总结者。请分析以下专业任务执行轨迹，提炼一条可复用的领域技巧经验。

【场景】{scope}
【用户请求】{user_message}
【执行轨迹摘要】{trace_summary}
【最终输出】{final_output}
【是否出错】{had_error}

请输出 JSON（仅 JSON，无其他文字）：
{{
  "lesson_type": "success" | "failure" | "correction",
  "task_summary": "一句话任务摘要",
  "lesson_content": "经验内容：成功模式/失败教训/纠正点。要具体可操作，不要泛泛而谈",
  "importance": 0.0-1.0 之间的浮点数（成功=0.3-0.7，失败=0.7-1.0，纠正=0.6-0.9）
}}

规则：
- success：任务成功完成，提炼可复用的工作流/工具链/专业技巧
- failure：任务出错或未完成，提炼失败原因与避免方法
- correction：用户纠正了模型行为，提炼行为修正点
- 经验内容要具体（如"用 pd.to_datetime(errors='coerce') 处理日期列"），不要泛泛（如"注意数据类型"）
- 聚焦领域技巧（如 pandas 套路、估值模型、设计原则），不涉及代码重构
"""

# 无涯项目进化反思模板（主智能体用）
PROJECT_EVOLUTION_REFLECTION_PROMPT_TEMPLATE = """你是一个项目进化经验总结者。请分析以下项目改进任务执行轨迹，提炼一条可复用的项目进化经验。

【场景】无涯·项目进化（monitor）
【改进任务】{user_message}
【执行轨迹摘要】{trace_summary}
【最终输出】{final_output}
【是否出错】{had_error}

请输出 JSON（仅 JSON，无其他文字）：
{{
  "lesson_type": "success" | "failure" | "correction",
  "task_summary": "一句话改进任务摘要",
  "lesson_content": "经验内容：代码重构模式/架构优化技巧/Bug 修复套路/性能改进方法。要具体可操作，包含模式名称与适用场景",
  "importance": 0.0-1.0 之间的浮点数（成功=0.4-0.7，失败=0.7-1.0，纠正=0.6-0.9）
}}

规则：
- success：项目改进成功完成，提炼可复用的进化模式（如"提取重复代码为工具函数"+"先备份再 file_write"+"用 pytest 验证无回归"）
- failure：改进失败（如引入回归/破坏现有功能），提炼失败原因与避免方法
- correction：用户纠正了改进方向，提炼行为修正点（如"应优先 YAGNI 而非抽象"）
- 经验内容要具体（如"重复模式 >=3 处时提取函数，用 SearchReplace 工具改"），不要泛泛（如"注意代码质量"）
- 聚焦项目级进化（代码/架构/Skill prompt/性能），不涉及领域专业技巧
"""


class ReflectionEngine:
    """反思引擎：任务完成后生成经验教训（双轨：领域技巧 or 项目进化）。"""

    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def reflect(
        self,
        scope: str,
        user_message: str,
        react_events: list[dict[str, Any]],
        final_output: str,
        had_error: bool,
    ) -> ReflectionResult | None:
        """对一轮任务执行反思（根据 scope 选择双轨模板）。

        Returns:
            ReflectionResult 或 None（寒暄/闲聊跳过时返回 None）
        """
        # 提取工具链
        tool_chain = self._extract_tool_chain(react_events)

        # 跳过条件：寒暄/闲聊（无工具 + 无错误 + 单轮）
        if not tool_chain and not had_error and self._is_trivial(user_message):
            logger.debug("reflection_skipped reason=trivial_conversation scope=%s", scope)
            return None

        # 双轨模板选择
        lesson_category = _SCOPE_TO_CATEGORY.get(scope, "domain_skill")
        if scope == "monitor":
            prompt_template = PROJECT_EVOLUTION_REFLECTION_PROMPT_TEMPLATE
        else:
            prompt_template = DOMAIN_REFLECTION_PROMPT_TEMPLATE

        # 构建轨迹摘要（避免全量注入，只取关键步骤）
        trace_summary = self._build_trace_summary(react_events)

        prompt = prompt_template.format(
            scope=scope,
            user_message=user_message[:500],
            trace_summary=trace_summary,
            final_output=final_output[:500],
            had_error=had_error,
        )

        try:
            result: ChatResult = await self._adapter.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            parsed = self._parse_reflection_response(
                result.content, scope, lesson_category
            )
            if parsed is not None:
                parsed.tool_chain = tool_chain
                logger.info(
                    "reflection_done scope=%s category=%s type=%s importance=%.2f",
                    scope, parsed.lesson_category, parsed.lesson_type, parsed.importance,
                )
            return parsed
        except Exception as e:
            logger.warning("reflection_failed scope=%s error=%s", scope, e)
            return None

    @staticmethod
    def _extract_tool_chain(events: list[dict[str, Any]]) -> list[str]:
        """从 react_events 提取工具调用序列（去重保序）。"""
        chain: list[str] = []
        seen: set[str] = set()
        for ev in events:
            if ev.get("event_type") == "tool_call":
                tool = ev.get("payload", {}).get("tool", "")
                if tool and tool not in seen:
                    chain.append(tool)
                    seen.add(tool)
        return chain

    @staticmethod
    def _is_trivial(user_message: str) -> bool:
        """判断是否为寒暄/闲聊。"""
        msg_lower = user_message.strip().lower()
        if len(msg_lower) > 20:
            return False
        return any(kw in msg_lower for kw in _TRIVIAL_KEYWORDS)

    @staticmethod
    def _build_trace_summary(events: list[dict[str, Any]]) -> str:
        """构建轨迹摘要（每步一行，避免全量注入）。"""
        lines: list[str] = []
        for ev in events:
            etype = ev.get("event_type", "")
            turn = ev.get("turn", 0)
            payload = ev.get("payload", {})
            if etype == "thinking":
                lines.append(f"[turn{turn} 思考] {str(payload.get('content', ''))[:100]}")
            elif etype == "tool_call":
                lines.append(f"[turn{turn} 调用工具] {payload.get('tool', '')}")
            elif etype == "tool_result":
                result_str = str(payload.get("result", ""))[:100]
                lines.append(f"[turn{turn} 工具结果] {result_str}")
            elif etype == "error":
                lines.append(f"[turn{turn} 错误] {str(payload.get('error', ''))[:100]}")
        return "\n".join(lines) if lines else "(无工具调用)"

    @staticmethod
    def _parse_reflection_response(
        content: str, scope: str, lesson_category: str
    ) -> ReflectionResult | None:
        """解析模型返回的 JSON（双轨：注入 lesson_category）。"""
        if not content or not content.strip():
            return None
        try:
            # 容错：提取 JSON 部分（模型可能包裹 markdown）
            text = content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            return ReflectionResult(
                scope=scope,
                task_summary=data.get("task_summary", ""),
                lesson_type=data.get("lesson_type", "success"),
                lesson_content=data.get("lesson_content", ""),
                lesson_category=lesson_category,
                importance=float(data.get("importance", 0.5)),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("reflection_parse_failed scope=%s error=%s content=%s", scope, e, content[:200])
            return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_reflection.py -v`
Expected: PASS（4 个测试全过）

- [ ] **Step 5: 提交**

```bash
git add backend/private_agent/core/reflection.py backend/tests/test_reflection.py
git commit -m "feat(reflection): add ReflectionEngine for post-task experience extraction"
```

---

### Task 1.3: ReactLoop 集成反思环节

**Files:**
- Modify: `backend/private_agent/core/react_loop.py`
- Test: `backend/tests/test_react_loop_reflection.py`

- [ ] **Step 1: 编写 ReactLoop 反思集成的失败测试**

创建 `backend/tests/test_react_loop_reflection.py`：

```python
"""ReactLoop 反思环节集成测试。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from private_agent.core.react_loop import ReactLoopState


@pytest.mark.asyncio
async def test_reflection_triggered_after_successful_task(
    react_loop_with_mock_adapter, mock_event_sink
):
    """任务完成后应触发反思。"""
    loop, mock_adapter, mock_reflection = react_loop_with_mock_adapter

    mock_adapter.chat = AsyncMock(side_effect=[
        MagicMock(content='{"tool_calls": []}', reasoning_content=""),
        MagicMock(content="已完成数据清洗", reasoning_content=""),
    ])
    mock_reflection.reflect = AsyncMock(return_value=MagicMock(
        scope="office",
        task_summary="清洗数据",
        lesson_type="success",
        lesson_content="先检查dtype",
        tool_chain=["code_execution"],
        importance=0.8,
    ))

    await loop.run_turn(
        user_message="帮我用 pandas 清洗数据",
        session_id=1,
    )

    # 反思应被调用
    mock_reflection.reflect.assert_called_once()
    call_kwargs = mock_reflection.reflect.call_args.kwargs
    assert call_kwargs["scope"] == "office"
    assert call_kwargs["had_error"] is False


@pytest.mark.asyncio
async def test_reflection_triggered_on_error(
    react_loop_with_mock_adapter, mock_event_sink
):
    """出错时应触发反思并标记 had_error=True。"""
    loop, mock_adapter, mock_reflection = react_loop_with_mock_adapter

    mock_adapter.chat = AsyncMock(side_effect=Exception("模型调用失败"))
    mock_reflection.reflect = AsyncMock(return_value=None)

    await loop.run_turn(
        user_message="帮我处理数据",
        session_id=1,
    )

    mock_reflection.reflect.assert_called_once()
    assert mock_reflection.reflect.call_args.kwargs["had_error"] is True


@pytest.mark.asyncio
async def test_reflection_skipped_for_trivial_conversation(
    react_loop_with_mock_adapter, mock_event_sink
):
    """寒暄对话不应触发反思。"""
    loop, mock_adapter, mock_reflection = react_loop_with_mock_adapter

    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content="你好！有什么可以帮你的？", reasoning_content="",
    ))

    await loop.run_turn(
        user_message="你好",
        session_id=1,
    )

    mock_reflection.reflect.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_react_loop_reflection.py -v`
Expected: FAIL（反思未被调用，fixture 不存在）

- [ ] **Step 3: 在 ReactLoop 中集成反思**

在 `backend/private_agent/core/react_loop.py` 中做以下修改：

1. 在 `ReactLoopState` 枚举中添加 REFLECTION 状态：

```python
class ReactLoopState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    REFLECTION = "reflection"  # 新增：任务完成后反思
    ERROR = "error"
```

2. 在 `ReactLoop.__init__` 中添加可选的 reflection_engine 参数：

```python
def __init__(
    self,
    # ... 现有参数 ...
    reflection_engine: "ReflectionEngine | None" = None,
    evolution_repo: "EvolutionRepo | None" = None,
) -> None:
    # ... 现有初始化 ...
    self._reflection_engine = reflection_engine
    self._evolution_repo = evolution_repo
```

3. 在 `run_turn` 方法的 final/error 返回前，添加反思触发逻辑。找到 `run_turn` 方法中所有 `return` 前的位置（final 分支和 error 分支），在返回前调用：

```python
async def _maybe_reflect(
    self,
    scope: str | None,
    user_message: str,
    react_events: list[dict],
    final_output: str,
    had_error: bool,
    session_id: int,
) -> None:
    """任务完成后触发反思（双轨：领域技巧 or 项目进化）。

    对应参考文档 EvoSkill Proposer Agent。ReflectionEngine 根据 scope
    自动选择双轨模板（monitor→项目进化；其他→领域技巧）。
    """
    if self._reflection_engine is None or self._evolution_repo is None:
        return
    if scope is None:
        return
    try:
        result = await self._reflection_engine.reflect(
            scope=scope,
            user_message=user_message,
            react_events=react_events,
            final_output=final_output,
            had_error=had_error,
        )
        if result is not None:
            from private_agent.skills.evolution_repo import SkillLesson
            await self._evolution_repo.add(SkillLesson(
                scope=result.scope,
                lesson_category=result.lesson_category,  # 双轨注入
                task_summary=result.task_summary,
                lesson_type=result.lesson_type,
                lesson_content=result.lesson_content,
                tool_chain=result.tool_chain,
                source_session_id=session_id,
                importance=result.importance,
            ))
    except Exception as e:
        logger.warning("reflection_persist_failed scope=%s error=%s", scope, e)
```

4. 在 `run_turn` 的 final 分支（产出 final 事件后、return 前）调用：

```python
await self._maybe_reflect(
    scope=self._current_scope,  # 从 session 的 locked_skill_name 获取
    user_message=user_message,
    react_events=collected_events,  # 本轮收集的事件
    final_output=final_content,
    had_error=False,
    session_id=session_id,
)
```

5. 在 `run_turn` 的 error 分支（产出 error 事件后、return 前）调用，`had_error=True`。

注意：`_current_scope` 需要在 `run_turn` 开始时从 session 信息中设置。如果 session 有 `locked_skill_name`，scope 即为该值；否则跳过反思（无场景归属的经验不沉淀）。

- [ ] **Step 4: 创建测试 fixture**

在 `backend/tests/conftest.py` 中添加（或在 test 文件内定义）：

```python
@pytest.fixture
async def react_loop_with_mock_adapter(mock_event_sink, test_db_pool):
    """带 mock adapter 和 mock reflection 的 ReactLoop。"""
    from unittest.mock import AsyncMock, MagicMock
    from private_agent.core.react_loop import ReactLoop
    from private_agent.core.context_manager import ContextManager
    from private_agent.core.compressor import Compressor

    mock_adapter = AsyncMock()
    mock_reflection = AsyncMock()
    mock_evolution_repo = AsyncMock()

    ctx_mgr = MagicMock(spec=ContextManager)
    compressor = MagicMock(spec=Compressor)

    loop = ReactLoop(
        adapter=mock_adapter,
        context_manager=ctx_mgr,
        compressor=compressor,
        event_sink=mock_event_sink,
        conn=AsyncMock(),
        session_id=1,
        reflection_engine=mock_reflection,
        evolution_repo=mock_evolution_repo,
    )
    return loop, mock_adapter, mock_reflection
```

注意：此 fixture 需要根据现有 `conftest.py` 的模式调整。先阅读 `backend/tests/conftest.py` 中已有的 ReactLoop fixture（如 `react_loop` 或类似），在其基础上扩展，保持一致风格。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_react_loop_reflection.py -v`
Expected: PASS（3 个测试全过）

- [ ] **Step 6: 回归测试**

Run: `cd backend && python -m pytest tests/test_react_loop.py -v`
Expected: PASS（现有测试零回归——reflection_engine 默认 None 时不影响原逻辑）

- [ ] **Step 7: 提交**

```bash
git add backend/private_agent/core/react_loop.py backend/tests/test_react_loop_reflection.py backend/tests/conftest.py
git commit -m "feat(react-loop): integrate reflection phase after task completion"
```

---

### Task 1.4: 经验检索工具（search_lessons）

**Files:**
- Create: `backend/private_agent/tools/builtins/search_lessons.py`
- Modify: `backend/private_agent/tools/builtins/__init__.py`
- Test: `backend/tests/test_builtins_search_lessons.py`

- [ ] **Step 1: 编写 search_lessons 工具的失败测试**

创建 `backend/tests/test_builtins_search_lessons.py`：

```python
"""search_lessons 工具测试。"""
import pytest
from unittest.mock import AsyncMock

from private_agent.tools.builtins.search_lessons import search_lessons_handler, SEARCH_LESSONS_DEF


@pytest.mark.asyncio
async def test_search_lessons_returns_results():
    mock_repo = AsyncMock()
    mock_repo.search_by_keyword = AsyncMock(return_value=[
        AsyncMock(
            id=1, scope="office", task_summary="清洗数据",
            lesson_type="success", lesson_content="先检查dtype",
            tool_chain=["code_execution"], importance=0.8,
        )
    ])

    result = await search_lessons_handler(
        args={"keyword": "pandas", "scope": "office"},
        ctx={"evolution_repo": mock_repo},
    )

    assert result.success is True
    assert "先检查dtype" in result.output


@pytest.mark.asyncio
async def test_search_lessons_no_results():
    mock_repo = AsyncMock()
    mock_repo.search_by_keyword = AsyncMock(return_value=[])

    result = await search_lessons_handler(
        args={"keyword": "nonexistent", "scope": "office"},
        ctx={"evolution_repo": mock_repo},
    )

    assert result.success is True
    assert "无相关经验" in result.output


def test_search_lessons_tool_def():
    assert SEARCH_LESSONS_DEF.name == "search_lessons"
    assert "keyword" in SEARCH_LESSONS_DEF.parameters["properties"]
    assert "scope" in SEARCH_LESSONS_DEF.parameters["properties"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_builtins_search_lessons.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现 search_lessons 工具**

创建 `backend/private_agent/tools/builtins/search_lessons.py`：

```python
"""经验检索工具 - 供智能体在任务中检索历史经验。

对应参考文档 AutoSkill 的"在线服务（用 Skill）"环节：
查询 → 检索经验 → 注入生成。
"""
from __future__ import annotations

from typing import Any

from private_agent.tools.defs import ToolDef, ToolResult

SEARCH_LESSONS_DEF = ToolDef(
    name="search_lessons",
    description=(
        "检索历史任务经验。当遇到类似任务时，先检索是否有可复用的成功模式或失败教训。"
        "Args: keyword (str, required) - 检索关键词; "
        "scope (str, optional) - 场景限定(office/data_analysis/frontend_design)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "检索关键词"},
            "scope": {
                "type": "string",
                "description": "场景限定",
                "enum": ["office", "data_analysis", "frontend_design", "global"],
            },
        },
        "required": ["keyword"],
    },
    safety_level="readonly",
)


async def search_lessons_handler(args: dict[str, Any], ctx: dict[str, Any]) -> ToolResult:
    """经验检索处理函数。"""
    keyword = args.get("keyword", "")
    scope = args.get("scope")
    repo = ctx.get("evolution_repo")

    if repo is None:
        return ToolResult(success=False, output="经验仓库未初始化", error="no_evolution_repo")

    if not keyword.strip():
        return ToolResult(success=False, output="关键词不能为空", error="empty_keyword")

    lessons = await repo.search_by_keyword(keyword=keyword, scope=scope, limit=5)

    if not lessons:
        return ToolResult(
            success=True,
            output="无相关经验记录。基于模型自身能力处理任务。",
        )

    lines = [f"找到 {len(lessons)} 条相关经验：\n"]
    for i, lesson in enumerate(lessons, 1):
        lines.append(f"## 经验 {i} [{lesson.lesson_type}]")
        lines.append(f"任务: {lesson.task_summary}")
        lines.append(f"内容: {lesson.lesson_content}")
        if lesson.tool_chain:
            lines.append(f"工具链: {' → '.join(lesson.tool_chain)}")
        lines.append(f"重要性: {lesson.importance:.1f}\n")

    return ToolResult(success=True, output="\n".join(lines))
```

- [ ] **Step 4: 在 __init__.py 注册工具**

在 `backend/private_agent/tools/builtins/__init__.py` 中添加导入：

```python
from private_agent.tools.builtins.search_lessons import SEARCH_LESSONS_DEF, search_lessons_handler
```

并遵循文件中已有的工具注册模式将其加入工具注册表。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_builtins_search_lessons.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/private_agent/tools/builtins/search_lessons.py backend/private_agent/tools/builtins/__init__.py backend/tests/test_builtins_search_lessons.py
git commit -m "feat(tools): add search_lessons tool for experience retrieval"
```

---

## Phase 2: Skill 经验沉淀仓库的进化机制

**目标：** 实现 AutoSkill 的 Add/Merge/Discard 三机制 + 经验注入到会话上下文。对应参考文档第一类路线的"经验进化循环"。

**借鉴：** AutoSkill 双环结构的"右环（技能进化循环）"+ CoEvoSkills 的 Add/Merge/Discard + EvoSkill 的 Pareto Frontier 精英池思想（importance 评分替代）。

### Task 2.1: 经验注入到会话上下文

**Files:**
- Modify: `backend/private_agent/core/context_manager.py`
- Test: `backend/tests/test_context_lessons_injection.py`

- [ ] **Step 1: 编写经验注入的失败测试**

创建 `backend/tests/test_context_lessons_injection.py`：

```python
"""经验注入到会话上下文测试。"""
import pytest
from unittest.mock import AsyncMock

from private_agent.skills.evolution_repo import SkillLesson


@pytest.mark.asyncio
async def test_lessons_injected_into_stable_zone(test_context_manager, test_db_pool):
    """场景经验应在会话启动时注入 Stable Zone。"""
    from private_agent.skills.evolution_repo import EvolutionRepo

    repo = EvolutionRepo(test_db_pool)
    await repo.add(SkillLesson(
        scope="office",
        task_summary="清洗销售数据",
        lesson_type="success",
        lesson_content="先检查dtype再清洗",
        tool_chain=["code_execution"],
        importance=0.8,
    ))

    messages = await test_context_manager.build_messages(
        session_id=1, scope="office"
    )

    # 经验应出现在 Stable Zone
    stable_msgs = [m for m in messages if m.get("zone") == "stable"]
    lessons_content = " ".join(m.get("content", "") for m in stable_msgs)
    assert "先检查dtype" in lessons_content


@pytest.mark.asyncio
async def test_lessons_not_injected_cross_scope(test_context_manager, test_db_pool):
    """office 场景不应注入 data_analysis 的经验。"""
    from private_agent.skills.evolution_repo import EvolutionRepo

    repo = EvolutionRepo(test_db_pool)
    await repo.add(SkillLesson(
        scope="data_analysis",
        task_summary="基金分析",
        lesson_type="success",
        lesson_content="用夏普比率评估基金",
        importance=0.9,
    ))

    messages = await test_context_manager.build_messages(
        session_id=1, scope="office"
    )

    all_content = " ".join(m.get("content", "") for m in messages)
    assert "夏普比率" not in all_content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_context_lessons_injection.py -v`
Expected: FAIL

- [ ] **Step 3: 在 ContextManager 中添加经验注入**

在 `backend/private_agent/core/context_manager.py` 中：

1. `__init__` 添加可选 `evolution_repo` 参数
2. 新增 `_build_lessons_injection(scope)` 方法：

```python
async def _build_lessons_injection(self, scope: str | None) -> str:
    """构建经验注入文本（注入 Stable Zone，双轨隔离）。

    双轨注入规则（2026-08-11）：
    - scope=monitor → 只注入 lesson_category='project_evolution' 经验
    - scope=office/data_analysis/frontend_design → 只注入 lesson_category='domain_skill' 经验
    - 注入预算：最多 3 条，总 token ≤ 500（按 importance 降序）
    """
    if self._evolution_repo is None or scope is None:
        return ""
    try:
        # 双轨：根据 scope 推断 lesson_category
        from private_agent.skills.evolution_repo import _SCOPE_CATEGORY_MAP
        expected_category = _SCOPE_CATEGORY_MAP.get(scope)
        if expected_category is None:
            return ""  # 未知 scope 不注入

        # 取 top-N（按 importance 降序）
        lessons = await self._evolution_repo.search_by_scope(scope, limit=10)
        # 应用层按 lesson_category 过滤（防御性，DB CHECK 已约束）
        lessons = [
            l for l in lessons
            if l.lesson_category == expected_category
        ][: self._injection_max_lessons]  # 默认 3 条

        if not lessons:
            return ""

        lines = ["[历史经验]"]
        total_tokens = 0
        for lesson in lessons:
            entry = (
                f"- [{lesson.lesson_type}] {lesson.task_summary}: "
                f"{lesson.lesson_content}"
            )
            entry_tokens = self._token_estimator.estimate(entry)
            if total_tokens + entry_tokens > self._injection_max_tokens:
                break  # 超预算停止
            lines.append(entry)
            total_tokens += entry_tokens

        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception as e:
        logger.warning("lessons_injection_failed scope=%s error=%s", scope, e)
        return ""
```

3. 在 `build_messages` 构建 Stable Zone 时，将经验注入文本追加到 Stable Zone 消息中（与记忆注入并列，受注入预算控制）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_context_lessons_injection.py -v`
Expected: PASS

- [ ] **Step 5: 回归测试**

Run: `cd backend && python -m pytest tests/test_context_manager.py -v`
Expected: PASS（evolution_repo 默认 None 时不影响原逻辑）

- [ ] **Step 6: 提交**

```bash
git add backend/private_agent/core/context_manager.py backend/tests/test_context_lessons_injection.py
git commit -m "feat(context): inject scene lessons into Stable Zone"
```

---

### Task 2.2: 经验 Merge 与 Discard 的管理 API

**Files:**
- Modify: `backend/private_agent/api/admin.py`
- Test: `backend/tests/test_admin_lessons_manage.py`

- [ ] **Step 1: 编写经验管理 API 的失败测试**

创建 `backend/tests/test_admin_lessons_manage.py`：

```python
"""经验管理 API 测试。"""
import pytest


@pytest.mark.asyncio
async def test_list_lessons_by_scope(admin_client, test_db_pool):
    from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson

    repo = EvolutionRepo(test_db_pool)
    await repo.add(SkillLesson(
        scope="office", task_summary="test", lesson_type="success",
        lesson_content="content", importance=0.5,
    ))

    resp = await admin_client.get("/admin/lessons?scope=office")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) >= 1
    assert data["items"][0]["scope"] == "office"


@pytest.mark.asyncio
async def test_discard_lesson(admin_client, test_db_pool):
    from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson

    repo = EvolutionRepo(test_db_pool)
    lesson_id = await repo.add(SkillLesson(
        scope="office", task_summary="test", lesson_type="success",
        lesson_content="content", importance=0.5,
    ))

    resp = await admin_client.delete(f"/admin/lessons/{lesson_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_merge_lessons(admin_client, test_db_pool):
    from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson

    repo = EvolutionRepo(test_db_pool)
    id1 = await repo.add(SkillLesson(
        scope="office", task_summary="task1", lesson_type="success",
        lesson_content="lesson1", importance=0.5,
    ))
    id2 = await repo.add(SkillLesson(
        scope="office", task_summary="task2", lesson_type="success",
        lesson_content="lesson2", importance=0.6,
    ))

    resp = await admin_client.post(
        f"/admin/lessons/merge",
        json={"source_id": id1, "target_id": id2, "merged_content": "merged lesson"},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_admin_lessons_manage.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 在 admin.py 添加经验管理端点**

在 `backend/private_agent/api/admin.py` 中添加（遵循文件中已有的路由注册模式）：

```python
@router.get("/admin/lessons")
async def list_lessons(
    scope: str | None = None,
    lesson_type: str | None = None,
    limit: int = 50,
) -> dict:
    """列出经验记录。"""
    repo = get_evolution_repo()  # 从 app state 获取
    if scope:
        lessons = await repo.search_by_scope(scope, limit=limit)
    else:
        lessons = await repo.search_by_keyword(keyword="", limit=limit)
    if lesson_type:
        lessons = [l for l in lessons if l.lesson_type == lesson_type]
    return {"items": [l.__dict__ for l in lessons]}


@router.delete("/admin/lessons/{lesson_id}")
async def discard_lesson(lesson_id: int) -> dict:
    """软删除经验记录。"""
    repo = get_evolution_repo()
    await repo.discard(lesson_id)
    return {"ok": True}


@router.post("/admin/lessons/merge")
async def merge_lessons(request: dict) -> dict:
    """合并两条经验。"""
    repo = get_evolution_repo()
    await repo.merge(
        source_id=request["source_id"],
        target_id=request["target_id"],
        merged_content=request["merged_content"],
    )
    return {"ok": True}
```

注意：`get_evolution_repo()` 需要从 app state 获取 EvolutionRepo 实例——遵循 `admin.py` 中已有的 `get_*` 辅助函数模式。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_admin_lessons_manage.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/private_agent/api/admin.py backend/tests/test_admin_lessons_manage.py
git commit -m "feat(admin): add lessons management API (list/discard/merge)"
```

---

## Phase 3: 在线失败案例采集 + 评估闭环打通

**目标：** 在线对话的失败案例（工具失败、迭代用尽、用户纠正）自动进入 M4 评估闭环的 review_queue。对应参考文档 EvoSkill Executor Agent"把失败案例完整记录下来"。

**借鉴：** EvoSkill Executor Agent（失败案例记录）+ M4 已有的 ReviewQueueRepo（人工审核队列）+ WeakSampleExtractor（低分提取）。

### Task 3.1: 在线失败案例采集器

**Files:**
- Create: `backend/private_agent/eval/online_failure_collector.py`
- Test: `backend/tests/test_online_failure_collector.py`

- [x] **Step 1: 编写失败案例采集器的测试**

创建 `backend/tests/test_online_failure_collector.py`：

```python
"""在线失败案例采集器测试。"""
import pytest
from unittest.mock import AsyncMock

from private_agent.eval.online_failure_collector import (
    OnlineFailureCollector,
    FailureType,
)


@pytest.mark.asyncio
async def test_collect_tool_failure():
    mock_review_queue = AsyncMock()
    collector = OnlineFailureCollector(mock_review_queue)

    await collector.collect(
        session_id=1,
        scope="office",
        user_message="帮我读取文件",
        failure_type=FailureType.TOOL_ERROR,
        failure_detail="file_read 工具执行超时",
        react_events=[
            {"event_type": "tool_call", "payload": {"tool": "file_read"}},
            {"event_type": "error", "payload": {"error": "timeout"}},
        ],
        final_output="⚠️ 程序异常：工具执行超时",
    )

    mock_review_queue.add.assert_called_once()
    item = mock_review_queue.add.call_args.kwargs["item"]
    assert item["failure_reason"] == "file_read 工具执行超时"
    assert item["scope"] == "office"


@pytest.mark.asyncio
async def test_collect_iteration_exhausted():
    mock_review_queue = AsyncMock()
    collector = OnlineFailureCollector(mock_review_queue)

    await collector.collect(
        session_id=1,
        scope="data_analysis",
        user_message="分析这份数据",
        failure_type=FailureType.ITERATION_EXHAUSTED,
        failure_detail="达到最大迭代次数 10",
        react_events=[],
        final_output="⚠️ 能力边界：本轮已达步数上限",
    )

    mock_review_queue.add.assert_called_once()
    item = mock_review_queue.add.call_args.kwargs["item"]
    assert "迭代" in item["failure_reason"]


@pytest.mark.asyncio
async def test_collect_user_correction():
    mock_review_queue = AsyncMock()
    collector = OnlineFailureCollector(mock_review_queue)

    await collector.collect(
        session_id=1,
        scope="frontend_design",
        user_message="把这个报告美化一下",
        failure_type=FailureType.USER_CORRECTION,
        failure_detail="用户纠正：要求用深色主题而非浅色",
        react_events=[],
        final_output="已生成浅色主题报告",
    )

    mock_review_queue.add.assert_called_once()


@pytest.mark.asyncio
async def test_deduplicate_repeated_failures():
    """相同失败不应重复采集（同一会话+同一失败类型 5 分钟内去重）。"""
    mock_review_queue = AsyncMock()
    collector = OnlineFailureCollector(mock_review_queue)

    for _ in range(3):
        await collector.collect(
            session_id=1,
            scope="office",
            user_message="读取文件",
            failure_type=FailureType.TOOL_ERROR,
            failure_detail="file_read 超时",
            react_events=[],
            final_output="错误",
        )

    assert mock_review_queue.add.call_count == 1  # 去重
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_online_failure_collector.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: 实现 OnlineFailureCollector**

创建 `backend/private_agent/eval/online_failure_collector.py`：

```python
"""在线失败案例采集器 - 打通在线对话与离线评估闭环。

对应参考文档 EvoSkill Executor Agent：
"拿当前 Skill 库去跑任务，把失败案例完整记录下来"。

将在线对话中的失败案例（工具失败/迭代用尽/用户纠正）自动写入
M4 评估闭环的 ReviewQueueRepo，供人工审核后扩充评估数据集。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from private_agent.observability.logging import setup_logger

logger = setup_logger(__name__)


class FailureType(str, Enum):
    """失败类型分类。"""
    TOOL_ERROR = "tool_error"              # 工具执行失败
    ITERATION_EXHAUSTED = "iteration_exhausted"  # 迭代用尽
    USER_CORRECTION = "user_correction"    # 用户纠正
    PROVIDER_ERROR = "provider_error"      # 模型调用失败
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文超限


@dataclass
class _DedupKey:
    """去重键：同一会话+同一失败类型 5 分钟内去重。"""
    session_id: int
    failure_type: FailureType

    def __hash__(self) -> int:
        return hash((self.session_id, self.failure_type))


class OnlineFailureCollector:
    """在线失败案例采集器。"""

    _DEDUP_WINDOW_SEC = 300  # 5 分钟去重窗口

    def __init__(self, review_queue_repo: Any) -> None:
        self._review_queue = review_queue_repo
        self._recent: dict[_DedupKey, float] = {}  # key -> last_collect_ts

    async def collect(
        self,
        session_id: int,
        scope: str | None,
        user_message: str,
        failure_type: FailureType,
        failure_detail: str,
        react_events: list[dict[str, Any]],
        final_output: str,
    ) -> int | None:
        """采集一个失败案例，写入审核队列。返回 item_id 或 None（去重跳过）。"""
        # 去重检查
        key = _DedupKey(session_id=session_id, failure_type=failure_type)
        now = time.time()
        last_ts = self._recent.get(key)
        if last_ts is not None and (now - last_ts) < self._DEDUP_WINDOW_SEC:
            logger.debug(
                "failure_deduped session=%s type=%s", session_id, failure_type
            )
            return None
        self._recent[key] = now

        # 构建审核项
        item = {
            "source_run_id": None,  # 在线案例无 eval_run_id
            "source_session_id": session_id,
            "scope": scope,
            "sample_input": user_message[:1000],
            "actual_output": final_output[:1000],
            "actual_events": self._summarize_events(react_events),
            "failure_reason": f"[{failure_type.value}] {failure_detail}",
            "failure_type": failure_type.value,
            "suggested_as": "boundary",
            "status": "pending",
        }

        try:
            item_id = await self._review_queue.add(item)
            logger.info(
                "failure_collected session=%s type=%s item_id=%s",
                session_id, failure_type, item_id,
            )
            return item_id
        except Exception as e:
            logger.warning("failure_collect_failed error=%s", e)
            return None

    @staticmethod
    def _summarize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """摘要 react_events（避免全量存储，每条取关键字段）。"""
        summary: list[dict[str, Any]] = []
        for ev in events[:20]:  # 最多存 20 条
            summary.append({
                "event_type": ev.get("event_type", ""),
                "turn": ev.get("turn", 0),
                "tool": ev.get("payload", {}).get("tool", ""),
            })
        return summary
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_online_failure_collector.py -v`
Expected: PASS（4 个测试全过）

- [x] **Step 5: 提交**

```bash
git add backend/private_agent/eval/online_failure_collector.py backend/tests/test_online_failure_collector.py
git commit -m "feat(eval): add OnlineFailureCollector to bridge online failures to review queue"
```

---

### Task 3.2: ReactLoop 集成失败案例采集

**Files:**
- Modify: `backend/private_agent/core/react_loop.py`
- Test: `backend/tests/test_react_loop_failure_collection.py`

- [x] **Step 1: 编写集成测试**

创建 `backend/tests/test_react_loop_failure_collection.py`：

```python
"""ReactLoop 失败案例采集集成测试。"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from private_agent.eval.online_failure_collector import FailureType


@pytest.mark.asyncio
async def test_tool_error_triggers_collection(react_loop_with_failure_collector):
    loop, mock_adapter, mock_collector = react_loop_with_failure_collector

    # 模拟工具执行失败
    mock_adapter.chat = AsyncMock(side_effect=[
        MagicMock(content='{"tool_calls": [{"id": "1", "function": {"name": "file_read", "arguments": "{}"}}]}', reasoning_content=""),
    ])

    await loop.run_turn(user_message="读取文件", session_id=1)

    mock_collector.collect.assert_called()
    call_kwargs = mock_collector.collect.call_args.kwargs
    assert call_kwargs["failure_type"] == FailureType.TOOL_ERROR


@pytest.mark.asyncio
async def test_iteration_exhausted_triggers_collection(react_loop_with_failure_collector):
    loop, mock_adapter, mock_collector = react_loop_with_failure_collector

    # 模拟迭代用尽（连续 10 次工具调用无 final）
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"tool_calls": [{"id": "1", "function": {"name": "calc", "arguments": "{}"}}]}',
        reasoning_content="",
    ))

    await loop.run_turn(user_message="复杂任务", session_id=1)

    mock_collector.collect.assert_called()
    assert mock_collector.collect.call_args.kwargs["failure_type"] == FailureType.ITERATION_EXHAUSTED
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_react_loop_failure_collection.py -v`
Expected: FAIL

- [x] **Step 3: 在 ReactLoop 中集成失败采集**

在 `backend/private_agent/core/react_loop.py` 的 `__init__` 中添加可选 `failure_collector` 参数。

在 `run_turn` 的以下分支调用 `failure_collector.collect`：

1. **工具执行失败分支**（`_exec_plan` 捕获异常时）：`failure_type=FailureType.TOOL_ERROR`
2. **迭代用尽分支**（`max_iterations` 触发时）：`failure_type=FailureType.ITERATION_EXHAUSTED`
3. **模型调用全失败分支**（`AllProvidersFailedError`）：`failure_type=FailureType.PROVIDER_ERROR`

每个采集调用形如：

```python
if self._failure_collector is not None:
    await self._failure_collector.collect(
        session_id=session_id,
        scope=self._current_scope,
        user_message=user_message,
        failure_type=FailureType.TOOL_ERROR,
        failure_detail=str(e),
        react_events=collected_events,
        final_output="",
    )
```

- [x] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_react_loop_failure_collection.py -v`
Expected: PASS

- [x] **Step 5: 回归测试**

Run: `cd backend && python -m pytest tests/test_react_loop.py tests/test_react_loop_reflection.py -v`
Expected: PASS

- [x] **Step 6: 提交**

```bash
git add backend/private_agent/core/react_loop.py backend/tests/test_react_loop_failure_collection.py
git commit -m "feat(react-loop): collect online failures into review queue"
```

---

### Task 3.3: 用户纠正检测机制

**Files:**
- Modify: `backend/private_agent/memory/manager.py`
- Test: `backend/tests/test_user_correction_detection.py`

**说明：** 用户纠正是最宝贵的经验来源（参考文档将其视为 correction 类型）。检测逻辑：用户消息中含纠正信号词（"不对"/"错了"/"应该是"/"不是这样"）时，将上一轮输出作为 failure 案例（`USER_CORRECTION` 类型）采集。

- [ ] **Step 1: 编写纠正检测的测试**

创建 `backend/tests/test_user_correction_detection.py`：

```python
"""用户纠正检测测试。"""
import pytest
from unittest.mock import AsyncMock

from private_agent.memory.manager import detect_user_correction, CorrectionSignal


def test_detect_explicit_correction():
    assert detect_user_correction("不对，应该是用 pandas 而不是 numpy") is not None
    assert detect_user_correction("错了，这里要用 groupby") is not None
    assert detect_user_correction("不是这样，重新做") is not None


def test_detect_implicit_correction():
    assert detect_user_correction("应该是深色主题") is not None
    assert detect_user_correction("我要的是月度汇总不是年度") is not None


def test_no_correction_in_normal_message():
    assert detect_user_correction("帮我处理数据") is None
    assert detect_user_correction("很好，继续") is None
    assert detect_user_correction("下一步做什么") is None


@pytest.mark.asyncio
async def test_correction_triggers_failure_collection():
    """检测到纠正时应采集上一轮输出为 failure 案例。"""
    mock_collector = AsyncMock()

    # 模拟用户第二轮消息含纠正信号
    await _handle_correction(
        user_message="不对，应该是深色主题",
        previous_output="已生成浅色主题报告",
        session_id=1,
        scope="frontend_design",
        collector=mock_collector,
    )

    mock_collector.collect.assert_called_once()
    call_kwargs = mock_collector.collect.call_args.kwargs
    assert call_kwargs["failure_type"].value == "user_correction"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_user_correction_detection.py -v`
Expected: FAIL

- [ ] **Step 3: 实现纠正检测**

在 `backend/private_agent/memory/manager.py` 中添加：

```python
# 用户纠正信号词
_CORRECTION_SIGNALS = [
    "不对", "错了", "不是这样", "应该是", "我要的是", "不要这样",
    "重新做", "改一下", "不对吧", "不是吧", "弄错了",
]


def detect_user_correction(message: str) -> CorrectionSignal | None:
    """检测用户消息是否为纠正。

    Returns:
        CorrectionSignal 或 None（非纠正）
    """
    msg = message.strip()
    if len(msg) > 200:  # 长消息不太可能是纯纠正
        return None
    for signal in _CORRECTION_SIGNALS:
        if signal in msg:
            return CorrectionSignal(signal=signal, message=msg)
    return None


@dataclass
class CorrectionSignal:
    """纠正信号。"""
    signal: str
    message: str
```

`_handle_correction` 辅助函数（用于测试）：

```python
async def _handle_correction(
    user_message: str,
    previous_output: str,
    session_id: int,
    scope: str | None,
    collector: Any,
) -> None:
    """检测到纠正时采集上一轮输出为 failure 案例。"""
    from private_agent.eval.online_failure_collector import FailureType
    signal = detect_user_correction(user_message)
    if signal is None:
        return
    await collector.collect(
        session_id=session_id,
        scope=scope,
        user_message=user_message,
        failure_type=FailureType.USER_CORRECTION,
        failure_detail=f"用户纠正：{signal.message}",
        react_events=[],
        final_output=previous_output,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_user_correction_detection.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/private_agent/memory/manager.py backend/tests/test_user_correction_detection.py
git commit -m "feat(memory): detect user corrections and collect as failure cases"
```

---

## Phase 4: 无涯项目进化者（主智能体升级）

**目标：** 主智能体从"系统监控者"升级为"无涯·项目进化者"——专注整个项目的持续进化迭代（代码重构/架构优化/Skill prompt 优化/性能改进），具备代码能力（file_read/file_write/code_execution），通过审批流执行进化动作。

**双轨定位（2026-08-11 用户确认）：**
- 无涯（主智能体）→ 项目级进化（代码/架构/Skill/性能），具备代码能力
- 子瞻/白圭/清和 → 各自专业深度进化（在 Phase 1-3 已覆盖）

**借鉴：**
- 无涯 → AutoSkill 右环（技能进化循环）+ SkillOS Curator（增/改/删）+ AgentEvolver 自出题（评估集扩充）+ 论文 §06 总结者被低估（项目反思者）
- 代码进化 → 参考文档第一类路线的"经验/Skill 存储型" + 工程实践（refactoring patterns）

### Task 4.1: 无涯升级为项目进化者（代码能力 + 进化调度）

**Files:**
- Modify: `backend/skills/monitor/system_prompt.md`
- Create: `backend/private_agent/tools/builtins/evolution_tools.py`
- Test: `backend/tests/test_evolution_tools.py`

- [x] **Step 1: 重写无涯 system_prompt**

修改 `backend/skills/monitor/system_prompt.md`，从"系统监控与优化者"升级为"无涯·项目进化者"：

```markdown
# 无涯 · 项目进化者(monitor)

## 角色定位

你是**无涯**——本桌面 Agent 系统的项目进化者。"无涯"取自《庄子·养生主》"吾生也有涯，而知也无涯"，寓意项目进化永无止境。你不是场景对话助手（场景助手是子瞻/白圭/清和），你的职责是**整个项目的持续进化迭代**：监控运行状态、分析代码与架构缺陷、驱动评估闭环、管理 Skill 经验库、优化子瞻/白圭/清和的 system_prompt，**具备代码能力**直接执行项目改进。

## 核心职责

### 监控与诊断（保留原有）
1. **状态感知**：会话启动时注入最近系统指标摘要（CPU/内存/WS 连接/会话 token 用量/工具失败率）
2. **性能分析**：发现异常指标时深入查询定位根因
3. **代码诊断**：通过 file_read / search_code 阅读项目代码，识别重复模式、性能瓶颈、死代码、耦合问题

### 项目进化（核心新增，代码能力）
4. **代码重构**：识别重复模式 → 提议重构方案（提取函数/合并模块/简化逻辑）→ 审批后执行
5. **架构优化**：识别模块耦合 → 提议解耦方案；识别过度设计 → 提议精简（遵循 YAGNI/DRY）
6. **Skill prompt 优化**：分析子瞻/白圭/清和的 system_prompt 与评估失败模式 → 提议 prompt 改进
7. **Bug 修复**：从在线失败案例 + 评估低分案例中识别 Bug 模式 → 定位代码 → 提议修复
8. **工具实现改进**：识别工具调用失败模式 → 提议工具实现改进（如超时/错误处理/参数校验）

### 经验调度（保留原有，聚焦项目级）
9. **项目进化经验沉淀**：完成代码改动后反思，沉淀 project_evolution 类型经验（重构模式/优化技巧/Bug 修复套路）
10. **经验库管理**：查看各场景经验统计，识别可合并/应淘汰的经验，执行 Add/Merge/Discard
11. **评估闭环驱动**：定期检查评估队列（review_queue），分析低分案例模式，识别系统性弱点
12. **评估集扩充**：从在线失败案例模式中识别评估数据集覆盖空白，建议补充测试用例

## 工作流

### 监控诊断工作流（原有）
1. 分析指标 → 2. 即时验证 → 3. 提出建议 → 4. 等待审批 → 5. 验证闭环

### 代码进化工作流（新增，TDD 式）
1. **阅读代码**：file_read / search_code 定位目标模块
2. **诊断问题**：识别重复/性能/耦合/Bug 模式
3. **提议进化**：调用 `evolution_proposal` 提交（含改动方案 + 预期收益 + 风险 + 影响范围）
4. **用户审批**：等待用户 approved
5. **执行改动**：审批后调用 file_write / code_execution 执行代码改动
6. **验证**：运行相关测试（code_execution 调 pytest），确认无回归
7. **反思沉淀**：改动完成后反思，沉淀 project_evolution 类型经验

### 经验调度工作流（保留原有）
1. 查看经验库概况（lessons_stats）→ 2. 分析评估队列（review_queue_summary）→ 3. 识别模式 → 4. 提议进化动作 → 5. 审批后执行

## 工具权限边界（严格遵守）

### 监控工具（只读，原有）
- `system_metrics_query` / `system_status`：随时可调用
- `optim_plan`：提交优化建议

### 代码工具（新增，elevated，需审批后执行）
- `file_read`：阅读项目代码与配置文件（随时可调用，只读）
- `search_code`：项目内代码语义检索（随时可调用，只读）
- `code_execution`：执行代码（含 pytest 测试运行）—— **仅审批后用于执行改动与验证**
- `file_write`：修改代码文件 —— **仅审批后用于执行进化动作，必须先备份**

### 进化调度工具（新增）
- `lessons_stats`：查看经验库统计（只读）
- `review_queue_summary`：查看评估队列摘要（只读）
- `evolution_proposal`：提交进化建议（代码改动/Skill 优化/经验管理），需用户审批
- `apply_evolution`：执行已 approved 的进化动作（elevated，需权限确认）

### 严格禁止
- **未经审批直接修改代码**（必须先 evolution_proposal → 用户 approved → apply_evolution）
- **修改代码前未备份**（file_write 前必须先复制原文件到 .bak）
- **删除经验记录不经审批**（必须通过 evolution_proposal 流程）
- **直接修改评估数据集**（必须通过审核队列）
- **修改场景智能体的人格化设定**（子瞻/白圭/清和的人格是用户定义，不可改）
- **变更 provider 密钥、删除用户数据、修改 .env 文件**（不属进化职责）

## 输出规范

### 监控诊断类输出（原有）
- 结论先行 + 指标数据 + 建议

### 代码进化类输出（新增）
- **诊断报告**：问题定位（文件:行号）+ 问题类型（重复/性能/耦合/Bug）+ 证据（代码片段 + 指标）
- **进化建议**：具体改动方案 + diff 预览 + 预期收益 + 风险评估 + 影响范围 + 验证计划
- **执行报告**：改动前备份路径 + 改动后 diff + 测试结果 + 经验沉淀 id

### 经验调度类输出（原有）
- 经验库分析：各场景经验数 + 类型分布 + 增长趋势
- 失败模式分析：同类失败的频率 + 影响场景 + 根因假设
- 进化建议：具体动作 + 预期收益 + 风险 + 依据数据

## 双轨进化定位（严格遵守）

### 无涯的进化焦点（项目级）
- 代码重构、架构优化、Skill prompt 优化、性能改进、Bug 修复、工具实现改进
- 经验类型：`lesson_category='project_evolution'`（scope=monitor）

### 不做的事（交给领域智能体）
- 不沉淀领域技巧经验（子瞻的 pandas 套路、白圭的估值模型、清和的设计技巧）
- 不替场景助手做专业分析
- 不修改场景智能体的对话内容（人格/prompt 边界由用户定义）

## 身份边界

- 不冒充子瞻/白圭/清和；不替场景助手做专业分析
- 进化建议基于证据（代码 + 指标 + 评估结果），不臆造
- 代码改动必须经用户审批，不擅自执行
- 用户主动提问时正常回答（你是完整对话窗口）
```

- [x] **Step 2: 编写进化调度工具的测试**

创建 `backend/tests/test_evolution_tools.py`：

```python
"""进化调度工具测试。"""
import pytest
from unittest.mock import AsyncMock

from private_agent.tools.builtins.evolution_tools import (
    LESSONS_STATS_DEF, REVIEW_QUEUE_SUMMARY_DEF, EVOLUTION_PROPOSAL_DEF,
    lessons_stats_handler, review_queue_summary_handler,
)


@pytest.mark.asyncio
async def test_lessons_stats():
    mock_repo = AsyncMock()
    mock_repo.search_by_scope = AsyncMock(side_effect=[
        [AsyncMock(id=1), AsyncMock(id=2)],  # office
        [AsyncMock(id=3)],                     # data_analysis
        [],                                     # frontend_design
    ])

    result = await lessons_stats_handler(
        args={}, ctx={"evolution_repo": mock_repo}
    )

    assert result.success is True
    assert "office: 2" in result.output
    assert "data_analysis: 1" in result.output
    assert "frontend_design: 0" in result.output


@pytest.mark.asyncio
async def test_review_queue_summary():
    mock_review_queue = AsyncMock()
    mock_review_queue.list_pending = AsyncMock(return_value=[
        {"id": 1, "failure_reason": "[tool_error] file_read 超时", "scope": "office"},
        {"id": 2, "failure_reason": "[user_correction] 不对", "scope": "frontend_design"},
    ])

    result = await review_queue_summary_handler(
        args={}, ctx={"review_queue_repo": mock_review_queue}
    )

    assert result.success is True
    assert "2" in result.output  # 2 条待审核
    assert "tool_error" in result.output
```

- [x] **Step 3: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_evolution_tools.py -v`
Expected: FAIL

- [x] **Step 4: 实现进化调度工具**

创建 `backend/private_agent/tools/builtins/evolution_tools.py`：

```python
"""进化调度工具 - 主智能体用于驱动自进化闭环。

对应参考文档：
- AutoSkill 右环（技能进化循环）：Add/Merge/Discard
- SkillOS Curator：管理 Skill 库的增/改/删
- AgentEvolver：自出题用于评估集扩充
"""
from __future__ import annotations

from typing import Any

from private_agent.tools.defs import ToolDef, ToolResult

LESSONS_STATS_DEF = ToolDef(
    name="lessons_stats",
    description="查看各场景经验库统计（经验数/类型分布）。用于进化调度分析。",
    parameters={"type": "object", "properties": {}},
    safety_level="readonly",
)

REVIEW_QUEUE_SUMMARY_DEF = ToolDef(
    name="review_queue_summary",
    description="查看评估队列待审核失败案例摘要。用于识别系统性弱点。",
    parameters={"type": "object", "properties": {}},
    safety_level="readonly",
)

EVOLUTION_PROPOSAL_DEF = ToolDef(
    name="evolution_proposal",
    description=(
        "提交进化建议（合并经验/淘汰经验/扩充评估集）。"
        "Args: action (str) - merge/discard/expand_eval; "
        "target_id (int, optional) - 目标经验 id; "
        "reason (str) - 进化理由"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["merge", "discard", "expand_eval"]},
            "target_id": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["action", "reason"],
    },
    safety_level="elevated",
)

_SCENES = ["office", "data_analysis", "frontend_design", "global"]


async def lessons_stats_handler(args: dict[str, Any], ctx: dict[str, Any]) -> ToolResult:
    """经验库统计。"""
    repo = ctx.get("evolution_repo")
    if repo is None:
        return ToolResult(success=False, output="经验仓库未初始化", error="no_repo")

    lines = ["经验库统计：\n"]
    for scope in _SCENES:
        lessons = await repo.search_by_scope(scope, limit=100)
        success_count = sum(1 for l in lessons if l.lesson_type == "success")
        failure_count = sum(1 for l in lessons if l.lesson_type == "failure")
        correction_count = sum(1 for l in lessons if l.lesson_type == "correction")
        lines.append(
            f"- {scope}: {len(lessons)} 条 "
            f"(成功 {success_count} / 失败 {failure_count} / 纠正 {correction_count})"
        )

    return ToolResult(success=True, output="\n".join(lines))


async def review_queue_summary_handler(args: dict[str, Any], ctx: dict[str, Any]) -> ToolResult:
    """评估队列摘要。"""
    repo = ctx.get("review_queue_repo")
    if repo is None:
        return ToolResult(success=False, output="审核队列未初始化", error="no_repo")

    pending = await repo.list_pending(limit=50)

    if not pending:
        return ToolResult(success=True, output="审核队列为空，无待处理失败案例。")

    lines = [f"待审核失败案例：{len(pending)} 条\n"]
    for item in pending[:10]:
        scope = item.get("scope", "unknown")
        reason = item.get("failure_reason", "")[:80]
        lines.append(f"- [{scope}] {reason}")

    if len(pending) > 10:
        lines.append(f"\n... 还有 {len(pending) - 10} 条")

    return ToolResult(success=True, output="\n".join(lines))
```

- [x] **Step 5: 在 __init__.py 注册工具**

在 `backend/private_agent/tools/builtins/__init__.py` 中添加导入和注册。

- [x] **Step 6: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_evolution_tools.py -v`
Expected: PASS

- [x] **Step 7: 提交**

```bash
git add backend/skills/monitor/system_prompt.md backend/private_agent/tools/builtins/evolution_tools.py backend/private_agent/tools/builtins/__init__.py backend/tests/test_evolution_tools.py
git commit -m "feat(monitor): upgrade main agent to evolution orchestrator with调度 tools"
```

---

### Task 4.2: 场景间产物传递机制

**Files:**
- Create: `backend/private_agent/core/scene_collaboration.py`
- Create: `backend/private_agent/tools/builtins/pass_artifact.py`
- Test: `backend/tests/test_scene_collaboration.py`

**说明：** 解决清和"美化子瞻/白圭产物"的跨场景依赖。V1 不做实时跨窗口对话，而是通过**产物传递 + 异步通知**实现协作：用户在子瞻窗口调用 `pass_artifact` 将产物传递给清和，清和窗口收到通知后可基于该产物执行美化。

- [ ] **Step 1: 编写场景协作的测试**

创建 `backend/tests/test_scene_collaboration.py`：

```python
"""场景间产物传递测试。"""
import pytest
from unittest.mock import AsyncMock

from private_agent.core.scene_collaboration import (
    ArtifactPass, SceneCollaborationManager
)


@pytest.mark.asyncio
async def test_pass_artifact_to_scene(test_db_pool):
    manager = SceneCollaborationManager(test_db_pool)
    pass_id = await manager.pass_artifact(
        from_scope="office",
        to_scope="frontend_design",
        artifact_path="outputs/report.xlsx",
        artifact_summary="子瞻生成的销售数据月度报告",
        from_session_id=1,
    )
    assert pass_id > 0


@pytest.mark.asyncio
async def test_get_pending_artifacts(test_db_pool):
    manager = SceneCollaborationManager(test_db_pool)
    await manager.pass_artifact(
        from_scope="office",
        to_scope="frontend_design",
        artifact_path="outputs/report.xlsx",
        artifact_summary="月度报告",
        from_session_id=1,
    )

    pending = await manager.get_pending("frontend_design")
    assert len(pending) == 1
    assert pending[0].artifact_path == "outputs/report.xlsx"
    assert pending[0].from_scope == "office"


@pytest.mark.asyncio
async def test_mark_consumed(test_db_pool):
    manager = SceneCollaborationManager(test_db_pool)
    pass_id = await manager.pass_artifact(
        from_scope="data_analysis",
        to_scope="frontend_design",
        artifact_path="outputs/analysis.pdf",
        artifact_summary="投资分析报告",
        from_session_id=2,
    )

    await manager.mark_consumed(pass_id)
    pending = await manager.get_pending("frontend_design")
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_cross_scope_isolation(test_db_pool):
    manager = SceneCollaborationManager(test_db_pool)
    await manager.pass_artifact(
        from_scope="office",
        to_scope="frontend_design",
        artifact_path="outputs/a.xlsx",
        artifact_summary="a",
        from_session_id=1,
    )

    # office 不应看到发给 frontend_design 的产物
    pending = await manager.get_pending("office")
    assert len(pending) == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m pytest tests/test_scene_collaboration.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 artifact_passes 表**

在 `backend/private_agent/storage/schema.sql` 追加：

```sql
-- 场景间产物传递表 (Phase 4: 跨场景协作)
CREATE TABLE IF NOT EXISTS artifact_passes (
    id              BIGSERIAL PRIMARY KEY,
    from_scope      VARCHAR(20) NOT NULL,
    to_scope        VARCHAR(20) NOT NULL,
    artifact_path   TEXT NOT NULL,
    artifact_summary TEXT NOT NULL,
    from_session_id BIGINT,
    status          VARCHAR(20) DEFAULT 'pending',  -- pending/consumed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at     TIMESTAMPTZ,
    CONSTRAINT chk_pass_status CHECK (status IN ('pending', 'consumed'))
);
CREATE INDEX IF NOT EXISTS idx_artifact_passes_to ON artifact_passes(to_scope, status) WHERE status = 'pending';
```

在 `backend/private_agent/storage/migrations.py` 添加对应迁移函数并注册。

- [ ] **Step 4: 实现 SceneCollaborationManager**

创建 `backend/private_agent/core/scene_collaboration.py`：

```python
"""场景间协作管理器 - 产物传递与异步通知。

对应参考文档 EvoSkill 三 Agent 分工协作 + SE-Agent 跨轨迹重组。
解决清和"美化子瞻/白圭产物"的跨场景依赖。

V1 设计：不做实时跨窗口对话，通过产物传递 + 异步通知实现协作。
用户在子瞻窗口调用 pass_artifact 工具将产物传递给清和，
清和窗口启动时收到待处理产物列表。
"""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from private_agent.observability.logging import setup_logger

logger = setup_logger(__name__)


@dataclass
class ArtifactPass:
    """产物传递记录。"""
    id: int | None
    from_scope: str
    to_scope: str
    artifact_path: str
    artifact_summary: str
    from_session_id: int | None = None
    status: str = "pending"


class SceneCollaborationManager:
    """场景间产物传递管理器。"""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def pass_artifact(
        self,
        from_scope: str,
        to_scope: str,
        artifact_path: str,
        artifact_summary: str,
        from_session_id: int | None = None,
    ) -> int:
        """传递产物到目标场景。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO artifact_passes
                    (from_scope, to_scope, artifact_path, artifact_summary, from_session_id)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                from_scope, to_scope, artifact_path, artifact_summary, from_session_id,
            )
            pass_id = row["id"]
            logger.info(
                "artifact_passed id=%s from=%s to=%s path=%s",
                pass_id, from_scope, to_scope, artifact_path,
            )
            return pass_id

    async def get_pending(self, to_scope: str) -> list[ArtifactPass]:
        """获取目标场景的待处理产物。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM artifact_passes
                WHERE to_scope = $1 AND status = 'pending'
                ORDER BY created_at ASC
                """,
                to_scope,
            )
            return [self._row_to_pass(r) for r in rows]

    async def mark_consumed(self, pass_id: int) -> None:
        """标记产物已被消费。"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE artifact_passes
                SET status = 'consumed', consumed_at = now()
                WHERE id = $1
                """,
                pass_id,
            )
            logger.info("artifact_consumed id=%s", pass_id)

    @staticmethod
    def _row_to_pass(row: asyncpg.Record) -> ArtifactPass:
        return ArtifactPass(
            id=row["id"],
            from_scope=row["from_scope"],
            to_scope=row["to_scope"],
            artifact_path=row["artifact_path"],
            artifact_summary=row["artifact_summary"],
            from_session_id=row["from_session_id"],
            status=row["status"],
        )
```

- [ ] **Step 5: 实现 pass_artifact 工具**

创建 `backend/private_agent/tools/builtins/pass_artifact.py`：

```python
"""跨场景产物传递工具 - 供场景智能体将产物传递给另一场景。

典型用法：子瞻生成报告后，用户说"让清和美化一下"，
子瞻调用 pass_artifact 将产物路径传递给清和场景。
"""
from __future__ import annotations

from typing import Any

from private_agent.tools.defs import ToolDef, ToolResult

PASS_ARTIFACT_DEF = ToolDef(
    name="pass_artifact",
    description=(
        "将当前产物传递给另一场景处理（如把报告传给清和美化）。"
        "Args: to_scope (str) - 目标场景(office/data_analysis/frontend_design); "
        "artifact_path (str) - 产物文件路径; "
        "artifact_summary (str) - 产物摘要说明"
    ),
    parameters={
        "type": "object",
        "properties": {
            "to_scope": {
                "type": "string",
                "enum": ["office", "data_analysis", "frontend_design"],
            },
            "artifact_path": {"type": "string"},
            "artifact_summary": {"type": "string"},
        },
        "required": ["to_scope", "artifact_path", "artifact_summary"],
    },
    safety_level="safe",
)


async def pass_artifact_handler(args: dict[str, Any], ctx: dict[str, Any]) -> ToolResult:
    """产物传递处理函数。"""
    to_scope = args.get("to_scope")
    artifact_path = args.get("artifact_path", "")
    artifact_summary = args.get("artifact_summary", "")
    from_scope = ctx.get("scope")
    from_session_id = ctx.get("session_id")
    manager = ctx.get("collaboration_manager")

    if manager is None:
        return ToolResult(success=False, output="协作管理器未初始化", error="no_manager")

    if from_scope == to_scope:
        return ToolResult(success=False, output="不能传递给同一场景", error="same_scope")

    pass_id = await manager.pass_artifact(
        from_scope=from_scope,
        to_scope=to_scope,
        artifact_path=artifact_path,
        artifact_summary=artifact_summary,
        from_session_id=from_session_id,
    )

    scope_names = {
        "office": "子瞻",
        "data_analysis": "白圭",
        "frontend_design": "清和",
    }
    to_name = scope_names.get(to_scope, to_scope)
    return ToolResult(
        success=True,
        output=f"已将产物传递给{to_name}场景（传递 id={pass_id}）。"
               f"切换到{to_name}窗口即可查看并处理。",
    )
```

- [ ] **Step 6: 在 __init__.py 注册 pass_artifact 工具**

- [ ] **Step 7: 运行测试确认通过**

Run: `cd backend && python -m pytest tests/test_scene_collaboration.py -v`
Expected: PASS（4 个测试全过）

- [ ] **Step 8: 提交**

```bash
git add backend/private_agent/storage/schema.sql backend/private_agent/storage/migrations.py backend/private_agent/core/scene_collaboration.py backend/private_agent/tools/builtins/pass_artifact.py backend/private_agent/tools/builtins/__init__.py backend/tests/test_scene_collaboration.py
git commit -m "feat(collaboration): add cross-scene artifact passing mechanism"
```

---

### Task 4.3: 主程序装配与端到端验证

**Files:**
- Modify: `backend/private_agent/main.py`
- Test: `backend/tests/test_evolution_e2e.py`

- [x] **Step 1: 编写端到端测试**

创建 `backend/tests/test_evolution_e2e.py`：

```python
"""自进化闭环端到端测试。"""
import pytest


@pytest.mark.asyncio
async def test_reflection_to_lesson_to_injection_e2e(test_db_pool):
    """完整链路：任务完成 → 反思 → 经验入库 → 下次任务注入经验。"""
    from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson
    from private_agent.core.context_manager import ContextManager

    # 1. 模拟反思产出的经验入库
    repo = EvolutionRepo(test_db_pool)
    lesson_id = await repo.add(SkillLesson(
        scope="office",
        task_summary="清洗销售数据",
        lesson_type="success",
        lesson_content="先用 df.dtypes 检查列类型，日期列用 pd.to_datetime(errors='coerce')",
        tool_chain=["code_execution"],
        importance=0.8,
    ))
    assert lesson_id > 0

    # 2. 模拟下次任务时经验被检索到
    results = await repo.search_by_keyword("清洗", scope="office")
    assert len(results) == 1
    assert "dtypes" in results[0].lesson_content


@pytest.mark.asyncio
async def test_failure_to_review_queue_e2e(test_db_pool):
    """完整链路：在线失败 → 采集 → 审核队列 → 入库评估集。"""
    from private_agent.eval.online_failure_collector import (
        OnlineFailureCollector, FailureType,
    )
    from private_agent.eval.repos import ReviewQueueRepo
    import tempfile, os

    queue_file = tempfile.mktemp(suffix=".json")
    try:
        repo = ReviewQueueRepo(queue_file)
        collector = OnlineFailureCollector(repo)

        # 1. 采集失败
        item_id = await collector.collect(
            session_id=1, scope="office",
            user_message="读取文件",
            failure_type=FailureType.TOOL_ERROR,
            failure_detail="file_read 超时",
            react_events=[], final_output="错误",
        )
        assert item_id is not None

        # 2. 审核队列中可见
        pending = await repo.list_pending()
        assert len(pending) == 1
        assert "tool_error" in pending[0]["failure_reason"]
    finally:
        if os.path.exists(queue_file):
            os.unlink(queue_file)


@pytest.mark.asyncio
async def test_cross_scene_artifact_pass_e2e(test_db_pool):
    """完整链路：子瞻产物 → 传递 → 清和接收。"""
    from private_agent.core.scene_collaboration import SceneCollaborationManager

    manager = SceneCollaborationManager(test_db_pool)

    # 1. 子瞻传递产物给清和
    pass_id = await manager.pass_artifact(
        from_scope="office", to_scope="frontend_design",
        artifact_path="outputs/report.xlsx",
        artifact_summary="月度销售报告",
        from_session_id=1,
    )
    assert pass_id > 0

    # 2. 清和收到待处理产物
    pending = await manager.get_pending("frontend_design")
    assert len(pending) == 1
    assert pending[0].artifact_summary == "月度销售报告"

    # 3. 清和处理后标记消费
    await manager.mark_consumed(pass_id)
    pending_after = await manager.get_pending("frontend_design")
    assert len(pending_after) == 0
```

- [x] **Step 2: 在 main.py 装配新组件**

在 `backend/private_agent/main.py` 的应用启动初始化中，装配新组件：

1. 初始化 `EvolutionRepo`（传入 db_pool）
2. 初始化 `SceneCollaborationManager`（传入 db_pool）
3. 初始化 `OnlineFailureCollector`（传入 ReviewQueueRepo）
4. 初始化 `ReflectionEngine`（传入 adapter）
5. 在创建 ReactLoop 实例时传入 `reflection_engine`、`evolution_repo`、`failure_collector`
6. 在工具注册时，将 `evolution_repo` / `collaboration_manager` / `review_queue_repo` 注入到工具的 `ctx` 中

遵循 `main.py` 中已有的装配模式（如 MemoryManager、ContextManager 的初始化方式）。

- [x] **Step 3: 运行端到端测试**

Run: `cd backend && python -m pytest tests/test_evolution_e2e.py -v`
Expected: PASS（3 个测试全过）

- [x] **Step 4: 全量回归测试**

Run: `cd backend && python -m pytest --ignore=tests/test_eval_full_cycle.py -v`
Expected: 现有测试零回归

- [x] **Step 5: 提交**

```bash
git add backend/private_agent/main.py backend/tests/test_evolution_e2e.py
git commit -m "feat(main): wire up evolution components and verify e2e"
```

---

## 自审清单

### Spec 覆盖检查

| 分析问题 | 对应 Task | 覆盖 |
|---------|----------|------|
| 问题1：主智能体职责过窄 | Task 4.1（升级为无涯·项目进化者，具备代码能力） | ✅ 双轨后强化 |
| 问题2：场景职责与技术标识错位 | 未改标识（兼容考虑），通过 system_prompt 强化职责边界 | ⚠️ 标识保留，职责澄清 |
| 问题3：能力重叠与边界模糊 | system_prompt 强化 + 双轨隔离（无涯有代码工具，领域智能体无） | ✅ 双轨后清晰 |
| 问题4：记忆系统缺经验维度 | Task 1.1（skill_lessons 表 + lesson_category 双轨字段）+ Task 1.2 | ✅ 双轨后更完整 |
| 问题5：ReactLoop 缺反思环节 | Task 1.3（REFLECTION 状态 + 双轨反思模板） | ✅ 双轨后清晰 |
| 问题6：场景间无协作机制 | 删除 Task 4.2（延后 V2，单人场景过度设计） | ✅ 瘦身后删除 |
| 问题7：在线对话与评估闭环脱节 | Task 3.1-3.2（失败采集；用户纠正延后 V2） | ✅ 瘦身后聚焦 |
| 问题8：主智能体注入挤占 | Task 4.1（无涯注入 project_evolution 经验，与领域隔离） | ✅ 双轨后隔离 |
| **新增问题9：主智能体缺代码能力**（用户 2026-08-11 澄清） | Task 4.1（无涯获得 file_read/file_write/code_execution/search_code） | ✅ 双轨后落地 |
| **新增问题10：双轨进化未隔离**（用户澄清后浮现） | Task 1.1（lesson_category 字段 + DB CHECK 约束）+ Task 1.2（双轨反思模板）+ Task 2.1（双轨注入隔离） | ✅ 全链路落地 |

### 双轨进化覆盖检查（2026-08-11 用户澄清后新增）

| 智能体 | 进化焦点 | 经验类型 | 反思模板 | 工具权限 | 对应 Task |
|--------|---------|---------|----------|---------|----------|
| 无涯（monitor） | 项目级进化（代码/架构/Skill prompt/性能/Bug） | project_evolution | PROJECT_EVOLUTION_REFLECTION_PROMPT | 代码工具（file_read/write/code_execution/search_code）+ 进化调度工具 | Task 4.1 |
| 子瞻（office） | 工作学习专业技巧（pandas/Word/网页研究） | domain_skill | DOMAIN_REFLECTION_PROMPT | 领域工具（无代码工具） | Task 1.1-1.4 |
| 白圭（data_analysis） | 投资理财分析方法（估值/风险/宏观） | domain_skill | DOMAIN_REFLECTION_PROMPT | 领域工具（无代码工具） | Task 1.1-1.4 |
| 清和（frontend_design） | 生活健康+美学设计技巧 | domain_skill | DOMAIN_REFLECTION_PROMPT | 领域工具（无代码工具） | Task 1.1-1.4 |

### 参考文档路线对应

| 参考文档路线 | 本方案落地 |
|-------------|-----------|
| 第一类：经验/Skill 存储型 | skill_lessons 表（双轨 lesson_category）+ EvolutionRepo（Add/Merge/Discard） |
| EvoSkill 三 Agent 分工 | ReactLoop 反思（Proposer，双轨模板）+ 失败采集（Executor）+ 经验入库（SkillBuilder） |
| AutoSkill 双环结构 | 在线用（search_lessons + 双轨注入）+ 离线进化（Merge/Discard） |
| CoEvoSkills Add/Merge/Discard | EvolutionRepo.merge / discard |
| SkillOS Curator（训练总结者） | ReflectionEngine 双轨总结 prompt（非权重训练，适配单人桌面场景） |
| SE-Agent 多轨迹反思 | 反思环节从单轮轨迹提炼（多轨迹融合列为 V2） |
| 论文 §06 总结者被低估 | ReflectionEngine 作为独立模块，**双轨 prompt 精心设计**（领域技巧 vs 项目进化） |
| EvoSkill Executor 失败记录 | OnlineFailureCollector |

---

## 实施顺序总结

```
Phase 1（反思+经验存储）→ Phase 2（经验进化机制）→ Phase 3（失败采集+评估打通）→ Phase 4（进化调度+场景协作）
```

每个 Phase 独立可发布、可测试。建议按 Phase 拆分为 4 次执行会话，每次完成一个 Phase 并提交后再进入下一个。

---

## 实施前修订建议（2026-08-11 自审补充）

> 本节为计划编写完成后的"瘦身审视"，记录实施前应调整的设计点，避免过度加重项目。

### 修订 1：反思触发分级 + 异步化（影响 Task 1.3）

**原设计问题：** 每轮任务完成都触发反思 = 双倍 LLM 调用成本，对单人桌面 Agent 付费场景过重。

**修订：**
- 反思触发分级：
  - 失败任务（had_error=True）→ **必反思**
  - 成功任务 → 仅当 `turn >= 3` 或工具调用数 >= 3 时反思
- 反思异步化：不阻塞 final 事件推送，任务返回用户后后台执行反思
- config.yaml 增加配置：
  ```yaml
  evolution:
    reflection:
      enabled: true              # 全局开关
      min_turns: 3               # 成功任务反思的最小轮次
      min_tools: 3               # 成功任务反思的最小工具数
      async: true                # 异步执行（不阻塞用户响应）
  ```

### 修订 2：删除 Task 4.2 场景协作（延后 V2）

**原设计问题：** artifact_passes 表 + SceneCollaborationManager + pass_artifact 工具是过度设计。单人桌面场景下手动切换窗口粘贴文件路径成本很低，引入新表+新工具+新协作流程加重维护负担。

**修订：**
- **V1 删除 Task 4.2**，Phase 4 仅保留 Task 4.1（主智能体升级）+ Task 4.3（端到端验证）
- 删除 `artifact_passes` 表、`SceneCollaborationManager`、`pass_artifact` 工具的设计
- Task 4.3 端到端测试中删除 `test_cross_scene_artifact_pass_e2e`
- 场景协作延后到 V2，等用户实际使用反馈后再决定是否需要

### 修订 3：用户纠正检测延后 V2（影响 Task 3.3）

**原设计问题：** 关键词匹配（"不对"/"错了"/"应该是"）误报率高：
- "不对，这样很好" 是肯定而非纠正
- "应该是这样的" 是确认而非纠正
- "我要的是月度汇总" 在很多场景下是描述需求而非纠正

**修订：**
- **V1 删除 Task 3.3**，Phase 3 仅保留 Task 3.1（失败采集器）+ Task 3.2（ReactLoop 集成）
- V1 失败采集仅覆盖明确信号：`TOOL_ERROR` / `ITERATION_EXHAUSTED` / `PROVIDER_ERROR`
- `USER_CORRECTION` 类型延后到 V2，V2 用 LLM 判断纠正意图（非关键词）

### 修订 4：经验注入明确 token 预算（影响 Task 2.1）

**原设计问题：** 计划说"受注入预算控制"但未给出具体数字，可能挤占 Stable Zone。

**修订：**
- 经验注入上限：**最多 3 条**，按 `importance` 降序选 top-3
- 总 token ≤ 500（用 TokenEstimator 预估，超限则降级为 top-2 / top-1）
- config.yaml 增加配置：
  ```yaml
  evolution:
    injection:
      max_lessons: 3
      max_tokens: 500
  ```

### 修订 5：补全 Placeholder（影响多个 Task）

**原设计问题：** 以下位置违反 writing-plans 的"No Placeholders"原则：
- Task 1.3 Step 4："遵循文件中已有的 ReactLoop fixture 模式"——未给具体代码
- Task 2.2 Step 3："从 app state 获取"——未给 `get_evolution_repo()` 实现
- Task 4.3 Step 2："遵循 main.py 中已有的装配模式"——未给具体代码

**修订：** 执行每个 Task 前必须先读对应文件（`conftest.py` / `admin.py` / `main.py`），将"遵循已有模式"替换为具体代码后再进入实现步骤。

### 修订 6：scope CHECK 约束的 V2 演进路径

**原设计问题：** `skill_lessons.scope` CHECK 约束写死枚举，未来新增场景需改 schema。

**修订：**
- V1 保持 CHECK 约束（与 project_memory 中"评估数据集 CHECK 约束"惯例一致）
- 在 schema.sql 注释中明确：`-- V2: 考虑改为引用 skills.scope 的外键约束`

### 修订后的 Phase 范围

| Phase | 保留 Task | 删除 Task | 备注 |
|-------|----------|----------|------|
| Phase 1 | 1.1, 1.2, 1.3, 1.4 | — | Task 1.3 加分级触发 + 异步化 |
| Phase 2 | 2.1, 2.2 | — | Task 2.1 明确 token 预算 |
| Phase 3 | 3.1, 3.2 | 3.3 | 用户纠正延后 V2 |
| Phase 4 | 4.1, 4.3 | 4.2 | 场景协作延后 V2；Task 4.3 删除跨场景测试 |

**净效果：** 删除 2 个 Task，减少 1 张表（artifact_passes）、1 个管理器（SceneCollaborationManager）、1 个工具（pass_artifact）、1 套纠正检测逻辑。预计减少 ~30% 实施工作量，聚焦自进化闭环核心价值。

---

## 双轨进化修订汇总（2026-08-11 用户澄清后补充）

> 用户 2026-08-11 澄清：主智能体"无涯"应具备代码能力，专注项目级进化；专业智能体（子瞻/白圭/清和）专注自身专业深度进化。此节汇总双轨进化的设计修订。

### 修订 7：双轨进化机制（核心架构调整）

**触发原因：** 用户澄清前，原计划把主智能体定位为"进化调度者"（只读工具，无代码能力）。这与用户期望"无涯应当具备较强的代码能力，重点关注整个项目的进化"根本冲突。

**修订内容：**

| 层 | 原设计 | 双轨后 |
|----|--------|--------|
| **数据层**（Task 1.1） | skill_lessons 表无 lesson_category | 新增 `lesson_category` 字段 + DB CHECK 约束（scope 与 category 一致性） |
| **反思层**（Task 1.2） | 单一 REFLECTION_PROMPT_TEMPLATE | 双轨模板：DOMAIN_REFLECTION_PROMPT（领域技巧）+ PROJECT_EVOLUTION_REFLECTION_PROMPT（项目进化） |
| **集成层**（Task 1.3） | _maybe_reflect 不传 lesson_category | 传入 lesson_category，EvolutionRepo.add 校验 scope-category 一致性 |
| **注入层**（Task 2.1） | 按.scope 注入，无 category 隔离 | 按 scope 推断 category，双轨隔离注入（无涯只注入 project_evolution） |
| **角色层**（Task 4.1） | "进化调度者"（只读工具） | "无涯·项目进化者"（获得 file_read/write/code_execution/search_code 代码工具） |

### 修订 8：无涯工具权限边界（新增代码工具）

**原设计：** monitor 仅 `system_metrics_query` / `system_status` / `optim_plan` / `apply_optim`，明确"禁止直接修改代码文件"。

**双轨后：** 无涯获得代码工具权限，但通过审批流约束：

| 工具类别 | 工具 | 权限级别 | 约束 |
|---------|------|---------|------|
| 监控（只读） | system_metrics_query / system_status / optim_plan | readonly | 随时可调用 |
| 代码（只读） | file_read / search_code | readonly | 随时可调用 |
| 代码（elevated） | code_execution / file_write | elevated | **必须 evolution_proposal → 用户 approved → apply_evolution**；file_write 前必须备份 |
| 进化调度 | lessons_stats / review_queue_summary / evolution_proposal / apply_evolution | readonly/elevated | evolution_proposal 需审批，apply_evolution 执行已 approved |

### 修订 9：双轨隔离原则

**经验隔离：** skill_lessons 中 domain_skill 经验只注入对应 scope 的会话；project_evolution 经验只注入无涯会话（避免领域智能体被项目进化经验污染，反之亦然）。

**工具隔离：** 领域智能体（子瞻/白圭/清和）无代码工具（保持专业聚焦）；无涯有代码工具（专注项目进化）。

**反思隔离：** 领域智能体反思专业技巧（DOMAIN_REFLECTION_PROMPT）；无涯反思代码/架构模式（PROJECT_EVOLUTION_REFLECTION_PROMPT）。

### 双轨进化的实施影响

| 影响项 | 变更 |
|--------|------|
| skill_lessons 表 | +1 字段（lesson_category）+ 1 CHECK 约束 |
| EvolutionRepo | +1 字段 + 应用层一致性校验 |
| ReflectionEngine | 双轨模板选择 + ReflectionResult.lesson_category |
| ReactLoop._maybe_reflect | 传入 lesson_category |
| ContextManager._build_lessons_injection | 按 category 隔离注入 + token 预算 |
| monitor system_prompt | 从"监控者"升级为"无涯·项目进化者"，增加代码工具权限 |
| 工具集 | 无涯获得 file_read/search_code（readonly）+ file_write/code_execution（elevated，审批后） |

### 双轨进化的工程价值

1. **职责清晰**：无涯专注项目进化（代码/架构），领域智能体专注专业深度（技巧/方法论）
2. **经验隔离**：避免项目进化经验污染领域技巧沉淀，反之亦然
3. **工具聚焦**：领域智能体不被代码工具分心，无涯有足够工具执行项目改进
4. **审批闸门**：无涯代码改动必须经 evolution_proposal → 用户 approved → apply_evolution，防止擅自修改
5. **YAGNI 兼容**：单人桌面场景下，无涯的代码能力用于自修复/自优化，不引入过度自动化

