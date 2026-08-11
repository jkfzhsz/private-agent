"""search_lessons 内置工具:经验按需检索。

对应参考文档 AutoSkill 的"在线服务（用 Skill）"环节:
查询 → 检索历史经验 → 注入生成。

注入策略: 历史经验不常驻 Stable Zone, 由模型在任务相关时主动调用
本工具检索召回(与 memory_search 同模式)。

双轨(2026-08-11): scope 限定决定检索域。
- 领域智能体(office/data_analysis/frontend_design) → domain_skill 经验
- 无涯(monitor) → project_evolution 经验
"""
from __future__ import annotations

from private_agent.config import loader
from private_agent.skills.evolution_repo import EvolutionRepo
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["SEARCH_LESSONS_TOOL"]


async def _search_lessons_handler(args: dict) -> ToolResult:
    """检索历史经验并返回格式化结果。

    Args:
        args: keyword(必选), scope(可选限定检索域)。

    Returns:
        格式化后的经验检索结果文本。
    """
    keyword = (args.get("keyword") or "").strip()
    if not keyword:
        return ToolResult(output="", error="No keyword provided")

    scope = args.get("scope") or None

    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Database connection failed: {type(e).__name__}: {e}",
        )

    try:
        repo = EvolutionRepo(conn)
        lessons = await repo.search_by_keyword(keyword=keyword, scope=scope, limit=5)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Lesson search failed: {type(e).__name__}: {e}",
        )
    finally:
        await conn.close()

    if not lessons:
        return ToolResult(output="无相关经验记录。基于模型自身能力处理任务。")

    lines = [f"找到 {len(lessons)} 条相关经验：\n"]
    for i, lesson in enumerate(lessons, 1):
        lines.append(f"## 经验 {i} [{lesson.lesson_type}]")
        lines.append(f"任务: {lesson.task_summary}")
        lines.append(f"内容: {lesson.lesson_content}")
        if lesson.tool_chain:
            lines.append(f"工具链: {' → '.join(lesson.tool_chain)}")
        lines.append(f"重要性: {lesson.importance:.1f}\n")

    return ToolResult(output="\n".join(lines))


SEARCH_LESSONS_TOOL = ToolDef(
    name="search_lessons",
    description=(
        "检索历史任务经验。当遇到类似任务时，先检索是否有可复用的成功模式"
        "或失败教训。Args: keyword (str, required) - 检索关键词; "
        "scope (str, optional) - 场景限定"
        "(office/data_analysis/frontend_design/monitor/global)"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "检索关键词",
            },
            "scope": {
                "type": "string",
                "description": "场景限定(office/data_analysis/frontend_design/monitor/global)",
            },
        },
        "required": ["keyword"],
    },
    handler=_search_lessons_handler,
    safety_level="readonly",
    risk_level="low",
)
