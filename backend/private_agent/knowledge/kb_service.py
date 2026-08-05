"""蓝图 §4.6/§4.14/§4.15/§4.16 知识库服务编排层。

职责:
1. 编排文档处理流水线:类型识别 → chunking → embedding → 写入(§4.6)。
2. 编排检索流水线:混合检索 → reranker 精排(§4.14)。
3. 增量更新与快照生成(§4.16)。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from private_agent.knowledge.document_processor import (
    DocumentProcessor,
)
from private_agent.knowledge.embedding_service import EmbeddingService
from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.knowledge.models import Chunk, Document
from private_agent.knowledge.reranker_service import RerankerService

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """知识库服务编排层(蓝图 §4.6/§4.14/§4.15/§4.16)。

    Args:
        kb_repo: KnowledgeBaseRepo 实例。
        processor: DocumentProcessor 实例。
        embedding_service: EmbeddingService 实例。
        reranker_service: RerankerService 实例。
        config: 配置 dict 中的 kb 段。
    """

    def __init__(
        self,
        kb_repo: KnowledgeBaseRepo,
        processor: DocumentProcessor | None = None,
        embedding_service: EmbeddingService | None = None,
        reranker_service: RerankerService | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._kb_repo = kb_repo
        self._processor = processor or DocumentProcessor()
        self._embedding_service = embedding_service or EmbeddingService()
        self._reranker_service = reranker_service or RerankerService()
        self._config = config or {}

        # 检索参数
        retrieval = self._config.get("retrieval", {})
        self._rrf_k = retrieval.get("rrf_k", 60)
        self._vector_top_k = retrieval.get("vector_top_k", 20)
        self._keyword_top_k = retrieval.get("keyword_top_k", 20)
        self._final_top_k = retrieval.get("final_top_k", 5)

        # HNSW 参数
        hnsw = self._config.get("hnsw", {})
        self._ef_search = hnsw.get("ef_search", 64)

    # ══════════════════════════════════════════════════════════════════════
    # 文档处理流水线
    # ══════════════════════════════════════════════════════════════════════

    async def process_document(
        self,
        content: str,
        filename: str,
        scenario: str | None = None,
        skip_dedup: bool = False,
    ) -> tuple[int, list[Chunk]]:
        """完整文档处理流水线:类型识别 → chunking → embedding → 写入(蓝图 §4.6)。

        Args:
            content: 文档原始文本。
            filename: 文件名。
            scenario: 场景。
            skip_dedup: 跳过 hash 去重(V1.3-7.3 重索引用: 旧 chunk 已清空,
                必须强制重切, 否则走 "unchanged" 分支返回空列表)。

        Returns:
            (doc_id, chunks) 元组。
        """
        # 1. 计算 hash,判断是否重复(skip_dedup 时跳过)
        content_hash = DocumentProcessor.compute_hash(content)
        if not skip_dedup:
            existing = await self._kb_repo.get_document_by_source(filename)
            if existing is not None and existing.hash == content_hash:
                logger.info("Document '%s' unchanged, skipping", filename)
                if existing.id is not None:
                    return existing.id, await self._kb_repo.get_chunks_by_doc(
                        existing.id
                    )

        # 2. 插入文档元数据
        doc_id = await self._kb_repo.insert_document(
            Document(
                source=filename,
                content=content,
                scenario=scenario,
                hash=content_hash,
            )
        )

        # 3. chunking
        chunks = self._processor.process(content, filename, scenario)

        # 4. embedding
        try:
            vectors = await self._embedding_service.embed_chunks(chunks)
            for i, c in enumerate(chunks):
                if i < len(vectors):
                    # MVP:embedding 暂存为 Chunk 属性,DB 写入时转 BYTEA
                    c.embedding = _vector_to_bytes(vectors[i])
        except Exception as e:
            logger.warning("Embedding failed for '%s': %s, inserting without vectors", filename, e)

        # 5. 写入 kb_chunks
        await self._kb_repo.batch_insert_chunks(doc_id, scenario, chunks)

        logger.info(
            "Document '%s' processed: %d chunks, scenario=%s",
            filename, len(chunks), scenario,
        )
        return doc_id, chunks

    # ══════════════════════════════════════════════════════════════════════
    # 检索流水线
    # ══════════════════════════════════════════════════════════════════════

    async def search_with_rerank(
        self,
        query: str,
        scenario: str | None = None,
        top_k: int | None = None,
        min_similarity: float = 0.2,
    ) -> list[Chunk]:
        """完整检索流水线:query 向量化 → 混合检索 → reranker 精排(蓝图 §4.14)。

        Args:
            query: 查询文本。
            scenario: 场景过滤(可选)。
            top_k: 返回条数(默认 self._final_top_k)。
            min_similarity: 最低相似度阈值(重排后过滤)。

        Returns:
            重排后的 Chunk 列表(含 score)。
        """
        top_k = top_k or self._final_top_k

        # 1. query 向量化
        query_vector = await self._embedding_service.embed_single(query)

        # 2. 混合检索 top-20
        filters = {"scenario": scenario} if scenario else None
        candidates = await self._kb_repo.hybrid_search(
            query,
            query_vector,
            limit=self._vector_top_k,
            ef_search=self._ef_search,
            rrf_k=self._rrf_k,
            filters=filters,
        )

        if not candidates:
            logger.info("No candidates found for query: %s", query)
            return []

        # 3. reranker 精排
        reranked = await self._reranker_service.rerank(
            query, candidates, top_k=top_k * 2  # 多取一些用于 min_similarity 过滤
        )

        # 4. min_similarity 过滤
        filtered = [c for c in reranked if c.score >= min_similarity]
        return filtered[:top_k]

    # ══════════════════════════════════════════════════════════════════════
    # 增量更新与快照(Blueprint §4.16)
    # ══════════════════════════════════════════════════════════════════════

    async def update_document(
        self,
        doc_id: int,
        content: str,
        filename: str,
        scenario: str | None = None,
    ) -> tuple[int, list[Chunk]]:
        """增量更新文档:标记旧 chunk 为 inactive → 重新 chunking + embedding → 写入(蓝图 §4.16)。

        Args:
            doc_id: 文档 ID。
            content: 新文档内容。
            filename: 文件名。
            scenario: 场景。

        Returns:
            (doc_id, chunks) 元组。
        """
        # 1. 标记旧 chunk 为 inactive
        await self._kb_repo.deactivate_document(doc_id)

        # 2. 重新处理
        return await self.process_document(content, filename, scenario)

    async def get_snapshot_data(self) -> dict[str, Any]:
        """获取知识库快照数据(蓝图 §4.16)。

        Returns:
            快照 dict。
        """
        stats = await self._kb_repo.get_stats()
        return {
            "total_documents": stats["total_documents"],
            "total_chunks": stats["total_chunks"],
            "scenarios": stats["scenarios"],
            "embedding_model": self._get_embedding_model(),
            "vector_dim": self._get_vector_dim(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _get_embedding_model(self) -> str:
        """获取当前 embedding 模型名称。"""
        return self._embedding_service._model_name  # type: ignore[attr-defined]

    def _get_vector_dim(self) -> int:
        """获取当前向量维度。"""
        return self._embedding_service._get_vector_dim()  # type: ignore[attr-defined]


def _vector_to_bytes(vector: list[float]) -> bytes:
    """将 float 向量转换为 BYTEA 存储格式。

    MVP 使用 struct 打包为 float32 二进制。
    V2 启用 pgvector 后直接存储 vector 类型。

    Args:
        vector: float 列表。

    Returns:
        BYTEA 二进制数据。
    """
    import struct
    return struct.pack(f"{len(vector)}f", *vector)