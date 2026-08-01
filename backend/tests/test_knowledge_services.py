"""蓝图 §4.10/§4.13/§4.14 知识库服务层测试。

测试:
- rrf_fusion 纯函数
- EmbeddingService(mock Worker)
- RerankerService(mock Worker)
- KnowledgeBaseService(mock 依赖)
"""
from __future__ import annotations

import pytest

from private_agent.knowledge.embedding_service import EmbeddingService
from private_agent.knowledge.kb_repo import rrf_fusion
from private_agent.knowledge.kb_service import KnowledgeBaseService, _vector_to_bytes
from private_agent.knowledge.models import Chunk, Document
from private_agent.knowledge.reranker_service import RerankerService


# ══════════════════════════════════════════════════════════════════════════
# _vector_to_bytes
# ══════════════════════════════════════════════════════════════════════════


class TestVectorToBytes:
    def test_simple_vector(self):
        vec = [0.0, 1.0, -1.0, 0.5]
        data = _vector_to_bytes(vec)
        assert isinstance(data, bytes)
        assert len(data) == 4 * 4  # 4 floats * 4 bytes

    def test_empty_vector(self):
        vec: list[float] = []
        data = _vector_to_bytes(vec)
        assert data == b""


# ══════════════════════════════════════════════════════════════════════════
# rrf_fusion
# ══════════════════════════════════════════════════════════════════════════


class TestRrfFusion:
    def test_empty_results(self):
        result = rrf_fusion([], [])
        assert result == []

    def test_only_vector(self):
        chunks = [Chunk(chunk_id=i, text=f"v{i}") for i in range(3)]
        result = rrf_fusion(chunks, [])
        assert len(result) == 3
        assert result[0].chunk_id == 0

    def test_only_keyword(self):
        chunks = [Chunk(chunk_id=i, text=f"k{i}") for i in range(3)]
        result = rrf_fusion([], chunks)
        assert len(result) == 3

    def test_intersection_gets_boost(self):
        """同时出现在两个结果集中的 chunk 获得更高 RRF 分数。"""
        shared = Chunk(chunk_id=1, text="shared")
        v = [Chunk(chunk_id=0, text="v0"), shared, Chunk(chunk_id=2, text="v2")]
        k = [shared, Chunk(chunk_id=3, text="k3")]
        result = rrf_fusion(v, k)
        # shared(id=1) 在两个结果集中都出现,RRF 分数最高
        assert result[0].chunk_id == 1

    def test_respects_limit(self):
        v = [Chunk(chunk_id=i, text=f"v{i}") for i in range(10)]
        k = [Chunk(chunk_id=i + 10, text=f"k{i}") for i in range(10)]
        result = rrf_fusion(v, k, limit=5)
        assert len(result) == 5


# ══════════════════════════════════════════════════════════════════════════
# EmbeddingService
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_embed_chunks_empty():
    svc = EmbeddingService()
    result = await svc.embed_chunks([])
    assert result == []


@pytest.mark.asyncio
async def test_embed_chunks_no_worker_returns_mock():
    """Worker 未配置时返回 mock 向量。"""
    svc = EmbeddingService()
    chunks = [Chunk(text="hello"), Chunk(text="world")]
    vectors = await svc.embed_chunks(chunks)
    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)  # bge-m3 默认 1024 维
    assert all(v == [0.0] * 1024 for v in vectors)  # mock 全 0


@pytest.mark.asyncio
async def test_embed_single_no_worker_returns_mock():
    """单条 query 返回 mock 向量。"""
    svc = EmbeddingService()
    vec = await svc.embed_single("test query")
    assert len(vec) == 1024


@pytest.mark.asyncio
async def test_embed_with_fallback_returns_mock():
    """降级时返回 mock 向量。"""
    svc = EmbeddingService()
    chunks = [Chunk(text="hello")]
    vectors = await svc.embed_with_fallback(chunks)
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024


def test_get_vector_dim_default():
    svc = EmbeddingService()
    assert svc._get_vector_dim() == 1024


def test_get_vector_dim_light():
    svc = EmbeddingService(config={"local_light": "bge-small", "local_default": "bge-small"})
    svc._model_name = "bge-small"
    assert svc._get_vector_dim() == 384


def test_clear_cache():
    svc = EmbeddingService()
    svc.clear_query_cache()  # 不应报错


# ══════════════════════════════════════════════════════════════════════════
# RerankerService
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rerank_empty():
    svc = RerankerService()
    result = await svc.rerank("query", [])
    assert result == []


@pytest.mark.asyncio
async def test_rerank_no_worker_returns_raw():
    """Worker 未配置时跳过重排,直接返回原始 top-k。"""
    svc = RerankerService()
    candidates = [Chunk(text=f"c{i}", chunk_id=i) for i in range(5)]
    result = await svc.rerank("query", candidates, top_k=3)
    assert len(result) == 3
    assert all(c.score == 1.0 for c in result)


@pytest.mark.asyncio
async def test_rerank_no_worker_respects_limit():
    svc = RerankerService()
    candidates = [Chunk(text=f"c{i}", chunk_id=i) for i in range(10)]
    result = await svc.rerank("query", candidates, top_k=2)
    assert len(result) == 2


# ══════════════════════════════════════════════════════════════════════════
# KnowledgeBaseService
# ══════════════════════════════════════════════════════════════════════════


class _MockKbRepo:
    """Mock KnowledgeBaseRepo for testing KnowledgeBaseService."""

    def __init__(self) -> None:
        self.documents: dict[int, Document] = {}
        self.chunks: dict[int, list[Chunk]] = {}
        self._next_doc_id = 1
        self._deactivated: list[int] = []

    async def get_document_by_source(self, source: str) -> Document | None:
        for doc in self.documents.values():
            if doc.source == source and doc.is_active:
                return doc
        return None

    async def insert_document(self, doc: Document) -> int:
        doc_id = self._next_doc_id
        self._next_doc_id += 1
        doc.id = doc_id
        self.documents[doc_id] = doc
        self.chunks[doc_id] = []
        return doc_id

    async def batch_insert_chunks(
        self, doc_id: int, scenario: str | None, chunks: list[Chunk]
    ) -> list[int]:
        ids: list[int] = []
        for i, c in enumerate(chunks):
            c.chunk_id = i + 1
            c.doc_id = doc_id
            ids.append(c.chunk_id)
        self.chunks[doc_id] = chunks
        return ids

    async def get_chunks_by_doc(self, doc_id: int) -> list[Chunk]:
        return self.chunks.get(doc_id, [])

    async def deactivate_document(self, doc_id: int) -> None:
        if doc_id in self.documents:
            self.documents[doc_id].is_active = False
        self._deactivated.append(doc_id)

    async def get_stats(self) -> dict:
        return {
            "total_documents": len(self.documents),
            "total_chunks": sum(len(c) for c in self.chunks.values()),
            "scenarios": {},
        }

    async def hybrid_search(self, query, query_vector, **kwargs):
        return []


@pytest.mark.asyncio
async def test_process_document():
    repo = _MockKbRepo()
    svc = KnowledgeBaseService(kb_repo=repo)
    doc_id, chunks = await svc.process_document(
        content="# Hello\n\nWorld.",
        filename="test.md",
        scenario="office",
    )
    assert doc_id > 0
    assert len(chunks) >= 1
    assert chunks[0].doc_type == "markdown"
    assert chunks[0].scenario == "office"


@pytest.mark.asyncio
async def test_process_document_duplicate_skips():
    """相同内容的文档应跳过处理。"""
    repo = _MockKbRepo()
    svc = KnowledgeBaseService(kb_repo=repo)
    doc_id1, _ = await svc.process_document(
        content="Hello", filename="test.md"
    )
    doc_id2, _ = await svc.process_document(
        content="Hello", filename="test.md"
    )
    # 不重复创建文档
    assert doc_id1 == doc_id2


@pytest.mark.asyncio
async def test_process_document_changed_content():
    """内容变更时重新处理。"""
    repo = _MockKbRepo()
    svc = KnowledgeBaseService(kb_repo=repo)
    doc_id1, _ = await svc.process_document(
        content="Hello", filename="test.md"
    )
    doc_id2, _ = await svc.process_document(
        content="Hello World", filename="test.md"
    )
    # 内容变更,创建新文档
    assert doc_id2 > doc_id1


@pytest.mark.asyncio
async def test_search_with_rerank_empty():
    repo = _MockKbRepo()
    svc = KnowledgeBaseService(kb_repo=repo)
    result = await svc.search_with_rerank("test query")
    assert result == []


@pytest.mark.asyncio
async def test_update_document():
    repo = _MockKbRepo()
    svc = KnowledgeBaseService(kb_repo=repo)
    doc_id, _ = await svc.process_document(
        content="v1", filename="test.md"
    )
    await svc.update_document(
        doc_id, content="v2", filename="test.md"
    )
    assert doc_id in repo._deactivated


@pytest.mark.asyncio
async def test_get_snapshot_data():
    repo = _MockKbRepo()
    svc = KnowledgeBaseService(kb_repo=repo)
    snapshot = await svc.get_snapshot_data()
    assert "total_documents" in snapshot
    assert "embedding_model" in snapshot
    assert "vector_dim" in snapshot
    assert "timestamp" in snapshot