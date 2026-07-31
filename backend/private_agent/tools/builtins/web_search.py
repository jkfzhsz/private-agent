"""web_search 内置工具:HTTP 搜索 API 封装。

通过外部搜索 API 执行网络搜索，返回结果摘要。
"""
from __future__ import annotations

import httpx

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["web_search_handler", "WEB_SEARCH_TOOL"]


async def web_search_handler(args: dict) -> ToolResult:
    """执行网络搜索。

    Args:
        args: 包含 query(搜索关键词)的 dict。

    Returns:
        搜索结果文本或错误信息。
    """
    query = args.get("query", "")
    if not query:
        return ToolResult(output="", error="No query provided")

    # 使用 DuckDuckGo 的 lite 搜索 API(无需 API Key)
    url = "https://api.duckduckgo.com/"
    params = {"q": query, "format": "json", "no_html": "1"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            # 提取摘要文本
            abstract = data.get("AbstractText", "")
            source = data.get("AbstractSource", "")
            result_text = abstract if abstract else f"Search results for '{query}' (no abstract available)"
            if source:
                result_text += f"\nSource: {source}"
            return ToolResult(output=result_text)
    except Exception as e:
        return ToolResult(output="", error=f"Search failed: {type(e).__name__}: {e}")


WEB_SEARCH_TOOL = ToolDef(
    name="web_search",
    description="Search the web for information. Returns a summary of search results for the given query.",
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string.",
            }
        },
        "required": ["query"],
    },
    handler=web_search_handler,
)