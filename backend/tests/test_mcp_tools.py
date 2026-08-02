"""MCP 工具装配测试(mcp_tools.py)。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from private_agent.tools.mcp_tools import MCPToolManager, mcp_result_to_text


class TestMcpResultToText:
    def test_text_content(self):
        r = mcp_result_to_text({"content": [{"type": "text", "text": "hello"}]})
        assert r == "hello"

    def test_is_error(self):
        r = mcp_result_to_text({"content": [{"type": "text", "text": "boom"}], "isError": True})
        assert "[工具错误]" in r and "boom" in r

    def test_empty_content_falls_back_to_json(self):
        r = mcp_result_to_text({"foo": "bar", "content": []})
        assert "bar" in r

    def test_image_audio_placeholder(self):
        r = mcp_result_to_text({
            "content": [
                {"type": "image", "data": "..."},
                {"type": "text", "text": "done"},
            ]
        })
        assert "[图片数据]" in r and "done" in r


class TestMcpToolManager:
    def _fake_client(self, tools: list[dict]):
        """构造 mock MCPClient: connect 成功 + discover_tools 返回 tools。"""
        client = MagicMock()
        client.connected = True
        client.connect = AsyncMock()
        client.discover_tools = AsyncMock(return_value=tools)
        client.call_tool = AsyncMock(
            return_value={"content": [{"type": "text", "text": "tool result"}]}
        )
        return client

    @pytest.mark.asyncio
    async def test_get_tools_builds_prefixed_tooldefs(self, monkeypatch):
        manager = MCPToolManager()
        svc = {"id": "srv1", "type": "http", "url": "http://x/mcp", "enabled": True}
        cfg = {"tools": {"mcp": {"servers": [svc]}}}

        async def fake_get_client(_svc):
            return self._fake_client([{"name": "hello", "description": "greet",
                                       "inputSchema": {"type": "object"}}])

        monkeypatch.setattr(manager, "_get_client", fake_get_client)
        tools = await manager.get_tools(cfg)
        assert len(tools) == 1
        assert tools[0].name == "mcp__srv1__hello"
        assert tools[0].description == "greet"

    @pytest.mark.asyncio
    async def test_disabled_server_skipped(self):
        manager = MCPToolManager()
        cfg = {"tools": {"mcp": {"servers": [
            {"id": "off", "type": "http", "enabled": False},
        ]}}}
        tools = await manager.get_tools(cfg)
        assert tools == []

    @pytest.mark.asyncio
    async def test_connect_failure_skipped(self, monkeypatch):
        manager = MCPToolManager()
        cfg = {"tools": {"mcp": {"servers": [
            {"id": "bad", "type": "http", "url": "http://x/mcp", "enabled": True},
        ]}}}

        async def fail(_svc):
            return None

        monkeypatch.setattr(manager, "_get_client", fail)
        assert await manager.get_tools(cfg) == []

    @pytest.mark.asyncio
    async def test_cache_reuses_tools(self, monkeypatch):
        manager = MCPToolManager()
        svc = {"id": "srv1", "type": "http", "url": "http://x/mcp", "enabled": True}
        cfg = {"tools": {"mcp": {"servers": [svc]}}}

        async def fake_get_client(_svc):
            return self._fake_client([{"name": "hello", "description": "greet",
                                       "inputSchema": {"type": "object"}}])

        monkeypatch.setattr(manager, "_get_client", fake_get_client)
        t1 = await manager.get_tools(cfg)
        t2 = await manager.get_tools(cfg)  # 第二次应命中缓存
        assert len(t1) == 1 and len(t2) == 1

    @pytest.mark.asyncio
    async def test_handler_calls_call_tool_and_returns_toolresult(self, monkeypatch):
        manager = MCPToolManager()
        client = self._fake_client([{"name": "hello", "description": "greet",
                                     "inputSchema": {"type": "object"}}])
        cfg = {"tools": {"mcp": {"servers": [
            {"id": "srv1", "type": "http", "url": "http://x/mcp", "enabled": True},
        ]}}}

        async def fake_get_client(_svc):
            return client

        monkeypatch.setattr(manager, "_get_client", fake_get_client)
        tools = await manager.get_tools(cfg)
        result = await tools[0].handler({"who": "world"})
        assert result.output == "tool result"
        assert result.error is None
        client.call_tool.assert_awaited_once_with("hello", {"who": "world"})

    @pytest.mark.asyncio
    async def test_handler_call_tool_error(self, monkeypatch):
        manager = MCPToolManager()
        client = self._fake_client([{"name": "hello", "description": "greet",
                                     "inputSchema": {"type": "object"}}])
        client.call_tool = AsyncMock(side_effect=RuntimeError("conn lost"))
        cfg = {"tools": {"mcp": {"servers": [
            {"id": "srv1", "type": "http", "url": "http://x/mcp", "enabled": True},
        ]}}}

        async def fake_get_client(_svc):
            return client

        monkeypatch.setattr(manager, "_get_client", fake_get_client)
        tools = await manager.get_tools(cfg)
        result = await tools[0].handler({})
        assert result.error and "conn lost" in result.error

    @pytest.mark.asyncio
    async def test_handler_mrtr_passthrough(self, monkeypatch):
        manager = MCPToolManager()
        client = self._fake_client([{"name": "hello", "description": "greet",
                                     "inputSchema": {"type": "object"}}])
        client.call_tool = AsyncMock(return_value={
            "resultType": "inputRequired",
            "inputRequests": {"confirm": {"message": "确认?"}},
            "requestState": "abc",
        })
        cfg = {"tools": {"mcp": {"servers": [
            {"id": "srv1", "type": "http", "url": "http://x/mcp", "enabled": True},
        ]}}}

        async def fake_get_client(_svc):
            return client

        monkeypatch.setattr(manager, "_get_client", fake_get_client)
        tools = await manager.get_tools(cfg)
        result = await tools[0].handler({})
        assert result.error and "需要用户确认" in result.error
