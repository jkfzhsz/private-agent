"""测试 web_search 内置工具。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from private_agent.tools.builtins.web_search import web_search_handler


class TestWebSearch:
    """web_search 工具:HTTP 搜索 API 封装。"""

    async def test_search_returns_results(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"AbstractText": "AI is artificial intelligence", "AbstractSource": "Wikipedia"}
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