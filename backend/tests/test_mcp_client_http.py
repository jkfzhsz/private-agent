"""B2 P1-6 - MCP HTTP transport 实现。

Source: plan/b2-remaining-features step 5-8 (修复计划 §2 P1-6)
- HTTP 模式用 httpx.AsyncClient,JSON-RPC 2.0 over POST /rpc
- connect 成功后自动 ping,失败抛 McpConnectError
"""
from __future__ import annotations

import httpx
import pytest

from private_agent.errors import McpConnectError
from private_agent.tools.mcp_client import MCPClient, MCPClientConfig


@pytest.fixture
def http_config() -> MCPClientConfig:
    return MCPClientConfig(
        server_id="http_server",
        server_type="http",
        url="http://mcp.test",
        tags=["web"],
        timeout_sec=5,
    )


def _mock_client(handler, config: MCPClientConfig) -> MCPClient:
    """用 MockTransport 构造 http 模式 MCPClient。"""
    transport = httpx.MockTransport(handler)
    client = MCPClient(config)
    client._http_client = httpx.AsyncClient(base_url=config.url, transport=transport)
    return client


class TestMCPClientHttp:
    """P1-6: HTTP 模式 connect/discover/call/disconnect。"""

    async def test_connect_pings_and_marks_connected(self, http_config: MCPClientConfig) -> None:
        """connect(http) 应建 httpx client + 自动 ping,成功则 connected=True。"""
        ping_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal ping_count
            if request.url.path == "/rpc" and "ping" in request.content.decode():
                ping_count += 1
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
            return httpx.Response(404, json={"error": "not found"})

        client = _mock_client(handler, http_config)
        await client.connect()
        assert client.connected is True
        assert ping_count >= 1

    async def test_connect_ping_failure_raises_mcp_connect_error(
        self, http_config: MCPClientConfig
    ) -> None:
        """connect 后 ping 失败 → 抛 McpConnectError 且断开。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        client = _mock_client(handler, http_config)
        with pytest.raises(McpConnectError):
            await client.connect()
        assert client.connected is False

    async def test_discover_tools_returns_list(self, http_config: MCPClientConfig) -> None:
        """discover_tools(http) 经 POST /rpc tools/list 返回工具列表。"""
        tools = [{"name": "read", "description": "Read file", "inputSchema": {"type": "object"}}]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/rpc"
            assert request.method == "POST"
            body = request.content.decode()
            assert "tools/list" in body
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}})

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.discover_tools()
        assert result == tools

    async def test_call_tool_returns_result(self, http_config: MCPClientConfig) -> None:
        """call_tool(http) 经 POST /rpc tools/call 返回结果。"""
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            assert "tools/call" in body
            assert "calculate" in body
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "42"}]},
                },
            )

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.call_tool("calculate", {"expr": "6*7"})
        assert result["content"][0]["text"] == "42"

    async def test_disconnect_closes_http_client(self, http_config: MCPClientConfig) -> None:
        """disconnect(http) 应关闭 httpx client 并幂等。"""
        client = _mock_client(lambda r: httpx.Response(404), http_config)
        client._connected = True
        await client.disconnect()
        assert client.connected is False
        assert client._http_client is None
        # 幂等
        await client.disconnect()
        assert client.connected is False

    async def test_discover_tools_not_connected_raises(self, http_config: MCPClientConfig) -> None:
        """未连接时 discover_tools(http) 应抛 RuntimeError。"""
        client = _mock_client(lambda r: httpx.Response(404), http_config)
        with pytest.raises(RuntimeError, match="not connected"):
            await client.discover_tools()
