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


def _as_dict(value: Any) -> dict[str, Any]:
    """JSONB 值统一转 dict(asyncpg 未注册 JSONB 类型码时返回 str, 用前必须解析)。"""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            parsed = __import__("json").loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    if isinstance(value, dict):
        return value
    return {}


# 2026-08-15(M2 P2-14): 查询重写 —— 启发式查询扩展(零 LLM 依赖)
_QUERY_STOPWORDS = frozenset(
    "的了吗呢是和在就都而及与或如果那么因为所以但是等"
    "我你他她它们这那什么怎么如何请帮问哪些有"
)
_QUERY_PUNCT = frozenset(
    "，。？！、；：""''（）《》【】—…·,.:;!?()[]{}<>\"'-_"
)


def _expand_queries(query: str, max_expansions: int = 3) -> list[str]:
    """生成查询扩展变体(含原查询), 最多 max_expansions 个, 保序去重。

    变体策略(纯启发式, 无 LLM/分词依赖):
      1. 原查询(始终保留)
      2. 去停用词 + 去标点的核心词串(≥2 字才作为变体)
      3. 含空格的多词查询 → 词拼接变体(中英混排时保留实体完整性)

    Args:
        query: 原始查询。
        max_expansions: 扩展总数上限(含原查询, ≥1)。

    Returns:
        去重后的查询列表(首个恒为原查询)。
    """
    q = (query or "").strip()
    if not q:
        return [q]
    max_expansions = max(int(max_expansions), 1)
    variants: list[str] = [q]

    # 变体 2: 去停用词/标点核心词
    core = "".join(c for c in q if c not in _QUERY_PUNCT and not c.isspace())
    core_no_stop = "".join(c for c in core if c not in _QUERY_STOPWORDS)
    if core_no_stop and core_no_stop != q and len(core_no_stop) >= 2:
        variants.append(core_no_stop)

    # 变体 3: 多词拼接(保留原序, 中英混排实体完整)
    words = [w for w in q.split() if w]
    if len(words) > 1:
        joined = "".join(words)
        if joined not in variants and len(joined) >= 2:
            variants.append(joined)

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= max_expansions:
            break
    return out or [q]


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

        # 2026-08-15(M2 P2-14): 查询重写(默认关闭, 行为与旧版一致)
        qr = self._config.get("query_rewrite", {})
        self._qr_enabled = bool(qr.get("enabled", False))
        self._qr_max = int(qr.get("max_expansions", 3))

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

        # 4. embedding(0.5.1 C1: 维度校验失败 → EmbeddingError 传播,
        #   拒绝入库非法向量; worker 故障已在 EmbeddingService 内部降级为
        #   全 0 mock → 检索 keyword-only, 不在此处吞异常)
        vectors = await self._embedding_service.embed_chunks(chunks)
        for i, c in enumerate(chunks):
            if i < len(vectors):
                # MVP:embedding 暂存为 Chunk 属性,DB 写入时转 pgvector 文本
                c.embedding = _vector_to_bytes(vectors[i])

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

        # 2026-08-15(M2 P2-14): 查询重写 —— enabled 时扩展查询并分别检索,
        # 按 chunk_id 合并去重(保留最高分)后统一重排; 关闭时走原路径零变化。
        queries = (
            _expand_queries(query, self._qr_max)
            if self._qr_enabled
            else [query]
        )
        if len(queries) == 1:
            return await self._search_single(query, scenario, top_k, min_similarity)

        filters = {"scenario": scenario} if scenario else None
        merged: dict[int, Chunk] = {}
        for q in queries:
            try:
                qv = await self._embedding_service.embed_single(q)
                cands = await self._kb_repo.hybrid_search(
                    q,
                    qv,
                    limit=self._vector_top_k,
                    ef_search=self._ef_search,
                    rrf_k=self._rrf_k,
                    filters=filters,
                )
            except Exception:  # noqa: BLE001 - 单变体失败不拖垮整体
                logger.warning("query rewrite variant failed: %r", q)
                continue
            for c in cands:
                cid = c.chunk_id
                if cid is None:
                    continue
                if cid not in merged or (c.score or 0) > (merged[cid].score or 0):
                    merged[cid] = c
        candidates = list(merged.values())
        if not candidates:
            return []

        reranked = await self._reranker_service.rerank(
            query, candidates, top_k=top_k * 2
        )
        filtered = [c for c in reranked if c.score >= min_similarity]
        return filtered[:top_k]

    async def _search_single(
        self,
        query: str,
        scenario: str | None = None,
        top_k: int = 5,
        min_similarity: float = 0.2,
    ) -> list[Chunk]:
        """单查询检索(原路径, 查询重写关闭时使用)。"""
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

    # ══════════════════════════════════════════════════════════════════════
    # 2026-08-15(M2 P2-15): KB 版本快照 / 对比 / 回滚
    # ══════════════════════════════════════════════════════════════════════

    async def build_snapshot_payload(self) -> dict[str, Any]:
        """收集当前知识库完整内容(文档 + 分块含 embedding)为可回滚快照。

        payload 结构:
        {
          embedding_model, vector_dim, timestamp, note,
          documents: [
            {doc_id, source, content, scenario, metadata, hash,
             chunks: [{chunk_text, scenario, source, metadata, embedding_text}]}
          ]
        }
        embedding_text 为 pgvector 文本("[a,b,...]"), 回滚时直接复用,
        无需重新 embedding(精确恢复)。
        """
        docs = await self._kb_repo.list_all_documents()
        documents = []
        for d in docs:
            chunks = await self._kb_repo.list_chunks_with_embedding(d["id"])
            documents.append({
                "doc_id": d["id"],
                "source": d["source"],
                "content": d["content"],
                "scenario": d["scenario"],
                "metadata": _as_dict(d["metadata"]),
                "hash": d["hash"],
                "chunks": [
                    {
                        "chunk_text": c["chunk_text"],
                        "scenario": c["scenario"],
                        "source": c["source"],
                        "metadata": _as_dict(c["metadata"]),
                        "embedding_text": c["embedding_text"],
                    }
                    for c in chunks
                ],
            })
        return {
            "embedding_model": self._get_embedding_model(),
            "vector_dim": self._get_vector_dim(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "documents": documents,
        }

    async def save_snapshot(
        self,
        snapshot_repo,
        *,
        version: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """保存知识库版本快照(scope='kb')。

        Args:
            snapshot_repo: VersionSnapshotRepo 实例。
            version: 版本号(默认 UTC 时间戳 %Y%m%d%H%M%S)。
            note: 备注(如"自动: upload xxx.pdf")。

        Returns:
            {"version": str, "documents": int, "chunks": int}
        """
        if version is None:
            version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        payload = await self.build_snapshot_payload()
        payload["note"] = note
        snapshot_id = await snapshot_repo.save(
            scope="kb", version=version, payload=payload
        )
        total_chunks = sum(len(d["chunks"]) for d in payload["documents"])
        return {
            "id": snapshot_id,
            "version": version,
            "documents": len(payload["documents"]),
            "chunks": total_chunks,
        }

    async def compare_versions(
        self,
        snapshot_repo,
        from_version: str,
        to_version: str,
    ) -> dict[str, Any]:
        """对比两个 KB 版本快照(文档级 diff)。

        Returns:
            {"from": v1, "to": v2,
             "added": [source...], "removed": [source...], "modified": [source...],
             "unchanged": n, "summary": "新增 N · 删除 M · 修改 K"}
        """
        v1 = await snapshot_repo.get(scope="kb", version=from_version)
        v2 = await snapshot_repo.get(scope="kb", version=to_version)
        if v1 is None or v2 is None:
            raise ValueError("snapshot_not_found")

        def _index(payload: dict) -> dict[str, dict]:
            return {
                d["source"]: d
                for d in (payload or {}).get("documents", [])
            }

        # 注: VersionSnapshotRepo.get 返回 payload 本身(非 {payload: ...} 包装)
        idx1, idx2 = _index(v1), _index(v2)
        added = [s for s in idx2 if s not in idx1]
        removed = [s for s in idx1 if s not in idx2]
        modified = [
            s for s in idx1
            if s in idx2 and idx1[s].get("hash") != idx2[s].get("hash")
        ]
        unchanged = [s for s in idx1 if s in idx2 and idx1[s].get("hash") == idx2[s].get("hash")]
        return {
            "from": from_version,
            "to": to_version,
            "added": added,
            "removed": removed,
            "modified": modified,
            "unchanged": len(unchanged),
            "summary": f"新增 {len(added)} · 删除 {len(removed)} · 修改 {len(modified)}",
        }

    async def rollback_to(
        self,
        snapshot_repo,
        version: str,
        *,
        conn=None,
    ) -> dict[str, Any]:
        """回滚知识库到指定版本快照。

        事务内: 清空当前全部文档/分块 → 按快照重建(含 embedding 文本直插,
        不重新 embedding)。快照查找失败或 payload 缺 documents 时抛 ValueError。

        Args:
            snapshot_repo: VersionSnapshotRepo 实例。
            version: 目标版本号。
            conn: 复用连接(事务由调用方控制; 为 None 时内部开启事务)。

        Returns:
            {"version": str, "documents": int, "chunks": int}
        """
        snap = await snapshot_repo.get(scope="kb", version=version)
        if snap is None:
            raise ValueError("snapshot_not_found")
        # 注: get 返回 payload 本身
        payload = snap or {}
        documents = payload.get("documents", [])
        if not isinstance(documents, list):
            raise ValueError("snapshot_payload_invalid")

        if conn is None:
            conn = self._kb_repo._conn
            async with conn.transaction():
                await self._kb_repo.truncate_all()
                return await self._apply_snapshot(documents)
        # 调用方已开事务(conn 传入): 直接应用
        await self._kb_repo.truncate_all()
        return await self._apply_snapshot(documents)

    async def _apply_snapshot(self, documents: list[dict]) -> dict[str, Any]:
        """将快照 documents 重建到 kb_documents/kb_chunks(须在事务内)。"""
        total_chunks = 0
        for d in documents:
            doc = Document(
                source=d.get("source") or "",
                content=d.get("content"),
                scenario=d.get("scenario"),
                metadata=_as_dict(d.get("metadata")),
                hash=d.get("hash"),
            )
            doc_id = await self._kb_repo.insert_document(doc)
            for c in d.get("chunks", []):
                embed_text = c.get("embedding_text")
                if not embed_text:
                    continue  # 缺 embedding 的分块跳过(与插入侧全 0 语义一致)
                vec_text = (
                    embed_text
                    if embed_text.startswith("[")
                    else f"[{embed_text}]"
                )
                await self._kb_repo._conn.execute(
                    """
                    INSERT INTO kb_chunks (doc_id, scenario, source, chunk_text,
                                           metadata, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6::vector)
                    """,
                    doc_id,
                    c.get("scenario"),
                    c.get("source"),
                    c.get("chunk_text") or "",
                    __import__("json").dumps(_as_dict(c.get("metadata"))),
                    vec_text,
                )
                total_chunks += 1
        return {"documents": len(documents), "chunks": total_chunks}

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