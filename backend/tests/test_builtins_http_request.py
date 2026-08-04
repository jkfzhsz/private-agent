"""测试 http_request 内置工具(阶段二批次 2 起带 SSRF 防护)。

handler 现走 safe_httpx_client(SafeHttpxClient, 见 security/ssrf.py),
既有测试适配: mock safe_httpx_client 返回的客户端对象。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from private_agent.tools.builtins.http_request import http_request_handler


def _mock_safe_client(status_code=200, text="", side_effect=None):
    """构造 SafeHttpxClient 的替身(handler 只用到 request + limited_content)。"""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.request.side_effect = side_effect
    mock_client.request.return_value = mock_response
    mock_client.limited_content.return_value = text.encode("utf-8")
    return mock_client


class TestHttpRequest:
    """http_request 工具:httpx GET/POST 封装 + SSRF 防护。"""

    async def test_get_request(self) -> None:
        mock_client = _mock_safe_client(status_code=200, text='{"ok": true}')

        with patch("private_agent.tools.builtins.http_request.safe_httpx_client", return_value=mock_client):
            result = await http_request_handler({"url": "https://example.com/api", "method": "GET"})
            assert result.error is None, result.error
            assert "ok" in result.output
            # 校验方法/URL 传递正确
            mock_client.request.assert_awaited_once_with("GET", "https://example.com/api", content=None)

    async def test_post_request(self) -> None:
        mock_client = _mock_safe_client(status_code=201, text='{"created": true}')

        with patch("private_agent.tools.builtins.http_request.safe_httpx_client", return_value=mock_client):
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
        mock_client = _mock_safe_client(side_effect=Exception("Connection refused"))

        with patch("private_agent.tools.builtins.http_request.safe_httpx_client", return_value=mock_client):
            result = await http_request_handler({"url": "https://example.com", "method": "GET"})
            assert result.error is not None