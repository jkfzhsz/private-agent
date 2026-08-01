"""蓝图 §4.12/§4.13 kb_documents + kb_chunks 表 CRUD 操作。

表结构:
- kb_documents: 文档元数据(source/content/scenario/metadata/hash)
- kb_chunks: 分块 + 向量(text/metadata/embedding/scenario/source)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from private_agent.knowledge.models import Chunk, Document

__all__ = ["KnowledgeBaseRepo", "rrf_fusion"]


def rrf_fusion(
    vector_results: list[Chunk],
    keyword_results: list[Chunk],
    k: int = 60,
    limit: int = 20,
) -> list[Chunk]:
    """RRF(Reciprocal Rank Fusion)融合策略(蓝图 §4.13)。

    基于排名融合,对分数尺度不敏感,实现简单且效果稳定。

    Args:
        vector_results: 向量检索结果。
        keyword_results: 关键词检索结果。
        k: RRF 常数(默认 60)。
        limit: 返回条数。

    Returns:
        RRF 融合后的 Chunk 列表。
    """
    scores: dict[int, float] = {}
    chunks: dict[int, Chunk] = {}

    for rank, chunk in enumerate(vector_results):
        if chunk.chunk_id is not None:
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1.0 / (k + rank + 1)
            chunks[chunk.chunk_id] = chunk

    for rank, chunk in enumerate(keyword_results):
        if chunk.chunk_id is not None:
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1.0 / (k + rank + 1)
            chunks[chunk.chunk_id] = chunk

    # 按 RRF 分数降序,取 top-limit
    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    return [chunks[cid] for cid in sorted_ids[:limit]]


class KnowledgeBaseRepo:
    """kb_documents + kb_chunks 表 CRUD 操作(蓝图 §4.12/§4.13)。

    Args:
        conn: asyncpg 连接。
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ══════════════════════════════════════════════════════════════════════
    # kb_documents CRUD
    # ══════════════════════════════════════════════════════════════════════

    async def insert_document(self, doc: Document) -> int:
        """插入文档元数据,返回 id(蓝图 §4.12)。

        Args:
            doc: Document 实例(source/content/scenario/metadata/hash)。

        Returns:
            新记录的 id。
        """
        return await self._conn.fetchval(
            """
            INSERT INTO kb_documents (source, content, scenario, metadata, hash)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            doc.source,
            doc.content,
            doc.scenario,
            json.dumps(doc.metadata),
            doc.hash,
        )

    async def get_document(self, doc_id: int) -> Document | None:
        """按 id 查询文档元数据。

        Args:
            doc_id: 文档 ID。

        Returns:
            Document 实例,不存在时返回 None。
        """
        row = await self._conn.fetchrow(
            """
            SELECT id, source, content, scenario, metadata, hash,
                   is_active, created_at, updated_at
            FROM kb_documents
            WHERE id = $1
            """,
            doc_id,
        )
        if row is None:
            return None
        return self._row_to_document(row)

    async def get_document_by_source(
        self, source: str
    ) -> Document | None:
        """按 source 查询活跃文档(增量更新判断用)。

        Args:
            source: 文件名/URL。

        Returns:
            Document 实例,不存在时返回 None。
        """
        row = await self._conn.fetchrow(
            """
            SELECT id, source, content, scenario, metadata, hash,
                   is_active, created_at, updated_at
            FROM kb_documents
            WHERE source = $1 AND is_active = TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            source,
        )
        if row is None:
            return None
        return self._row_to_document(row)

    async def list_documents(
        self,
        scenario: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        """列出活跃文档(蓝图 §4.12 文档管理)。

        Args:
            scenario: 场景过滤(可选)。
            limit: 返回条数。
            offset: 偏移量。

        Returns:
            Document 列表。
        """
        if scenario:
            rows = await self._conn.fetch(
                """
                SELECT id, source, content, scenario, metadata, hash,
                       is_active, created_at, updated_at
                FROM kb_documents
                WHERE is_active = TRUE AND scenario = $1
                ORDER BY updated_at DESC
                LIMIT $2 OFFSET $3
                """,
                scenario,
                limit,
                offset,
            )
        else:
            rows = await self._conn.fetch(
                """
                SELECT id, source, content, scenario, metadata, hash,
                       is_active, created_at, updated_at
                FROM kb_documents
                WHERE is_active = TRUE
                ORDER BY updated_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [self._row_to_document(r) for r in rows]

    async def deactivate_document(self, doc_id: int) -> None:
        """软删除文档(蓝图 §4.16 增量更新 + 保留历史)。

        Args:
            doc_id: 文档 ID。
        """
        async with self._conn.transaction():
            await self._conn.execute(
                "UPDATE kb_documents SET is_active = FALSE WHERE id = $1",
                doc_id,
            )
            # 同时软删除对应 chunk
            await self._conn.execute(
                "UPDATE kb_chunks SET is_active = FALSE WHERE doc_id = $1",
                doc_id,
            )

    async def count_documents(
        self, scenario: str | None = None
    ) -> int:
        """统计活跃文档数。

        Args:
            scenario: 场景过滤(可选)。

        Returns:
            活跃文档总数。
        """
        if scenario:
            return await self._conn.fetchval(
                "SELECT COUNT(*) FROM kb_documents WHERE is_active = TRUE AND scenario = $1",
                scenario,
            ) or 0
        return await self._conn.fetchval(
            "SELECT COUNT(*) FROM kb_documents WHERE is_active = TRUE"
        ) or 0

    # ══════════════════════════════════════════════════════════════════════
    # kb_chunks CRUD
    # ══════════════════════════════════════════════════════════════════════

    async def insert_chunk(self, chunk: Chunk) -> int:
        """插入单条分块,返回 id(蓝图 §4.12)。

        Args:
            chunk: Chunk 实例(doc_id/scenario/source/chunk_text/metadata)。

        Returns:
            新记录的 id。
        """
        if chunk.doc_id is None:
            raise ValueError("chunk.doc_id is required")
        return await self._conn.fetchval(
            """
            INSERT INTO kb_chunks (doc_id, scenario, source, chunk_text, metadata, embedding)
            VALUES ($1, $2, $3, $4, $5, array_fill(0.0, ARRAY[1024])::vector)
            RETURNING id
            """,
            chunk.doc_id,
            chunk.scenario,
            chunk.source,
            chunk.text,
            json.dumps(chunk.metadata),
        )

    async def batch_insert_chunks(
        self,
        doc_id: int,
        scenario: str | None,
        chunks: list[Chunk],
    ) -> list[int]:
        """批量插入分块(蓝图 §4.6 文档处理流水线)。

        Args:
            doc_id: 文档 ID。
            scenario: 场景。
            chunks: Chunk 列表。

        Returns:
            新记录的 id 列表。
        """
        ids: list[int] = []
        async with self._conn.transaction():
            for c in chunks:
                c.doc_id = doc_id
                c.scenario = scenario
                mid = await self.insert_chunk(c)
                ids.append(mid)
        return ids

    async def get_chunks_by_doc(
        self, doc_id: int
    ) -> list[Chunk]:
        """按文档 ID 查询活跃分块(蓝图 §4.16 增量更新)。

        Args:
            doc_id: 文档 ID。

        Returns:
            Chunk 列表。
        """
        rows = await self._conn.fetch(
            """
            SELECT id, doc_id, scenario, source, chunk_text, metadata,
                   created_at, is_active
            FROM kb_chunks
            WHERE doc_id = $1 AND is_active = TRUE
            ORDER BY id
            """,
            doc_id,
        )
        return [self._row_to_chunk(r) for r in rows]

    async def get_chunk_count(self, doc_id: int | None = None) -> int:
        """统计活跃分块数。

        Args:
            doc_id: 文档 ID(可选,指定时统计单个文档)。

        Returns:
            活跃分块总数。
        """
        if doc_id is not None:
            return await self._conn.fetchval(
                "SELECT COUNT(*) FROM kb_chunks WHERE doc_id = $1 AND is_active = TRUE",
                doc_id,
            ) or 0
        return await self._conn.fetchval(
            "SELECT COUNT(*) FROM kb_chunks WHERE is_active = TRUE"
        ) or 0

    # ══════════════════════════════════════════════════════════════════════
    # 统计 & 快照辅助
    # ══════════════════════════════════════════════════════════════════════

    async def get_stats(self) -> dict[str, Any]:
        """获取知识库统计信息(蓝图 §4.16 快照内容)。

        Returns:
            {
                "total_documents": int,
                "total_chunks": int,
                "scenarios": {scenario: {"docs": int, "chunks": int}},
            }
        """
        total_docs = await self.count_documents()
        total_chunks = await self.get_chunk_count()

        # 按场景统计
        rows = await self._conn.fetch(
            """
            SELECT scenario, COUNT(*) AS cnt
            FROM kb_documents
            WHERE is_active = TRUE AND scenario IS NOT NULL
            GROUP BY scenario
            """
        )
        scenarios: dict[str, dict[str, int]] = {}
        for row in rows:
            s = row["scenario"]
            scenarios[s] = {"docs": row["cnt"], "chunks": 0}

        # 按场景统计 chunk 数
        chunk_rows = await self._conn.fetch(
            """
            SELECT scenario, COUNT(*) AS cnt
            FROM kb_chunks
            WHERE is_active = TRUE AND scenario IS NOT NULL
            GROUP BY scenario
            """
        )
        for row in chunk_rows:
            s = row["scenario"]
            if s in scenarios:
                scenarios[s]["chunks"] = row["cnt"]
            else:
                scenarios[s] = {"docs": 0, "chunks": row["cnt"]}

        return {
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "scenarios": scenarios,
        }

    # ══════════════════════════════════════════════════════════════════════
    # 检索:向量 + 关键词 + 混合(RRF)
    # ══════════════════════════════════════════════════════════════════════

    async def vector_search(
        self,
        query_vector: list[float],
        limit: int = 20,
        ef_search: int = 64,
        filters: dict[str, str | None] | None = None,
    ) -> list[Chunk]:
        """向量检索:cosine 相似度 top-k(蓝图 §4.11/§4.13)。

        MVP 简化:使用 cosine 相似度直接计算(embedding 字段为 BYTEA 占位时返回空)。
        V2 启用 pgvector HNSW 索引。

        Args:
            query_vector: 查询向量。
            limit: 返回条数。
            ef_search: HNSW ef_search 参数(运行时调参)。
            filters: 过滤条件({scenario, source})。

        Returns:
            Chunk 列表(含 score)。
        """
        # MVP:embedding 字段为 BYTEA 占位,返回空
        # V2:ALTER 为 vector(1024) 后启用 HNSW 索引
        _ = ef_search  # V2 使用
        _ = query_vector  # V2 使用
        return []

    async def keyword_search(
        self,
        query: str,
        limit: int = 20,
        filters: dict[str, str | None] | None = None,
    ) -> list[Chunk]:
        """关键词检索:BM25 全文检索(蓝图 §4.13)。

        MVP 简化:使用 ILIKE 模糊匹配(Postgres 全文索引未就绪)。
        V2 启用 tsvector GIN 索引。

        Args:
            query: 查询文本。
            limit: 返回条数。
            filters: 过滤条件({scenario, source})。

        Returns:
            Chunk 列表(含 score)。
        """
        sql = """
            SELECT id, doc_id, scenario, source, chunk_text, metadata,
                   created_at, is_active
            FROM kb_chunks
            WHERE is_active = TRUE
              AND chunk_text ILIKE $1
        """
        params: list[Any] = [f"%{query}%"]

        if filters:
            if filters.get("scenario"):
                sql += f" AND scenario = ${len(params) + 1}"
                params.append(filters["scenario"])
            if filters.get("source"):
                sql += f" AND source = ${len(params) + 1}"
                params.append(filters["source"])

        sql += f" ORDER BY id LIMIT {limit}"

        rows = await self._conn.fetch(sql, *params)
        chunks = [self._row_to_chunk(r) for r in rows]
        # 按匹配度分配分数(简单实现:越靠前越高)
        total = len(chunks)
        for i, c in enumerate(chunks):
            c.score = 1.0 - (i / total) if total > 0 else 0.0
        return chunks

    async def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        limit: int = 20,
        ef_search: int = 64,
        rrf_k: int = 60,
        filters: dict[str, str | None] | None = None,
    ) -> list[Chunk]:
        """混合检索:向量 + 关键词 + RRF 融合(蓝图 §4.13)。

        Args:
            query: 查询文本。
            query_vector: 查询向量。
            limit: 最终返回条数。
            ef_search: HNSW ef_search 参数。
            rrf_k: RRF 常数(默认 60)。
            filters: 过滤条件。

        Returns:
            RRF 融合后的 Chunk 列表。
        """
        # 并行执行向量检索与关键词检索
        vector_results, keyword_results = await asyncio.gather(
            self.vector_search(
                query_vector, limit=limit, ef_search=ef_search, filters=filters
            ),
            self.keyword_search(query, limit=limit, filters=filters),
        )
        # RRF 融合
        return rrf_fusion(
            vector_results, keyword_results, k=rrf_k, limit=limit
        )

    # ══════════════════════════════════════════════════════════════════════
    # 内部 helpers
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _row_to_document(row: asyncpg.Record) -> Document:
        return Document(
            id=row["id"],
            source=row["source"],
            content=row["content"],
            scenario=row["scenario"],
            metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
            hash=row["hash"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_chunk(row: asyncpg.Record) -> Chunk:
        return Chunk(
            chunk_id=row["id"],
            doc_id=row["doc_id"],
            scenario=row["scenario"],
            source=row["source"],
            text=row["chunk_text"],
            metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
            is_active=row["is_active"],
        )