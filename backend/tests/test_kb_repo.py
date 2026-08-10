"""蓝图 §4.12/§4.13 KnowledgeBaseRepo - kb_documents + kb_chunks 表 CRUD 测试。

依赖:
- 真实 PostgreSQL (TEST_DSN)
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.knowledge.models import Chunk, Document
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


async def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
async def conn():
    await _setup_schema()
    c = await asyncpg.connect(TEST_DSN)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
def repo(conn: "asyncpg.Connection") -> KnowledgeBaseRepo:
    return KnowledgeBaseRepo(conn)


# ══════════════════════════════════════════════════════════════════════════
# Document 数据类
# ══════════════════════════════════════════════════════════════════════════


class TestDocumentDataclass:
    def test_defaults(self):
        doc = Document()
        assert doc.source == ""
        assert doc.is_active is True
        assert doc.metadata == {}

    def test_with_values(self):
        doc = Document(
            source="test.md",
            content="# Hello",
            scenario="office",
            metadata={"title": "Test"},
            hash="abc123",
        )
        assert doc.source == "test.md"
        assert doc.hash == "abc123"


# ══════════════════════════════════════════════════════════════════════════
# Chunk 数据类
# ══════════════════════════════════════════════════════════════════════════


class TestChunkDataclass:
    def test_defaults(self):
        c = Chunk()
        assert c.doc_type == "plain"
        assert c.is_active is True

    def test_with_values(self):
        c = Chunk(
            text="chunk content",
            metadata={"title": "Section 1"},
            doc_type="markdown",
            doc_id=1,
            scenario="office",
            source="test.md",
        )
        assert c.text == "chunk content"
        assert c.doc_id == 1


# ══════════════════════════════════════════════════════════════════════════
# KnowledgeBaseRepo - kb_documents CRUD
# ══════════════════════════════════════════════════════════════════════════


class TestInsertDocument:
    async def test_insert_basic(self, repo: KnowledgeBaseRepo):
        doc = Document(
            source="test.md",
            content="# Hello",
            scenario="office",
            metadata={"key": "val"},
            hash="abc123",
        )
        doc_id = await repo.insert_document(doc)
        assert isinstance(doc_id, int)
        assert doc_id > 0

    async def test_insert_and_retrieve(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        retrieved = await repo.get_document(doc_id)
        assert retrieved is not None
        assert retrieved.source == "doc.md"
        assert retrieved.scenario == "office"

    async def test_get_nonexistent(self, repo: KnowledgeBaseRepo):
        doc = await repo.get_document(99999)
        assert doc is None


class TestGetDocumentBySource:
    async def test_by_source(self, repo: KnowledgeBaseRepo):
        await repo.insert_document(Document(
            source="doc.md", content="v1", scenario="office",
        ))
        doc = await repo.get_document_by_source("doc.md")
        assert doc is not None
        assert doc.source == "doc.md"

    async def test_by_source_not_found(self, repo: KnowledgeBaseRepo):
        doc = await repo.get_document_by_source("nonexistent.md")
        assert doc is None


class TestListDocuments:
    async def test_list_all(self, repo: KnowledgeBaseRepo):
        for i in range(3):
            await repo.insert_document(Document(
                source=f"doc{i}.md", content=f"content{i}", scenario="office",
            ))
        docs = await repo.list_documents()
        assert len(docs) == 3

    async def test_list_by_scenario(self, repo: KnowledgeBaseRepo):
        await repo.insert_document(Document(
            source="doc1.md", content="c1", scenario="office",
        ))
        await repo.insert_document(Document(
            source="doc2.md", content="c2", scenario="data_analysis",
        ))
        docs = await repo.list_documents(scenario="office")
        assert len(docs) == 1
        assert docs[0].scenario == "office"

    async def test_list_limit_offset(self, repo: KnowledgeBaseRepo):
        for i in range(5):
            await repo.insert_document(Document(
                source=f"doc{i}.md", content=f"c{i}", scenario="office",
            ))
        docs = await repo.list_documents(limit=2, offset=0)
        assert len(docs) == 2


class TestDeactivateDocument:
    async def test_deactivate(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        await repo.deactivate_document(doc_id)
        # 验证文档已软删除
        doc = await repo.get_document(doc_id)
        assert doc is not None
        assert doc.is_active is False

    async def test_count_after_deactivate(self, repo: KnowledgeBaseRepo):
        d1 = await repo.insert_document(Document(
            source="doc1.md", content="c1", scenario="office",
        ))
        await repo.insert_document(Document(
            source="doc2.md", content="c2", scenario="office",
        ))
        assert await repo.count_documents() == 2
        await repo.deactivate_document(d1)
        assert await repo.count_documents() == 1


class TestCountDocuments:
    async def test_count_all(self, repo: KnowledgeBaseRepo):
        for i in range(3):
            await repo.insert_document(Document(
                source=f"doc{i}.md", content="c", scenario="office",
            ))
        assert await repo.count_documents() == 3

    async def test_count_by_scenario(self, repo: KnowledgeBaseRepo):
        await repo.insert_document(Document(
            source="d1.md", content="c", scenario="office",
        ))
        await repo.insert_document(Document(
            source="d2.md", content="c", scenario="data_analysis",
        ))
        assert await repo.count_documents(scenario="office") == 1
        assert await repo.count_documents(scenario="data_analysis") == 1


# ══════════════════════════════════════════════════════════════════════════
# KnowledgeBaseRepo - kb_chunks CRUD
# ══════════════════════════════════════════════════════════════════════════


class TestInsertChunk:
    async def test_insert_basic(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        chunk = Chunk(
            text="chunk content",
            metadata={"title": "Section 1"},
            doc_type="markdown",
            doc_id=doc_id,
            scenario="office",
            source="doc.md",
        )
        chunk_id = await repo.insert_chunk(chunk)
        assert isinstance(chunk_id, int)
        assert chunk_id > 0

    async def test_insert_without_doc_id_raises(self, repo: KnowledgeBaseRepo):
        chunk = Chunk(text="orphan chunk")
        with pytest.raises(ValueError, match="doc_id"):
            await repo.insert_chunk(chunk)


class TestBatchInsertChunks:
    async def test_batch_insert(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        chunks = [
            Chunk(text=f"chunk{i}", doc_type="markdown", doc_id=doc_id)
            for i in range(3)
        ]
        ids = await repo.batch_insert_chunks(doc_id, "office", chunks)
        assert len(ids) == 3
        assert all(isinstance(i, int) and i > 0 for i in ids)

    async def test_batch_insert_and_query(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        chunks = [
            Chunk(text=f"chunk{i}", doc_type="markdown", doc_id=doc_id)
            for i in range(3)
        ]
        await repo.batch_insert_chunks(doc_id, "office", chunks)
        retrieved = await repo.get_chunks_by_doc(doc_id)
        assert len(retrieved) == 3
        assert retrieved[0].text == "chunk0"


class TestGetChunksByDoc:
    async def test_empty_doc(self, repo: KnowledgeBaseRepo):
        chunks = await repo.get_chunks_by_doc(99999)
        assert chunks == []

    async def test_excludes_inactive(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        await repo.batch_insert_chunks(doc_id, "office", [
            Chunk(text="active", doc_type="markdown", doc_id=doc_id),
            Chunk(text="inactive", doc_type="markdown", doc_id=doc_id),
        ])
        # 手动标记一个为 inactive
        await repo._conn.execute(
            "UPDATE kb_chunks SET is_active = FALSE WHERE chunk_text = 'inactive'"
        )
        chunks = await repo.get_chunks_by_doc(doc_id)
        assert len(chunks) == 1
        assert chunks[0].text == "active"


class TestGetChunkCount:
    async def test_count_chunks(self, repo: KnowledgeBaseRepo):
        doc_id = await repo.insert_document(Document(
            source="doc.md", content="content", scenario="office",
        ))
        await repo.batch_insert_chunks(doc_id, "office", [
            Chunk(text=f"c{i}", doc_type="markdown", doc_id=doc_id)
            for i in range(5)
        ])
        assert await repo.get_chunk_count() == 5
        assert await repo.get_chunk_count(doc_id=doc_id) == 5

    async def test_count_zero(self, repo: KnowledgeBaseRepo):
        assert await repo.get_chunk_count() == 0


# ══════════════════════════════════════════════════════════════════════════
# KnowledgeBaseRepo - get_stats
# ══════════════════════════════════════════════════════════════════════════


class TestGetStats:
    async def test_empty_stats(self, repo: KnowledgeBaseRepo):
        stats = await repo.get_stats()
        assert stats["total_documents"] == 0
        assert stats["total_chunks"] == 0
        assert stats["scenarios"] == {}

    async def test_stats_with_data(self, repo: KnowledgeBaseRepo):
        d1 = await repo.insert_document(Document(
            source="doc1.md", content="c1", scenario="office",
        ))
        d2 = await repo.insert_document(Document(
            source="doc2.md", content="c2", scenario="data_analysis",
        ))
        await repo.batch_insert_chunks(d1, "office", [
            Chunk(text="c1", doc_type="markdown", doc_id=d1),
        ])
        await repo.batch_insert_chunks(d2, "data_analysis", [
            Chunk(text="c2", doc_type="markdown", doc_id=d2),
        ])
        stats = await repo.get_stats()
        assert stats["total_documents"] == 2
        assert stats["total_chunks"] == 2
        assert stats["scenarios"]["office"]["docs"] == 1
        assert stats["scenarios"]["office"]["chunks"] == 1
        assert stats["scenarios"]["data_analysis"]["docs"] == 1
        assert stats["scenarios"]["data_analysis"]["chunks"] == 1