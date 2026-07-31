"""测试 http_request 内置工具。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from private_agent.tools.builtins.http_request import http_request_handler


class TestHttpRequest:
    """http_request 工具:httpx GET/POST 封装。"""

    async def test_get_request(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_response

        with patch("private_agent.tools.builtins.http_request.httpx.AsyncClient", return_value=mock_client):
            result = await http_request_handler({"url": "https://example.com/api", "method": "GET"})
            assert result.error is None, result.error
            assert "ok" in result.output

    async def test_post_request(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = '{"created": true}'
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response

        with patch("private_agent.tools.builtins.http_request.httpx.AsyncClient", return_value=mock_client):
            result = await http_request_handler({
                "url": "https://example.com/api",
                "method": "POST",
                "body": '{"key": "value"}',
            })
            assert result.error is None, result.error
            assert "created" in result.output

    async def test_invalid_method(self) -> None:
        result = await http_request_handler({"url": "https://example.com", "method": "INVALID"})
        assert result.error is not None

    async def test_http_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("Connection refused")

        with patch("private_agent.tools.builtins.http_request.httpx.AsyncClient", return_value=mock_client):
            result = await http_request_handler({"url": "https://example.com", "method": "GET"})
            assert result.error is not None