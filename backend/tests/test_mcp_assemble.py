"""V2 P2 - MCP server 装配开关(assemble) + 工具使用指南。

验证:
- assemble=false 的 server 不装配工具(_load_server_tools 不被调用)
- PUT /settings/mcp/{name}/assemble 更新开关并持久化
- build_tools_guide 生成按 server 分类的工具速查文本
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.api import admin
from private_agent.main import app
from private_agent.storage import db, migrations
from private_agent.tools.mcp_tools import MCPToolManager, build_tools_guide

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


def _patch_db_connect(monkeypatch) -> None:
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)
    monkeypatch.setattr(admin.db, "connect", _fake_connect)


def _write_servers(servers: list[dict]) -> None:
    """直接向测试 DB 写入 tools.mcp.servers。"""
    import json

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                "INSERT INTO config_runtime (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                "tools.mcp.servers",
                json.dumps(servers),
            )
        finally:
            await conn.close()

    asyncio.run(_run())


def _read_servers() -> list[dict]:
    import json

    async def _run() -> list[dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchval(
                "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
            )
            return json.loads(row) if row else []
        finally:
            await conn.close()

    return asyncio.run(_run())


def _fake_server(name: str, **kw) -> dict:
    base = {"id": name, "type": "http", "url": "http://fake.local/mcp",
            "enabled": True, "timeout_sec": 30.0, "protocol_version": "auto"}
    base.update(kw)
    return base


class TestAssembleFilter:
    def test_get_tools_skips_assemble_false(self):
        """assemble=false 的 server → _load_server_tools 不被调用(不装配)。"""
        mgr = MCPToolManager()
        cfg = {
            "tools": {
                "mcp": {
                    "servers": [
                        _fake_server("off-srv", assemble=False),
                        _fake_server("on-srv", assemble=True),
                    ]
                }
            }
        }

        async def _run():
            with patch.object(
                mgr, "_load_server_tools", new=AsyncMock(return_value=[])
            ) as mock_load:
                tools = await mgr.get_tools(cfg)
            # 只装配 assemble 未关闭的 server
            called_ids = [c.args[0]["id"] for c in mock_load.call_args_list]
            return tools, called_ids

        tools, called_ids = asyncio.run(_run())
        assert tools == []
        assert called_ids == ["on-srv"], (
            f"assemble=false 的 server 不应被装配, called={called_ids}"
        )

    def test_assemble_default_true(self):
        """未配置 assemble 字段 → 默认装配(向后兼容)。"""
        mgr = MCPToolManager()
        cfg = {
            "tools": {"mcp": {"servers": [_fake_server("legacy-srv")]}}
        }

        async def _run():
            with patch.object(
                mgr, "_load_server_tools", new=AsyncMock(return_value=[])
            ) as mock_load:
                await mgr.get_tools(cfg)
            return [c.args[0]["id"] for c in mock_load.call_args_list]

        called_ids = asyncio.run(_run())
        assert called_ids == ["legacy-srv"]


class TestAssembleEndpoint:
    def test_put_assemble_updates_and_persists(self, monkeypatch):
        """PUT /settings/mcp/{name}/assemble → 持久化 assemble=false。"""
        _setup_schema()
        _patch_db_connect(monkeypatch)
        _write_servers([_fake_server("s1", assemble=True)])

        client = TestClient(app)
        resp = client.put(
            "/admin/settings/mcp/s1/assemble", json={"assemble": False}
        )
        assert resp.status_code == 200
        servers = _read_servers()
        assert servers[0]["id"] == "s1"
        assert servers[0]["assemble"] is False

    def test_put_assemble_unknown_server_404(self, monkeypatch):
        """不存在的 server → 404。"""
        _setup_schema()
        _patch_db_connect(monkeypatch)
        _write_servers([_fake_server("s1")])

        client = TestClient(app)
        resp = client.put(
            "/admin/settings/mcp/nope/assemble", json={"assemble": False}
        )
        assert resp.status_code == 404


class TestBuildToolsGuide:
    def test_guide_lists_servers_and_tool_names(self):
        """指南包含 server id 与工具名, 不含完整 schema(避免重复占 token)。"""
        mgr = MCPToolManager()
        mgr._tools_cache = {
            "srv-a": [
                type("TD", (), {"name": "mcp__srv-a__get_price"})(),
                type("TD", (), {"name": "mcp__srv-a__get_news"})(),
            ],
            "srv-b": [
                type("TD", (), {"name": "mcp__srv-b__search"})(),
            ],
        }
        guide = build_tools_guide(mgr, [
            _fake_server("srv-a"), _fake_server("srv-b"),
        ])
        assert "srv-a" in guide
        assert "get_price" in guide
        assert "get_news" in guide
        assert "srv-b" in guide
        assert "search" in guide

    def test_guide_empty_when_no_servers(self):
        """无 server → 空字符串(不注入多余上下文)。"""
        mgr = MCPToolManager()
        assert build_tools_guide(mgr, []) == ""
