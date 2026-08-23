"""测试 web_search 内置工具。"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from private_agent.tools.builtins.web_search import web_search_handler


class TestWebSearch:
    """web_search 工具:HTTP 搜索 API 封装。"""

    async def test_search_returns_results(self, monkeypatch) -> None:
        # 2026-08-15 修复: 显式锁定 bing 后端 —— _pick_backend 读
        # env > config.yaml > duckduckgo, 全量顺序下 config_runtime 可能
        # 残留其他 backend, 导致 mock(只配 bing HTML)走 duckduckgo JSON
        # 分支 → output 为 MagicMock。测试自包含, 不依赖全局默认。
        monkeypatch.setenv("PA_WEB_SEARCH_BACKEND", "bing")
        # 默认后端已是 bing(908ee2e 后 PA_WEB_SEARCH_BACKEND=bing):
        # mock 需提供 bing HTML(.text) 而非 duckduckgo JSON(.json)
        bing_html = (
            '<li class="b_algo">'
            '<h2><a href="https://en.wikipedia.org/wiki/Artificial_intelligence">'
            "Artificial intelligence</a></h2>"
            "<p>AI is artificial intelligence</p></li>"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = bing_html
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_response

        with patch("private_agent.tools.builtins.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search_handler({"query": "artificial intelligence"})
            assert result.error is None, result.error
            assert "AI" in result.output

    async def test_empty_query_raises(self) -> None:
        result = await web_search_handler({"query": ""})
        assert result.error is not None

    async def test_api_error_returns_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("API timeout")

        with patch("private_agent.tools.builtins.web_search.httpx.AsyncClient", return_value=mock_client):
            result = await web_search_handler({"query": "test"})
            assert result.error is not None