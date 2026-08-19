"""2026-08-15(M2 P2-14): 查询重写测试。

- _expand_queries 纯函数(变体生成/去重/上限)
- query_rewrite enabled 时 search_with_rerank 多查询路径不崩溃且返回
  (真实 DB + PA_EMBEDDING_MOCK=1, keyword-only 检索)
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from private_agent.knowledge.factory import build_kb_service
from private_agent.knowledge.kb_service import _expand_queries
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


class TestExpandQueries:
    def test_original_query_always_first(self):
        assert _expand_queries("什么是商业银行公司信贷")[0] == "什么是商业银行公司信贷"

    def test_removes_stopwords_and_punct(self):
        queries = _expand_queries("请问，商业银行的信贷风险有哪些？")
        # 变体2: 去停用词/标点 → 核心词串(问/哪些/有 均属停用词)
        assert "商业银行信贷风险" in queries

    def test_multiword_join_variant(self):
        queries = _expand_queries("Basel III 资本充足率")
        assert any(" " not in q for q in queries[1:]), queries

    def test_dedup_and_limit(self):
        queries = _expand_queries("资本充足率", max_expansions=1)
        assert queries == ["资本充足率"]
        q2 = _expand_queries("资本充足率是什么", max_expansions=2)
        assert len(q2) <= 2
        assert len(set(q2)) == len(q2)

    def test_empty_query(self):
        assert _expand_queries("") == [""]
        assert _expand_queries("  ") == [""]


@pytest.fixture
async def conn():
    c = await asyncpg.connect(TEST_DSN)
    try:
        await c.execute("DROP SCHEMA public CASCADE")
        await c.execute("CREATE SCHEMA public")
        await c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await migrations.migrate_all(c)
        yield c
    finally:
        await c.close()


class TestQueryRewriteSearch:
    async def test_enabled_search_returns_results(self, conn):
        """enabled 时多查询检索路径返回结果(不崩溃, 与原路径语义一致)。"""
        cfg = {"kb": {"query_rewrite": {"enabled": True, "max_expansions": 3}}}
        svc = build_kb_service(conn, cfg, processor=None)
        await svc.process_document(
            content="商业银行公司信贷业务包含授信审批与贷后管理",
            filename="credit.md",
            scenario="office",
        )
        results = await svc.search_with_rerank("商业银行信贷", scenario="office")
        assert isinstance(results, list)
        # keyword-only 降级下可能为空, 但不抛错且为 Chunk 列表
        for c in results:
            assert c.text

    async def test_disabled_unchanged_path(self, conn):
        """disabled(默认)时走原单查询路径。"""
        cfg = {"kb": {"query_rewrite": {"enabled": False}}}
        svc = build_kb_service(conn, cfg, processor=None)
        await svc.process_document(
            content="商业银行公司信贷业务包含授信审批与贷后管理",
            filename="credit.md",
            scenario="office",
        )
        results = await svc.search_with_rerank("商业银行信贷", scenario="office")
        assert isinstance(results, list)
