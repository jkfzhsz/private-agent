"""2026-08-13 类型感知限流: MCP client 类型信号量测试。

覆盖(方案 §6.7): search 类并发飞行数 ≤ type_limits.search; 不同类型互不影响。
mock _send_request(不真实连接 stdio 子进程), 统计并发峰值。
"""
import asyncio

import pytest

from private_agent.tools.mcp_client import MCPClient, MCPClientConfig


class _Tracker:
    """统计并发飞行峰值。send() 返回可任意签名的 async 闭包。"""

    def __init__(self, delay: float = 0.05):
        self.active = 0
        self.max_active = 0
        self.delay = delay

    def send(self):
        async def _inner(*args, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(self.delay)
            self.active -= 1
            return {"result": {}}

        return _inner


def _mk_client(type_limits: dict) -> MCPClient:
    cfg = MCPClientConfig(
        server_id="test", server_type="stdio", type_limits=type_limits
    )
    client = MCPClient(cfg)
    client._connected = True  # 跳过真实连接
    return client


def test_search_concurrent_limited_to_one(monkeypatch):
    """search 类(web_search)并发飞行数 ≤ type_limits.search(=1)。"""
    tracker = _Tracker()
    client = _mk_client({"search": 1, "analysis": 3, "code": 3, "other": 5})
    monkeypatch.setattr(client, "_send_request", tracker.send())

    async def _run():
        await asyncio.gather(
            *(client.call_tool("web_search", {"q": f"q{i}"}) for i in range(4))
        )
        assert tracker.max_active <= 1, f"search 并发峰值 {tracker.max_active} > 1"

    asyncio.run(_run())


def test_different_types_independent(monkeypatch):
    """search 与 analysis 互不影响: search 限 1, analysis 限 3。"""
    tracker = _Tracker()
    client = _mk_client({"search": 1, "analysis": 3, "code": 3, "other": 5})
    monkeypatch.setattr(client, "_send_request", tracker.send())

    async def _run():
        calls = [client.call_tool("web_search", {"q": f"s{i}"}) for i in range(3)]
        calls += [client.call_tool("calculator", {"expr": f"a{i}"}) for i in range(3)]
        await asyncio.gather(*calls)
        # 峰值 = search(限1) + analysis(限3) = 4
        assert tracker.max_active <= 4
        # analysis 3 个并发过(验证 analysis 不受 search 限 1 拖累)
        assert tracker.max_active >= 3

    asyncio.run(_run())


def test_http_type_not_limited(monkeypatch):
    """http 型 server 的 call_tool 也走类型限流(统一路径)。"""
    tracker = _Tracker()
    cfg = MCPClientConfig(
        server_id="test-http", server_type="http",
        url="http://localhost:9999/mcp", type_limits={"search": 1, "other": 5},
    )
    client = MCPClient(cfg)
    client._connected = True
    monkeypatch.setattr(client, "_http_post", tracker.send())

    async def _run():
        await asyncio.gather(
            *(client.call_tool("web_fetch", {"url": f"u{i}"}) for i in range(3))
        )
        assert tracker.max_active <= 1

    asyncio.run(_run())
