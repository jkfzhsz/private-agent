"""search_knowledge 内置工具:Agentic RAG 知识库检索。

通过 KnowledgeBaseService 对知识库执行混合检索(向量 + 关键词 + reranker 精排),
返回格式化后的知识片段供 LLM 参考。
"""
from __future__ import annotations

from private_agent.config import loader
from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.knowledge.kb_service import KnowledgeBaseService
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["SEARCH_KNOWLEDGE_TOOL"]


async def _search_knowledge_handler(args: dict) -> ToolResult:
    """执行知识库检索并返回格式化结果。

    Args:
        args: 包含 query(必选),scenario(可选),top_k(可选)的 dict。

    Returns:
        格式化后的检索结果文本。
    """
    query = args.get("query", "")
    if not query:
        return ToolResult(output="", error="No query provided")

    scenario = args.get("scenario")
    top_k = args.get("top_k", 5)
    if not isinstance(top_k, int) or top_k < 1:
        top_k = 5
    top_k = min(top_k, 20)

    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Database connection failed: {type(e).__name__}: {e}",
        )

    try:
        repo = KnowledgeBaseRepo(conn)
        svc = KnowledgeBaseService(kb_repo=repo)
        chunks = await svc.search_with_rerank(
            query=query,
            scenario=scenario,
            top_k=top_k,
            min_similarity=0.2,
        )
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Knowledge search failed: {type(e).__name__}: {e}",
        )
    finally:
        await conn.close()

    if not chunks:
        return ToolResult(
            output=f"Knowledge base returned no results for query: '{query}'."
        )

    lines: list[str] = [f"Found {len(chunks)} relevant result(s) for '{query}':\n"]
    for i, c in enumerate(chunks, 1):
        source_info = f" [source: {c.source}]" if c.source else ""
        scenario_info = f" [scenario: {c.scenario}]" if c.scenario else ""
        score_info = f" [score: {c.score:.3f}]" if c.score else ""
        lines.append(f"{i}.{source_info}{scenario_info}{score_info}")
        # 截断过长文本
        text = c.text[:800] + "..." if len(c.text) > 800 else c.text
        lines.append(f"   {text}\n")

    return ToolResult(output="\n".join(lines))


SEARCH_KNOWLEDGE_TOOL = ToolDef(
    name="search_knowledge",
    description=(
        "Search the knowledge base for relevant information. "
        "Returns ranked knowledge chunks matching the query. "
        "Use this when you need to reference stored documents, "
        "guidelines, or previously processed information."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query text.",
            },
            "scenario": {
                "type": "string",
                "description": "Optional scenario filter (e.g. 'office', 'data_analysis').",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default 5, max 20).",
            },
        },
        "required": ["query"],
    },
    handler=_search_knowledge_handler,
)