"""测试 MCPClient（蓝图 §5.x / spec m2-tools-lifecycle AC-3/4）。

MCP 协议客户端:stdio 模式全量实现,HTTP 模式仅 stub。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from private_agent.tools.mcp_client import MCPClient, MCPClientConfig


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def stdio_config() -> MCPClientConfig:
    return MCPClientConfig(
        server_id="test_server",
        server_type="stdio",
        command="python",
        args=["-c", "print('mock')"],
        tags=["utility"],
        timeout_sec=10,
    )


@pytest.fixture
def http_config() -> MCPClientConfig:
    return MCPClientConfig(
        server_id="http_server",
        server_type="http",
        command="",
        args=[],
        tags=["web"],
        timeout_sec=10,
    )


# ──────────────────────────────────────────────────────────────────────────────
# MCPClientConfig
# ──────────────────────────────────────────────────────────────────────────────


class TestMCPClientConfig:
    """MCPClientConfig 数据类基础行为。"""

    def test_stdio_config_created(self, stdio_config: MCPClientConfig) -> None:
        assert stdio_config.server_id == "test_server"
        assert stdio_config.server_type == "stdio"
        assert stdio_config.command == "python"

    def test_http_config_created(self, http_config: MCPClientConfig) -> None:
        assert http_config.server_id == "http_server"
        assert http_config.server_type == "http"


# ──────────────────────────────────────────────────────────────────────────────
# MCPClient - HTTP (B2 P1-6)
# ──────────────────────────────────────────────────────────────────────────────


class TestMCPClientHttp:
    """P1-6: HTTP 模式 MCPClient 生命周期(connect/discover/call/disconnect)。"""

    async def test_connect_http_marks_connected(self, http_config: MCPClientConfig) -> None:
        """connect(http) 应建 httpx client 并标记 connected。"""
        client = MCPClient(http_config)
        with patch.object(client, "ping", new=AsyncMock(return_value=True)):
            await client.connect()
            assert client.connected is True
            await client.disconnect()

    async def test_connect_http_ping_failure_raises(self, http_config: MCPClientConfig) -> None:
        """connect(http) ping 失败应抛 McpConnectError。"""
        from private_agent.errors import McpConnectError

        client = MCPClient(http_config)
        with patch.object(client, "ping", new=AsyncMock(return_value=False)):
            with pytest.raises(McpConnectError):
                await client.connect()

    async def test_discover_http_returns_tools(self, http_config: MCPClientConfig) -> None:
        """discover_tools(http) 经 _http_post 返回工具列表。"""
        client = MCPClient(http_config)
        client._connected = True
        with patch.object(client, "_http_post", new=AsyncMock(return_value={"tools": [{"name": "t"}]})):
            tools = await client.discover_tools()
            assert tools == [{"name": "t"}]

    async def test_call_tool_http_returns_result(self, http_config: MCPClientConfig) -> None:
        """call_tool(http) 经 _http_post 返回结果。"""
        client = MCPClient(http_config)
        client._connected = True
        with patch.object(client, "_http_post", new=AsyncMock(return_value={"content": []})):
            result = await client.call_tool("echo", {"text": "hi"})
            assert result == {"content": []}

    async def test_disconnect_http_closes_and_idempotent(self, http_config: MCPClientConfig) -> None:
        """disconnect(http) 关闭 client 且幂等。"""
        client = MCPClient(http_config)
        client._connected = True
        await client.disconnect()
        assert client.connected is False
        await client.disconnect()  # 不抛异常

    async def test_connected_property_http(self, http_config: MCPClientConfig) -> None:
        client = MCPClient(http_config)
        assert client.connected is False


# ──────────────────────────────────────────────────────────────────────────────
# MCPClient - stdio (AC-3)
# ──────────────────────────────────────────────────────────────────────────────


class TestMCPClientStdio:
    """AC-3: stdio 模式 MCPClient 生命周期(connect/discover/call/disconnect)。"""

    async def test_initial_state_not_connected(self, stdio_config: MCPClientConfig) -> None:
        """新建 MCPClient 的 connected 应为 False。"""
        client = MCPClient(stdio_config)
        assert client.connected is False
        assert client._reconnect_count == 0

    async def test_connect_stdio_starts_subprocess(self, stdio_config: MCPClientConfig) -> None:
        """connect 应启动子进程并设置 connected=True。"""
        client = MCPClient(stdio_config)
        with patch.object(client, "_process", None):
            mock_process = MagicMock()
            mock_process.stdout = AsyncMock()
            mock_process.stdin = AsyncMock()
            mock_process.returncode = None

            with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)):
                with patch.object(client, "_read_loop", new=AsyncMock()):
                    await client.connect()
                    assert client.connected is True
                    assert client._reconnect_count == 0

    async def test_disconnect_closes_subprocess(self, stdio_config: MCPClientConfig) -> None:
        """disconnect 应关闭子进程并设置 connected=False。"""
        client = MCPClient(stdio_config)
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        client._process = mock_process
        client._connected = True

        await client.disconnect()
        assert client.connected is False
        assert client._process is None

    async def test_disconnect_idempotent(self, stdio_config: MCPClientConfig) -> None:
        """已断开时调用 disconnect 应为幂等操作。"""
        client = MCPClient(stdio_config)
        client._process = None
        client._connected = False
        await client.disconnect()  # 不应抛异常
        assert client.connected is False

    async def test_reconnect_count_increments(self, stdio_config: MCPClientConfig) -> None:
        """重连计数器应递增。"""
        client = MCPClient(stdio_config)
        client._reconnect_count = 2
        assert client.reconnect_count == 2

    async def test_async_context_manager(self, stdio_config: MCPClientConfig) -> None:
        """async with 应自动 disconnect。"""
        client = MCPClient(stdio_config)
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        client._process = mock_process
        client._connected = True

        async with client:
            assert client.connected is True
        assert client.connected is False

    async def test_discover_tools_returns_list(self, stdio_config: MCPClientConfig) -> None:
        """discover_tools 应返回工具列表。"""
        client = MCPClient(stdio_config)
        client._connected = True
        mock_response = {
            "result": {
                "tools": [
                    {"name": "read", "description": "Read file", "inputSchema": {"type": "object"}},
                ]
            }
        }
        with patch.object(client, "_send_request", new=AsyncMock(return_value=mock_response)):
            tools = await client.discover_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "read"

    async def test_discover_tools_not_connected_raises(self, stdio_config: MCPClientConfig) -> None:
        """未连接时 discover_tools 应抛 RuntimeError。"""
        client = MCPClient(stdio_config)
        client._connected = False
        with pytest.raises(RuntimeError, match="not connected"):
            await client.discover_tools()

    async def test_call_tool_returns_result(self, stdio_config: MCPClientConfig) -> None:
        """call_tool 应返回工具调用结果。"""
        client = MCPClient(stdio_config)
        client._connected = True
        mock_response = {
            "result": {
                "content": [{"type": "text", "text": "42"}],
            }
        }
        with patch.object(client, "_send_request", new=AsyncMock(return_value=mock_response)):
            result = await client.call_tool("calculate", {"expr": "6*7"})
            assert result["content"][0]["text"] == "42"

    async def test_call_tool_not_connected_raises(self, stdio_config: MCPClientConfig) -> None:
        """未连接时 call_tool 应抛 RuntimeError。"""
        client = MCPClient(stdio_config)
        client._connected = False
        with pytest.raises(RuntimeError, match="not connected"):
            await client.call_tool("test", {})

    async def test_discover_tools_sends_correct_request(self, stdio_config: MCPClientConfig) -> None:
        """discover_tools 应发送 method=tools/list 的 JSON-RPC 请求。"""
        client = MCPClient(stdio_config)
        client._connected = True
        sent_request = None

        async def capture_request(msg: dict) -> dict:
            nonlocal sent_request
            sent_request = msg
            return {"result": {"tools": []}}

        with patch.object(client, "_send_request", new=AsyncMock(side_effect=capture_request)):
            await client.discover_tools()
            assert sent_request is not None
            assert sent_request["method"] == "tools/list"

    async def test_call_tool_sends_correct_request(self, stdio_config: MCPClientConfig) -> None:
        """call_tool 应发送 method=tools/call 的 JSON-RPC 请求。"""
        client = MCPClient(stdio_config)
        client._connected = True
        sent_request = None

        async def capture_request(msg: dict) -> dict:
            nonlocal sent_request
            sent_request = msg
            return {"result": {"content": []}}

        with patch.object(client, "_send_request", new=AsyncMock(side_effect=capture_request)):
            await client.call_tool("echo", {"text": "hello"})
            assert sent_request is not None
            assert sent_request["method"] == "tools/call"
            assert sent_request["params"]["name"] == "echo"
            assert sent_request["params"]["arguments"] == {"text": "hello"}


class TestStdioConcurrency:
    """2026-08-07: stdio 并发工具调用(同轮并行)写锁 —— 防请求行交错。

    无写锁时多个协程同时 write stdin → 请求行拼接 → server 收到无效
    JSON → 无响应 → 超时/空结果(mempalace/searchpin 并行调用失败的根因)。
    用真实 stdio 子进程端到端验证: 并发 8 个调用全部收到响应。
    """

    async def test_concurrent_tools_against_real_stdio(self) -> None:
        import asyncio
        import sys

        echo_code = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    req = json.loads(line)\n"
            "    resp = {'jsonrpc':'2.0','id':req.get('id'),"
            "'result':{'content':[{'type':'text','text':'ok'}]}}\n"
            "    sys.stdout.write(json.dumps(resp)+'\\n')\n"
            "    sys.stdout.flush()\n"
        )
        client = MCPClient(
            MCPClientConfig(
                server_id="echo", server_type="stdio",
                command=sys.executable, args=["-c", echo_code],
                timeout_sec=15,
            )
        )
        await client.connect()
        try:
            results = await asyncio.gather(
                *(client.call_tool("echo", {"i": i}) for i in range(8))
            )
            # 无写锁时并发写入交错 → server 解析失败 → 部分超时/空
            assert len(results) == 8
            assert all(r.get("content") for r in results), (
                "并发 stdio 调用存在写交错(应有 _write_lock 串行化)"
            )
        finally:
            await client.disconnect()

    async def test_write_lock_exists(self) -> None:
        import asyncio

        client = MCPClient(MCPClientConfig(server_id="s", server_type="stdio"))
        assert isinstance(client._write_lock, asyncio.Lock)

    async def test_concurrent_slow_responses_no_cross_pop(self) -> None:
        """2026-08-07 根治回归: 慢响应 server 下并发调用不能互相误删 pending。

        竞态背景: _send_request 的 finally 若用共享 self._request_id 做
        pop, 请求 A(id=2)完成时 self._request_id 已被请求 B 改成 3 →
        pop(3) 误删 B 的 pending future → B 的响应到达时无处分发 → 超时
        (mempalace 并发调 2 工具第二个必超时的根因; echo 响应太快测不出)。

        用 sleep 0.3s 的慢响应 server 放大竞争窗口, 并发 4 个调用必须
        全部收到响应。
        """
        import asyncio
        import sys

        slow_code = (
            "import sys, json, time\n"
            "for line in sys.stdin:\n"
            "    req = json.loads(line)\n"
            "    time.sleep(0.3)\n"  # 慢响应放大竞态窗口(响应有先后)
            "    resp = {'jsonrpc':'2.0','id':req.get('id'),"
            "'result':{'content':[{'type':'text','text':str(req.get('id'))}]}}\n"
            "    sys.stdout.write(json.dumps(resp)+'\\n')\n"
            "    sys.stdout.flush()\n"
        )
        client = MCPClient(
            MCPClientConfig(
                server_id="slow", server_type="stdio",
                command=sys.executable, args=["-c", slow_code],
                timeout_sec=15,
            )
        )
        await client.connect()
        try:
            results = await asyncio.gather(
                *(client.call_tool("echo", {"i": i}) for i in range(4))
            )
            assert len(results) == 4, f"got {len(results)} results"
            for r in results:
                assert r.get("content"), f"empty content: {r}"
        finally:
            await client.disconnect()

