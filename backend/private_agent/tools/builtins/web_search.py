"""web_search 内置工具: 多后端网络搜索。

后端选择(优先级: 环境变量 PA_WEB_SEARCH_BACKEND > config.yaml tools.web_search.backend > duckduckgo):
- duckduckgo: DuckDuckGo Instant Answer API(无需 key, 国际网络可达, 默认)
- bocha:      博查 BochaAI 搜索 API(国内可达, 需 PA_BOCHA_API_KEY, bochaai.com 免费注册)
- bing:       Bing 搜索结果页 HTML 解析(无需 key, 尽力而为)
"""
from __future__ import annotations

import asyncio
import html as _html
import os
import re
import urllib.parse

import httpx

from private_agent.security.ssrf import SSRFBlockedError
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["web_search_handler", "WEB_SEARCH_TOOL"]

_TIMEOUT = httpx.Timeout(20.0)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 阶段二批次 2: 搜索后端固定域名白名单(防御式——URL 目前硬编码,
# 显式白名单防止未来参数化引入 SSRF)
_WEB_SEARCH_ALLOWED_HOSTS = {
    "api.duckduckgo.com",
    "api.bochaai.com",
    "www.bing.com",
}


def _check_search_endpoint(url: str) -> None:
    """校验搜索后端 URL 域名在白名单内, 否则拒绝。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host not in _WEB_SEARCH_ALLOWED_HOSTS:
        raise SSRFBlockedError(f"search endpoint host '{host}' not allowed")


def _pick_backend() -> str:
    """返回搜索后端名(env > config.yaml > 默认 duckduckgo)。"""
    env = os.environ.get("PA_WEB_SEARCH_BACKEND", "")
    if env:
        return env
    try:
        from private_agent.config import loader

        cfg = loader.load_config()
        backend = cfg.get("tools", {}).get("web_search", {}).get("backend", "")
        return backend or "duckduckgo"
    except Exception:  # noqa: BLE001
        return "duckduckgo"


async def web_search_handler(args: dict) -> ToolResult:
    """执行网络搜索(多后端 fallback)。"""
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(output="", error="No query provided")

    backend = _pick_backend()
    try:
        if backend == "bocha":
            return await _search_bocha(query)
        if backend == "bing":
            return await _search_bing(query)
        return await _search_duckduckgo(query)
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            output="",
            error=(
                f"Search failed ({backend}): {type(e).__name__}: {e}\n"
                "提示: 当前网络可能无法访问该搜索后端。可在设置中改用国内可达的 "
                "bocha 后端(注册 bochaai.com 免费获取 PA_BOCHA_API_KEY, 并设置 "
                "PA_WEB_SEARCH_BACKEND=bocha)或 bing 后端。"
            ),
        )


async def _search_duckduckgo(query: str) -> ToolResult:
    """DuckDuckGo Instant Answer API(无需 key)。"""
    _check_search_endpoint("https://api.duckduckgo.com/")
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": _UA}) as client:
        resp = await client.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1"},
        )
        resp.raise_for_status()
        data = resp.json()
        abstract = data.get("AbstractText", "")
        source = data.get("AbstractSource", "")
        if abstract:
            text = abstract
            if source:
                text += f"\nSource: {source}"
            return ToolResult(output=text)
        # 无摘要时给出相关话题提示
        topics = data.get("RelatedTopics", [])
        names = [
            t.get("Text", "") for t in topics if isinstance(t, dict) and t.get("Text")
        ][:5]
        if names:
            return ToolResult(output=f"未找到直接摘要, 相关话题:\n- " + "\n- ".join(names))
        return ToolResult(output=f"Search results for '{query}' (no abstract available)")


async def _search_bocha(query: str) -> ToolResult:
    """博查 BochaAI 搜索 API(国内可达, 需 key)。"""
    _check_search_endpoint("https://api.bochaai.com/v1/web-search")
    key = os.environ.get("PA_BOCHA_API_KEY", "")
    if not key:
        return ToolResult(
            output="",
            error=(
                "Bocha 搜索需要 API Key: 请注册 bochaai.com(免费额度), "
                "设置环境变量 PA_BOCHA_API_KEY 后重试"
            ),
        )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            "https://api.bochaai.com/v1/web-search",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "count": 8, "summary": True},
        )
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("data", {}).get("webPages", {}).get("value", [])
        lines = []
        for p in pages[:8]:
            name = p.get("name", "")
            url = p.get("url", "")
            snippet = (p.get("summary") or p.get("snippet") or "")[:220]
            lines.append(f"- {name}\n  {url}\n  {snippet}")
        if not lines:
            return ToolResult(output=f"未找到与 '{query}' 相关的结果")
        return ToolResult(output="\n\n".join(lines))


async def _search_bing(query: str) -> ToolResult:
    """Bing 搜索结果页 HTML 解析(尽力而为, 部分网络环境可能被反爬)。"""
    _check_search_endpoint("https://www.bing.com/search")
    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    ) as client:
        resp = await client.get(
            "https://www.bing.com/search",
            params={"q": query, "setmkt": "zh-CN"},
        )
        resp.raise_for_status()
        html_text = resp.text
        if not html_text:
            return ToolResult(
                output="",
                error="Bing 返回空响应(可能被反爬), 建议切换 bocha 后端",
            )
        results: list[str] = []
        for m in re.finditer(r'<li class="b_algo".*?</li>', html_text, re.S):
            block = m.group(0)
            title_m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not title_m:
                continue
            url = _html.unescape(title_m.group(1))
            title = _strip_tags(title_m.group(2))
            snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
            snippet = _strip_tags(snippet_m.group(1))[:200] if snippet_m else ""
            results.append(f"- {title}\n  {url}\n  {snippet}")
        if not results:
            return ToolResult(output=f"未找到与 '{query}' 相关的结果")
        return ToolResult(output="\n\n".join(results[:8]))


def _strip_tags(text: str) -> str:
    """剥离 HTML 标签并清理空白。"""
    text = re.sub(r"<[^>]+>", "", text)
    return _html.unescape(text).strip()


WEB_SEARCH_TOOL = ToolDef(
    name="web_search",
    description=(
        "Search the web for information. Returns a summary of search results "
        "for the given query."
    ),
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
