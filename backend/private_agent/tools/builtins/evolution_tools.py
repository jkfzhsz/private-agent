"""Phase 4 Task 4.1 - 进化调度工具(无涯·项目进化者专属)。

对应参考文档:
- AutoSkill 右环(技能进化循环): 经验库统计驱动 Merge/Discard 决策
- SkillOS Curator: 管理经验库的增/改/删
- AgentEvolver: 从失败案例识别评估集覆盖空白

工具清单:
- lessons_stats: 查看各场景经验库统计(只读)
- review_queue_summary: 查看评估队列待审核失败案例摘要(只读)
- lessons_add: 主动沉淀进化经验/教训(safe, 落 skill_lessons + 引导 mempalace 双写)
- 经验检索: 复用通用 search_lessons(已增强: keyword 可空, 支持仅 scope 列出)

遵循项目工具模式: handler 自建 DB/文件连接(search_lessons/monitor_tools 模式),
无 ctx 依赖 —— 测试通过 PA_DB_* 环境变量指向测试库。
"""
from __future__ import annotations

import os
from typing import Any

from private_agent.config import loader
from private_agent.eval.repos import ReviewQueueRepo
from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = [
    "LESSONS_STATS_DEF",
    "REVIEW_QUEUE_SUMMARY_DEF",
    "LESSONS_ADD_DEF",
    "EVOLUTION_TOOLS",
    "_lessons_stats_handler",
    "_review_queue_summary_handler",
    "_lessons_add_handler",
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


# ──────────────────────────────────────────────────────────────────────────────
# 阶段5(2026-08-16): 进化沉淀 —— 主动经验写入 + 检索(能力域⑤ 从只读升级为闭环)
# ──────────────────────────────────────────────────────────────────────────────

# lesson_type 合法值(与 schema.sql CHECK 约束一致)
_VALID_LESSON_TYPES = ("success", "failure", "correction")
# scope 合法值(与 EvolutionRepo._SCOPE_CATEGORY_MAP 键一致)
_VALID_SCOPES = ("office", "data_analysis", "frontend_design", "monitor", "global")


def _category_for_scope(scope: str) -> str:
    """scope → 默认 lesson_category(与 EvolutionRepo._SCOPE_CATEGORY_MAP 同源)。"""
    return {
        "monitor": "project_evolution",
        "office": "domain_skill",
        "data_analysis": "domain_skill",
        "frontend_design": "domain_skill",
        "global": "cross_domain",
    }.get(scope, "domain_skill")


async def _lessons_add_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """主动沉淀进化经验/教训(阶段5 进化沉淀闭环入口)。

    - scope: 场景(office/data_analysis/frontend_design/monitor/global)
    - lesson_type: success/failure/correction
    - lesson_category: 默认按 scope 映射(monitor→project_evolution 等)
    - importance: 0-1(默认 0.5)
    - tool_chain: 可选, 涉及的工具调用链
    落库 skill_lessons 后提示可用 mempalace_add_drawer 双写教训规则。
    """
    scope = str(args.get("scope") or "").strip()
    lesson_type = str(args.get("lesson_type") or "").strip()
    lesson_content = str(args.get("lesson_content") or "").strip()
    task_summary = str(args.get("task_summary") or "").strip()
    if not scope or not lesson_type or not lesson_content:
        return ToolResult(
            output="", error="scope/lesson_type/lesson_content 均为必填",
        )
    if scope not in _VALID_SCOPES:
        return ToolResult(
            output="",
            error=f"scope 非法: {scope}(可选 {', '.join(_VALID_SCOPES)})",
        )
    if lesson_type not in _VALID_LESSON_TYPES:
        return ToolResult(
            output="",
            error=f"lesson_type 非法: {lesson_type}"
            f"(可选 {', '.join(_VALID_LESSON_TYPES)})",
        )
    if not task_summary:
        task_summary = lesson_content[:60]

    lesson_category = str(args.get("lesson_category") or "").strip() or _category_for_scope(scope)
    if lesson_category != _category_for_scope(scope):
        return ToolResult(
            output="",
            error=f"scope={scope} 要求 lesson_category={_category_for_scope(scope)}, "
            f"got {lesson_category}",
        )
    try:
        importance = float(args.get("importance") or 0.5)
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    raw_chain = args.get("tool_chain") or []
    tool_chain = [str(t) for t in raw_chain] if isinstance(raw_chain, list) else []

    conn = None
    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
        repo = EvolutionRepo(conn)
        lesson_id = await repo.add(SkillLesson(
            scope=scope,
            lesson_category=lesson_category,
            task_summary=task_summary,
            lesson_type=lesson_type,
            lesson_content=lesson_content,
            tool_chain=tool_chain,
            importance=importance,
        ))
        lines = [
            f"经验已沉淀 ✅ (id={lesson_id})",
            f"- 场景: {scope} / 类型: {lesson_type} / 重要度: {importance}",
            f"- 摘要: {task_summary}",
            f"- 内容: {lesson_content[:200]}",
            "",
            "建议双写: 用 mempalace_add_drawer 把该教训写入记忆宫殿"
            "(wing=private_agent, 便于跨会话检索)。",
        ]
        return ToolResult(output="\n".join(lines))
    except ValueError as e:
        return ToolResult(output="", error=f"经验校验失败: {e}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"lessons_add failed: {type(e).__name__}: {e}")
    finally:
        if conn is not None:
            await conn.close()


LESSONS_ADD_DEF = ToolDef(
    name="lessons_add",
    description=(
        "主动沉淀进化经验/教训到经验库(skill_lessons)。"
        "scope=场景(office/data_analysis/frontend_design/monitor/global), "
        "lesson_type=success/failure/correction, lesson_content=经验内容必填, "
        "task_summary=摘要(缺省取内容前60字), importance=重要度0-1(默认0.5)。"
        "沉淀后建议用 mempalace_add_drawer 双写记忆宫殿。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "description": "场景: office/data_analysis/frontend_design/monitor/global"},
            "lesson_type": {"type": "string", "description": "success/failure/correction"},
            "lesson_content": {"type": "string", "description": "经验/教训内容(必填)"},
            "task_summary": {"type": "string", "description": "任务摘要(缺省取内容前60字)"},
            "importance": {"type": "number", "description": "重要度 0-1, 默认 0.5"},
            "tool_chain": {"type": "array", "items": {"type": "string"}, "description": "涉及的工具调用链(可选)"},
        },
        "required": ["scope", "lesson_type", "lesson_content"],
    },
    handler=_lessons_add_handler,
    is_kernel=False,
    safety_level="full",
    risk_level="low",
)

# 进化调度工具集(monitor 会话专属白名单, 与 MONITOR_TOOLS 同模式装配)
# 经验检索复用通用 search_lessons(已增强 keyword 可空), 避免工具重复
EVOLUTION_TOOLS: list[ToolDef] = [
    LESSONS_STATS_DEF,
    REVIEW_QUEUE_SUMMARY_DEF,
    LESSONS_ADD_DEF,
]
