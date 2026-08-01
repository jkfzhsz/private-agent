"""B2 P1-6 - MCP 双探活:ping / health_check / liveness_loop。

Source: plan/b2-remaining-features step 5-8 (修复计划 §2 P1-6)
- ping(): 健康 True / 宕机 False
- health_check(): 组合 ping + discover_tools
- liveness_loop(): 后台 task,不健康时触发 on_unhealthy 回调
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from private_agent.tools.mcp_client import MCPClient, MCPClientConfig, McpHealthStatus


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
    transport = httpx.MockTransport(handler)
    client = MCPClient(config)
    client._http_client = httpx.AsyncClient(base_url=config.url, transport=transport)
    return client


def _healthy_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})


class TestMCPClientHealth:
    """P1-6: ping / health_check / liveness_loop 双探活。"""

    async def test_ping_returns_true_when_server_healthy(
        self, http_config: MCPClientConfig
    ) -> None:
        client = _mock_client(_healthy_handler, http_config)
        client._connected = True
        assert await client.ping() is True

    async def test_ping_returns_false_when_server_down(
        self, http_config: MCPClientConfig
    ) -> None:
        client = _mock_client(lambda r: httpx.Response(503, json={"error": "down"}), http_config)
        client._connected = True
        assert await client.ping() is False

    async def test_ping_returns_false_on_exception(
        self, http_config: MCPClientConfig
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _mock_client(handler, http_config)
        client._connected = True
        assert await client.ping() is False

    async def test_health_check_combines_ping_and_discover(
        self, http_config: MCPClientConfig
    ) -> None:
        tools = [{"name": "read"}]

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode()
            if "tools/list" in body:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

        client = _mock_client(handler, http_config)
        client._connected = True
        status = await client.health_check()
        assert isinstance(status, McpHealthStatus)
        assert status.ping_ok is True
        assert status.tools_count == 1
        assert status.latency_ms >= 0

    async def test_health_check_reports_down_when_ping_fails(
        self, http_config: MCPClientConfig
    ) -> None:
        client = _mock_client(lambda r: httpx.Response(500), http_config)
        client._connected = True
        status = await client.health_check()
        assert status.ping_ok is False
        assert status.tools_count == 0

    async def test_liveness_loop_triggers_on_unhealthy(
        self, http_config: MCPClientConfig
    ) -> None:
        """liveness_loop 在 ping 失败时触发 on_unhealthy 回调。"""
        client = _mock_client(lambda r: httpx.Response(500, json={"error": "down"}), http_config)
        client._connected = True

        triggered = asyncio.Event()

        async def on_unhealthy(reason: str) -> None:
            triggered.set()

        task = asyncio.create_task(client.liveness_loop(interval_sec=0.01, on_unhealthy=on_unhealthy))
        try:
            await asyncio.wait_for(triggered.wait(), timeout=2.0)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert triggered.is_set()
