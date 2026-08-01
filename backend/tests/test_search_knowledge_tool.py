"""search_knowledge 内置工具测试。

测试:
- 工具定义(schema/name/description)
- Handler 参数验证
- Handler 格式化输出(使用 mock 替代 DB)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from private_agent.knowledge.models import Chunk
from private_agent.tools.builtins.search_knowledge import SEARCH_KNOWLEDGE_TOOL


class TestToolDef:
    """工具定义测试(无需 DB)。"""

    def test_name(self):
        assert SEARCH_KNOWLEDGE_TOOL.name == "search_knowledge"

    def test_description(self):
        assert isinstance(SEARCH_KNOWLEDGE_TOOL.description, str)
        assert len(SEARCH_KNOWLEDGE_TOOL.description) > 20

    def test_openai_schema(self):
        schema = SEARCH_KNOWLEDGE_TOOL.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "search_knowledge"
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert params["required"] == ["query"]

    def test_parameters_schema(self):
        ps = SEARCH_KNOWLEDGE_TOOL.parameters_schema
        assert "query" in ps["properties"]
        assert ps["properties"]["query"]["type"] == "string"
        assert "scenario" in ps["properties"]
        assert "top_k" in ps["properties"]
        assert ps["properties"]["top_k"]["type"] == "integer"


class TestHandlerWithMock:
    """Handler 测试(使用 mock 替代 DB 连接)。"""

    @pytest.mark.asyncio
    async def test_empty_query(self):
        result = await SEARCH_KNOWLEDGE_TOOL.handler({})
        assert result.error is not None
        assert "No query" in result.error

    @pytest.mark.asyncio
    async def test_empty_query_explicit(self):
        result = await SEARCH_KNOWLEDGE_TOOL.handler({"query": ""})
        assert result.error is not None
        assert "No query" in result.error

    @pytest.mark.asyncio
    async def test_db_connect_failure(self):
        """DB 连接失败时返回错误。"""
        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(side_effect=Exception("connection refused")),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {"host": "localhost"}},
        ):
            result = await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "test query"}
            )
            assert result.error is not None
            assert "connection refused" in result.error

    @pytest.mark.asyncio
    async def test_search_returns_no_results(self):
        """检索无结果时返回提示信息。"""
        mock_svc = AsyncMock()
        mock_svc.search_with_rerank.return_value = []

        mock_conn = AsyncMock()

        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(return_value=mock_conn),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {}},
        ), patch(
            "private_agent.tools.builtins.search_knowledge.KnowledgeBaseService",
            return_value=mock_svc,
        ):
            result = await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "something unknown"}
            )
            assert result.error is None
            assert "no results" in result.output.lower()

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """检索有结果时返回格式化文本。"""
        chunks = [
            Chunk(
                chunk_id=1,
                text="This is a relevant knowledge chunk about Python.",
                source="docs/python.md",
                scenario="office",
                score=0.95,
            ),
            Chunk(
                chunk_id=2,
                text="Another piece of knowledge about async programming.",
                source="docs/async.md",
                scenario="office",
                score=0.85,
            ),
        ]

        mock_svc = AsyncMock()
        mock_svc.search_with_rerank.return_value = chunks

        mock_conn = AsyncMock()

        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(return_value=mock_conn),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {}},
        ), patch(
            "private_agent.tools.builtins.search_knowledge.KnowledgeBaseService",
            return_value=mock_svc,
        ):
            result = await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "python programming", "top_k": 2}
            )
            assert result.error is None
            assert "2 relevant result" in result.output
            assert "Python" in result.output
            assert "docs/python.md" in result.output
            assert "0.950" in result.output or "0.95" in result.output

    @pytest.mark.asyncio
    async def test_search_with_scenario_filter(self):
        """scenario 参数传递给服务层。"""
        mock_svc = AsyncMock()
        mock_svc.search_with_rerank.return_value = []

        mock_conn = AsyncMock()

        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(return_value=mock_conn),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {}},
        ), patch(
            "private_agent.tools.builtins.search_knowledge.KnowledgeBaseService",
            return_value=mock_svc,
        ):
            await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "test", "scenario": "office"}
            )
            mock_svc.search_with_rerank.assert_called_once()
            kwargs = mock_svc.search_with_rerank.call_args.kwargs
            assert kwargs.get("scenario") == "office"

    @pytest.mark.asyncio
    async def test_top_k_clamping(self):
        """top_k 超出范围时被钳制。"""
        mock_svc = AsyncMock()
        mock_svc.search_with_rerank.return_value = []

        mock_conn = AsyncMock()

        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(return_value=mock_conn),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {}},
        ), patch(
            "private_agent.tools.builtins.search_knowledge.KnowledgeBaseService",
            return_value=mock_svc,
        ):
            await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "test", "top_k": 100}
            )
            mock_svc.search_with_rerank.assert_called_once()
            assert mock_svc.search_with_rerank.call_args.kwargs.get("top_k") == 20

    @pytest.mark.asyncio
    async def test_search_error_handled(self):
        """检索异常时返回错误信息。"""
        mock_svc = AsyncMock()
        mock_svc.search_with_rerank.side_effect = ValueError("search failed")

        mock_conn = AsyncMock()

        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(return_value=mock_conn),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {}},
        ), patch(
            "private_agent.tools.builtins.search_knowledge.KnowledgeBaseService",
            return_value=mock_svc,
        ):
            result = await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "test"}
            )
            assert result.error is not None
            assert "search failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_long_text_truncated(self):
        """超过 800 字符的文本应被截断。"""
        long_text = "A" * 1000
        chunks = [Chunk(chunk_id=1, text=long_text, source="test.md", score=0.9)]

        mock_svc = AsyncMock()
        mock_svc.search_with_rerank.return_value = chunks

        mock_conn = AsyncMock()

        with patch(
            "private_agent.tools.builtins.search_knowledge.db.connect",
            AsyncMock(return_value=mock_conn),
        ), patch(
            "private_agent.tools.builtins.search_knowledge.loader.load_config",
            return_value={"database": {}},
        ), patch(
            "private_agent.tools.builtins.search_knowledge.KnowledgeBaseService",
            return_value=mock_svc,
        ):
            result = await SEARCH_KNOWLEDGE_TOOL.handler(
                {"query": "test"}
            )
            assert "..." in result.output


class TestRegistration:
    """验证工具已正确注册到 register_all_builtins。"""

    def test_tool_is_in_registry(self):
        from private_agent.tools.builtins import register_all_builtins
        from private_agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_all_builtins(registry)
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "search_knowledge" in names

    def test_tool_can_be_retrieved(self):
        from private_agent.tools.builtins import register_all_builtins
        from private_agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_all_builtins(registry)
        tool = registry.get_tool("search_knowledge")
        assert tool is not None
        assert tool.name == "search_knowledge"