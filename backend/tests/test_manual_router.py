"""测试 ManualRouter select_by_tag 标签路由(蓝图 §5.x / spec m2-tools-lifecycle AC-5)。

ManualRouter 需要支持基于 MCP Server 标签筛选候选服务。
"""
from __future__ import annotations

import pytest

from private_agent.models.registry import ManualRouter
from private_agent.tools.mcp_client import MCPClient, MCPClientConfig


@pytest.fixture
def router() -> ManualRouter:
    return ManualRouter({"models": {"router": {"type": "manual"}}})


@pytest.fixture
def mcps() -> list[MCPClient]:
    return [
        MCPClient(MCPClientConfig(server_id="fs", server_type="stdio", command="npx", tags=["filesystem", "utility"], timeout_sec=10)),
        MCPClient(MCPClientConfig(server_id="db", server_type="stdio", command="npx", tags=["database"], timeout_sec=10)),
        MCPClient(MCPClientConfig(server_id="web", server_type="stdio", command="npx", tags=["web", "utility"], timeout_sec=10)),
    ]


class TestManualRouterSelectByTag:
    """AC-5: ManualRouter 基于标签筛选 MCP 客户端。"""

    def test_select_by_tag_returns_matching_clients(self, router: ManualRouter, mcps: list[MCPClient]) -> None:
        """select_by_tag 应返回匹配标签的 MCP 客户端列表。"""
        result = router.select_by_tag("filesystem", mcps)
        assert len(result) == 1
        assert result[0].config.server_id == "fs"

    def test_select_by_tag_returns_multi_match(self, router: ManualRouter, mcps: list[MCPClient]) -> None:
        """select_by_tag 应返回所有匹配标签的客户端。"""
        result = router.select_by_tag("utility", mcps)
        assert len(result) == 2
        ids = [c.config.server_id for c in result]
        assert "fs" in ids
        assert "web" in ids

    def test_select_by_tag_no_match_returns_empty(self, router: ManualRouter, mcps: list[MCPClient]) -> None:
        """select_by_tag 无匹配时返回空列表。"""
        result = router.select_by_tag("nonexistent", mcps)
        assert result == []

    def test_select_by_tag_empty_mcp_list(self, router: ManualRouter) -> None:
        """select_by_tag 空 MCP 列表时返回空列表。"""
        result = router.select_by_tag("anything", [])
        assert result == []

    def test_select_by_tag_exact_match_only(self, router: ManualRouter, mcps: list[MCPClient]) -> None:
        """select_by_tag 应精确匹配，不支持部分匹配。"""
        result = router.select_by_tag("file", mcps)
        assert result == []

    def test_select_by_tag_empty_tag_returns_empty(self, router: ManualRouter, mcps: list[MCPClient]) -> None:
        """select_by_tag 空字符串标签应返回空列表。"""
        result = router.select_by_tag("", mcps)
        assert result == []