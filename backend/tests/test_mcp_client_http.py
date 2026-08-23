"""B2 P1-6 + Phase 2 - MCP HTTP transport 实现(2026-07-28 无状态协议)。

- HTTP 模式用 httpx.AsyncClient,JSON-RPC 2.0 POST 到 config.url(MCP 端点, 不拼 /rpc)
- 请求带 MCP-Protocol-Version / Mcp-Method / Mcp-Name 头 + _meta(无状态)
- connect 成功后自动 ping,失败抛 McpConnectError
"""
from __future__ import annotations

import httpx
import pytest

from private_agent.errors import McpConnectError
from private_agent.tools.mcp_client import (
    CLIENT_INFO,
    PROTOCOL_VERSION,
    MCPClient,
    MCPClientConfig,
)


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
    """P1-6: HTTP 模式 connect/discover/call/disconnect(2026-07-28 无状态)。"""

    async def test_connect_pings_and_marks_connected(self, http_config: MCPClientConfig) -> None:
        """connect(http) 应建 httpx client + 自动 ping,成功则 connected=True。"""
        ping_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal ping_count
            if request.url.path == "/" and "ping" in request.content.decode():
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

    async def test_discover_tools_uses_stateless_headers(self, http_config: MCPClientConfig) -> None:
        """discover_tools(http) 应携带 2026-07-28 无状态头(Mcp-Method/MCP-Protocol-Version)。"""
        tools = [{"name": "read", "description": "Read file", "inputSchema": {"type": "object"}}]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/"
            assert request.method == "POST"
            assert request.headers.get("MCP-Protocol-Version") == PROTOCOL_VERSION
            assert request.headers.get("Mcp-Method") == "tools/list"
            body = request.content.decode()
            assert "tools/list" in body
            assert CLIENT_INFO["name"] in body  # _meta 注入 clientInfo
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}})

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.discover_tools()
        assert result == tools

    async def test_call_tool_uses_stateless_headers_and_meta(
        self, http_config: MCPClientConfig
    ) -> None:
        """call_tool(http) 应携带 Mcp-Name 头 + _meta(无状态)。"""
        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            assert "tools/call" in body
            assert "calculate" in body
            # 无状态头
            assert request.headers.get("Mcp-Method") == "tools/call"
            assert request.headers.get("Mcp-Name") == "calculate"
            assert request.headers.get("MCP-Protocol-Version") == PROTOCOL_VERSION
            # _meta 注入
            assert '"protocolVersion"' in body
            assert "io.modelcontextprotocol/clientInfo" in body
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"resultType": "result", "content": [{"type": "text", "text": "42"}]},
                },
            )

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.call_tool("calculate", {"expr": "6*7"})
        assert result["content"][0]["text"] == "42"

    async def test_call_tool_mrtr_input_required_passthrough(
        self, http_config: MCPClientConfig
    ) -> None:
        """MRTR: 服务器返回 inputRequired 时原样透传(不当作错误)。"""
        mrtr_result = {
            "resultType": "inputRequired",
            "inputRequests": {
                "confirm": {
                    "type": "elicitation",
                    "message": "确定删除 3 个文件?",
                    "schema": {"type": "boolean"},
                }
            },
            "requestState": "eyJzdGVwIjoxfQ==",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": mrtr_result})

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.call_tool("delete_files", {"paths": ["a", "b", "c"]})
        assert result.get("resultType") == "inputRequired"
        assert "inputRequests" in result
        assert "requestState" in result

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


class TestErrorVisibility:
    """2026-08-06: JSON-RPC error / HTTP 错误透传 isError(不静默空)。"""

    async def test_http_4xx_returns_iserror(self, http_config: MCPClientConfig) -> None:
        """HTTP 4xx + JSON error body → call_tool 返回 isError(而非空 result)。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500,
                json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}},
            )

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.call_tool("web_search", {"query": "x"})
        assert result.get("isError") is True
        assert "boom" in str(result.get("error"))

    async def test_http_error_body_no_result_returns_iserror(
        self, http_config: MCPClientConfig
    ) -> None:
        """200 但 body 为 JSON-RPC error → isError 透传(修复空结果根因)。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "internal"}},
            )

        client = _mock_client(handler, http_config)
        client._connected = True
        result = await client.call_tool("web_search", {"query": "x"})
        assert result.get("isError") is True
        assert "internal" in str(result.get("error"))
