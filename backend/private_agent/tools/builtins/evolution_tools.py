"""Phase 4 Task 4.1 - 进化调度工具(无涯·项目进化者专属)。

对应参考文档:
- AutoSkill 右环(技能进化循环): 经验库统计驱动 Merge/Discard 决策
- SkillOS Curator: 管理经验库的增/改/删
- AgentEvolver: 从失败案例识别评估集覆盖空白

工具清单:
- lessons_stats: 查看各场景经验库统计(只读)
- review_queue_summary: 查看评估队列待审核失败案例摘要(只读)

遵循项目工具模式: handler 自建 DB/文件连接(search_lessons/monitor_tools 模式),
无 ctx 依赖 —— 测试通过 PA_DB_* 环境变量指向测试库。
"""
from __future__ import annotations

import os
from typing import Any

from private_agent.config import loader
from private_agent.eval.repos import ReviewQueueRepo
from private_agent.skills.evolution_repo import EvolutionRepo
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = [
    "LESSONS_STATS_DEF",
    "REVIEW_QUEUE_SUMMARY_DEF",
    "EVOLUTION_TOOLS",
    "_lessons_stats_handler",
    "_review_queue_summary_handler",
    "_build_review_queue_repo",
]

# 经验库统计的 scope 列表(与 _SCOPE_CATEGORY_MAP 对齐)
_SCENES = ["office", "data_analysis", "frontend_design", "monitor", "global"]


def _build_review_queue_repo(cfg: dict[str, Any]) -> ReviewQueueRepo:
    """构造 ReviewQueueRepo(同 api/eval.py 模式, 测试可 monkeypatch)。

    queue_file 路径: {workspace_root}/.eval_review_queue.json。
    """
    workspace_root = cfg.get("system", {}).get("workspace_root", ".")
    queue_file = os.path.join(workspace_root, ".eval_review_queue.json")
    return ReviewQueueRepo(queue_file=queue_file)


async def _lessons_stats_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """查看各场景经验库统计(经验数/类型分布)。

    用于无涯分析经验库健康度, 识别可合并/应淘汰的经验。
    """
    conn = None
    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
        repo = EvolutionRepo(conn)
        lines = ["经验库统计：\n"]
        total = 0
        for scope in _SCENES:
            lessons = await repo.search_by_scope(scope, limit=100)
            success_count = sum(1 for l in lessons if l.lesson_type == "success")
            failure_count = sum(1 for l in lessons if l.lesson_type == "failure")
            correction_count = sum(1 for l in lessons if l.lesson_type == "correction")
            total += len(lessons)
            lines.append(
                f"- {scope}: {len(lessons)} 条 "
                f"(成功 {success_count} / 失败 {failure_count} / 纠正 {correction_count})"
            )
        lines.append(f"\n总计: {total} 条")
        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"lessons_stats failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


async def _review_queue_summary_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """查看评估队列待审核失败案例摘要。

    用于无涯识别系统性弱点, 驱动评估集扩充与 Bug 修复。
    """
    try:
        cfg = loader.load_config()
        repo = _build_review_queue_repo(cfg)
        pending = await repo.list_pending(limit=50)
        if not pending:
            return ToolResult(output="审核队列为空，无待处理失败案例。")
        lines = [f"待审核失败案例：{len(pending)} 条\n"]
        for item in pending[:10]:
            scope = item.get("scope", "unknown")
            reason = item.get("failure_reason", "")[:80]
            lines.append(f"- [{scope}] {reason}")
        if len(pending) > 10:
            lines.append(f"\n... 还有 {len(pending) - 10} 条")
        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"review_queue_summary failed: {e}")


LESSONS_STATS_DEF = ToolDef(
    name="lessons_stats",
    description=(
        "查看各场景经验库统计(经验数/类型分布)。"
        "用于无涯分析经验库健康度, 识别可合并/应淘汰的经验。"
    ),
    parameters_schema={"type": "object", "properties": {}},
    handler=_lessons_stats_handler,
    is_kernel=False,
    safety_level="readonly",
    risk_level="low",
)

REVIEW_QUEUE_SUMMARY_DEF = ToolDef(
    name="review_queue_summary",
    description=(
        "查看评估队列待审核失败案例摘要。"
        "用于识别系统性弱点, 驱动评估集扩充与 Bug 修复。"
    ),
    parameters_schema={"type": "object", "properties": {}},
    handler=_review_queue_summary_handler,
    is_kernel=False,
    safety_level="readonly",
    risk_level="low",
)

# 进化调度工具集(monitor 会话专属白名单, 与 MONITOR_TOOLS 同模式装配)
EVOLUTION_TOOLS: list[ToolDef] = [
    LESSONS_STATS_DEF,
    REVIEW_QUEUE_SUMMARY_DEF,
]
