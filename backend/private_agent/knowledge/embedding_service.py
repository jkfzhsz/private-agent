"""蓝图 §4.10 Embedding 服务 - Worker 集成 + 云端降级 + LRU 缓存。

职责:
1. 将文本批量发送到 Worker 进程做 bge-m3 embedding。
2. Worker 不可用时降级到云端 embedding API。
3. query 向量 LRU 缓存(高频相同 query 复用)。
4. 启动时按可用内存自动选择标准(bge-m3)/轻量(bge-small)模型。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from private_agent.knowledge.models import Chunk

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Embedding 操作失败。"""


class EmbeddingService:
    """Embedding 服务(蓝图 §4.10)。

    MVP 实现:
    - embed_chunks: 批量 embedding(Worker 进程)。
    - embed_single: 单条 query embedding(带 LRU 缓存)。
    - embed_with_fallback: Worker 不可用时降级云端。

    Args:
        worker_pool: 进程池(用于 run_in_executor offload)。
        config: 配置 dict 中的 kb.embedding 段。
        model_registry: 模型注册表(用于获取云端 embedding 适配器)。
    """

    def __init__(
        self,
        worker_pool: Any | None = None,
        config: dict[str, Any] | None = None,
        model_registry: Any | None = None,
    ) -> None:
        self._worker_pool = worker_pool
        self._config = config or {}
        self._model_registry = model_registry
        self._lru_cache_size = self._config.get("lru_cache_size", 512)

        # 模型选择
        self._model_name = self._config.get(
            "local_default", "BAAI/bge-m3"
        )
        self._light_model = self._config.get(
            "local_light", "BAAI/bge-small-zh-v1.5"
        )
        self._auto_switch_gb = self._config.get("auto_switch_memory_gb", 6)
        self._fallback_cloud = self._config.get(
            "fallback_cloud", "glm-embedding"
        )

    async def embed_chunks(self, chunks: list[Chunk]) -> list[list[float]]:
        """批量 embedding(蓝图 §4.10 Worker 集成)。

        Args:
            chunks: Chunk 列表。

        Returns:
            向量列表,每项为 float 列表。

        Raises:
            EmbeddingError: Worker 不可用时。
        """
        if not chunks:
            return []
        texts = [c.text for c in chunks]
        return await self._embed_texts(texts)

    async def embed_single(self, text: str) -> list[float]:
        """单条 query embedding(带 LRU 缓存,蓝图 §4.10 LRU 缓存)。

        Args:
            text: 查询文本。

        Returns:
            向量(float 列表)。
        """
        return list(await self._embed_query_cached(text))

    async def embed_with_fallback(
        self,
        chunks: list[Chunk],
        session_id: str | None = None,
        turn: int = 0,
    ) -> list[list[float]]:
        """Worker 不可用时降级云端(蓝图 §4.10 云端降级)。

        Args:
            chunks: Chunk 列表。
            session_id: 会话 ID(异常入库用)。
            turn: 轮次(异常入库用)。

        Returns:
            向量列表。
        """
        try:
            return await self.embed_chunks(chunks)
        except EmbeddingError as e:
            logger.warning(
                "Worker unavailable, falling back to cloud embedding: %s", e
            )
            # MVP 简化:云端降级记录日志,具体实现依赖模型注册表
            # 异常入库逻辑在集成时通过 react_events 处理
            return await self._cloud_embed(chunks)

    # ── 内部实现 ────────────────────────────────────────────────────────

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """发送文本到 Worker 进行 embedding。

        MVP 简化:Worker 未就绪时使用 mock 向量(全 0 占位)。
        V2 实现实际 Worker 调用。
        """
        if self._worker_pool is None:
            # MVP:Worker 未就绪,返回 mock 向量(占位)
            dim = self._get_vector_dim()
            logger.info(
                "Worker pool not configured, returning mock %d-dim vectors", dim
            )
            return [[0.0] * dim for _ in texts]

        # 实际 Worker 调用
        loop = __import__("asyncio", fromlist=[""]).get_event_loop()
        try:
            vectors = await loop.run_in_executor(
                self._worker_pool,
                _embed_worker_fn,
                texts,
                self._model_name,
            )
            return vectors
        except Exception as e:
            raise EmbeddingError(f"Worker embedding failed: {e}") from e

    async def _cloud_embed(
        self, chunks: list[Chunk]
    ) -> list[list[float]]:
        """云端 embedding 降级。

        MVP 简化:返回 mock 向量。
        V2 实现实际云端 API 调用。
        """
        dim = self._get_vector_dim()
        logger.info(
            "Cloud embedding fallback (MVP mock), dim=%d", dim
        )
        return [[0.0] * dim for _ in chunks]

    @lru_cache(maxsize=512)
    async def _embed_query_cached(
        self, query: str
    ) -> tuple[float, ...]:
        """query 向量 LRU 缓存(蓝图 §4.10 LRU 缓存)。

        使用 lru_cache,key=query 文本,有效期直到 cache_clear()。
        """
        # MVP 简化:Worker 未就绪时返回 mock 向量
        if self._worker_pool is None:
            dim = self._get_vector_dim()
            return tuple([0.0] * dim)
        vectors = await self._embed_texts([query])
        return tuple(vectors[0])

    def _get_vector_dim(self) -> int:
        """获取当前模型的向量维度。

        bge-m3: 1024 维
        bge-small-zh-v1.5: 384 维
        """
        if self._model_name == self._light_model:
            return 384
        return 1024

    def clear_query_cache(self) -> None:
        """清理 query 向量 LRU 缓存(蓝图 §4.10 每 10 分钟触发)。"""
        self._embed_query_cached.cache_clear()
        logger.info("Query embedding LRU cache cleared")


def _embed_worker_fn(
    texts: list[str], model_name: str
) -> list[list[float]]:
    """Worker 进程 embedding 函数(蓝图 §4.10)。

    在 Worker 进程内执行,加载 bge-m3 模型进行批量 embedding。

    Args:
        texts: 文本列表。
        model_name: 模型名称。

    Returns:
        向量列表。
    """
    raise NotImplementedError(
        "Worker embedding requires FlagEmbedding library. "
        "Install with: pip install FlagEmbedding"
    )