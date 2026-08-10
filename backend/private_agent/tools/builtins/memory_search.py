"""memory_search 内置工具:0.5.0 M1 用户记忆按需检索。

注入策略(用户确认): 全局只注入"身份+核心偏好"画像(1-3 条), 其余配额
给场景记忆; 全局记忆的其他内容(进行中项目概况、历史偏好细节)不常驻,
由模型在任务相关时主动调用本工具检索召回。

检索范围:
- user_memories 活跃记忆(content ILIKE 关键词匹配);
- 可选 scope 过滤(global/office/data_analysis/frontend_design);
- 可选含归档(user_memories_archive, 0.5.0 M3 B3 巩固归档召回)。
"""
from __future__ import annotations

from private_agent.config import loader
from private_agent.memory.memories_repo import MemoriesRepo
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["MEMORY_SEARCH_TOOL"]


async def _memory_search_handler(args: dict) -> ToolResult:
    """检索用户记忆并返回格式化结果。

    Args:
        args: query(必选), scope(可选), include_archived(可选, 默认 false),
              top_k(可选, 默认 5, 上限 20)。

    Returns:
        格式化后的记忆检索结果文本。
    """
    query = (args.get("query") or "").strip()
    if not query:
        return ToolResult(output="", error="No query provided")

    scope = args.get("scope") or None
    include_archived = bool(args.get("include_archived", False))
    try:
        top_k = int(args.get("top_k", 5))
    except (TypeError, ValueError):
        top_k = 5
    top_k = min(max(top_k, 1), 20)

    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Database connection failed: {type(e).__name__}: {e}",
        )

    try:
        repo = MemoriesRepo(conn)
        memories = await repo.get_top_active(
            limit=top_k * 3,  # 宽取再做关键词过滤
            scope=scope,
        )
        hits = [m for m in memories if query.lower() in (m.content or "").lower()]
        archived: list[dict] = []
        if include_archived:
            archived = await repo.search_archived(query, limit=top_k)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Memory search failed: {type(e).__name__}: {e}",
        )
    finally:
        await conn.close()

    lines: list[str] = []
    if hits:
        lines.append(f"Found {len(hits)} active memory (memories):")
        for i, m in enumerate(hits[:top_k], 1):
            scope_tag = f"@{m.scope}" if m.scope != "global" else ""
            lines.append(f"{i}. [{m.type}{scope_tag}] {m.content[:300]}")
    if archived:
        lines.append(
            f"Found {len(archived)} archived memory (consolidated):"
        )
        for i, a in enumerate(archived[: max(0, top_k - len(hits))], 1):
            scope_tag = f"@{a['scope']}" if a["scope"] != "global" else ""
            lines.append(
                f"{i}. [{a['type']}{scope_tag}] {a['summary'][:300]}"
            )
    if not lines:
        return ToolResult(
            output=(
                f"No memories found for query: '{query}'"
                f"{f' (scope={scope})' if scope else ''}."
            )
        )
    return ToolResult(output="\n".join(lines))


MEMORY_SEARCH_TOOL = ToolDef(
    name="memory_search",
    description=(
        "Search the user's long-term memories (preferences, facts, projects, "
        "decisions). Use this when you need to recall what the user said or "
        "decided before — especially global context not injected by default "
        "(ongoing projects, historical preferences). Optional scope filter "
        "(global/office/data_analysis/frontend_design); include_archived=true "
        "also searches consolidated/archived memories."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The memory search keyword.",
            },
            "scope": {
                "type": "string",
                "description": (
                    "Optional scope filter: 'global' (cross-scene), 'office' "
                    "(子瞻 work/study), 'data_analysis' (白圭 investing), "
                    "'frontend_design' (清和 health/design). Omit to search all."
                ),
            },
            "include_archived": {
                "type": "boolean",
                "description": (
                    "Whether to also search consolidated/archived memories "
                    "(default false)."
                ),
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default 5, max 20).",
            },
        },
        "required": ["query"],
    },
    handler=_memory_search_handler,
)
