"""蓝图 §4.14 Reranker 重排服务 - Worker 集成 + 降级。

职责:
1. 将 query + 候选 chunk 发送到 Worker 做 bge-reranker 重排。
2. Worker 不可用时跳过重排,直接返回混合检索 top-5。
"""
from __future__ import annotations

import logging
from typing import Any

from private_agent.knowledge.models import Chunk

logger = logging.getLogger(__name__)


class RerankerError(Exception):
    """Reranker 操作失败。"""


class RerankerService:
    """Reranker 重排服务(蓝图 §4.14)。

    MVP 实现:
    - rerank: 对候选 chunk 重排,返回 top-k。
    - Worker 不可用时跳过重排,直接返回原始候选。

    Args:
        worker_pool: 进程池(用于 run_in_executor offload)。
        top_k: 默认返回条数。
    """

    def __init__(
        self,
        worker_pool: Any | None = None,
        top_k: int = 5,
    ) -> None:
        self._worker_pool = worker_pool
        self._top_k = top_k

    async def rerank(
        self,
        query: str,
        candidates: list[Chunk],
        top_k: int | None = None,
    ) -> list[Chunk]:
        """对候选 chunk 重排,返回 top-k(蓝图 §4.14)。

        Args:
            query: 原始查询文本。
            candidates: 候选 chunk 列表(来自混合检索)。
            top_k: 返回条数(默认 self._top_k)。

        Returns:
            重排后的 Chunk 列表(含 score)。
        """
        if not candidates:
            return []

        top_k = top_k or self._top_k

        if self._worker_pool is None:
            # MVP:Worker 未就绪,跳过重排,直接返回 top-k
            logger.info(
                "Worker pool not configured, skipping reranker, "
                "returning raw top-%d", top_k
            )
            for c in candidates[:top_k]:
                c.score = 1.0
            return candidates[:top_k]

        try:
            return await self._do_rerank(query, candidates, top_k)
        except RerankerError as e:
            logger.warning(
                "Reranker unavailable, returning raw candidates: %s", e
            )
            for c in candidates[:top_k]:
                c.score = 1.0
            return candidates[:top_k]

    async def _do_rerank(
        self,
        query: str,
        candidates: list[Chunk],
        top_k: int,
    ) -> list[Chunk]:
        """实际 Worker 重排调用。

        Args:
            query: 查询文本。
            candidates: 候选列表。
            top_k: 返回条数。

        Returns:
            重排后的 Chunk 列表(含 score)。

        Raises:
            RerankerError: Worker 不可用时。
        """
        loop = __import__("asyncio", fromlist=[""]).get_event_loop()
        try:
            ranked = await loop.run_in_executor(
                self._worker_pool,
                _rerank_worker_fn,
                query,
                [c.text for c in candidates],
            )
            # ranked: [(index, score), ...] 按分数降序
            result: list[Chunk] = []
            for idx, score in ranked[:top_k]:
                chunk = candidates[idx]
                chunk.score = score
                result.append(chunk)
            return result
        except Exception as e:
            raise RerankerError(f"Worker reranker failed: {e}") from e


def _rerank_worker_fn(
    query: str, texts: list[str]
) -> list[tuple[int, float]]:
    """Worker 进程 reranker 函数(蓝图 §4.14)。

    在 Worker 进程内执行,加载 bge-reranker 模型进行重排。

    Args:
        query: 查询文本。
        texts: 候选文本列表。

    Returns:
        [(index, score), ...] 按分数降序排列。
    """
    raise NotImplementedError(
        "Worker reranker requires FlagEmbedding library. "
        "Install with: pip install FlagEmbedding"
    )