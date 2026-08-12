"""蓝图 §4.10 Embedding 服务 - Worker 集成 + 云端降级 + LRU 缓存。

职责:
1. 将文本批量发送到 Worker 进程做 bge embedding(按模型名分派模型类)。
2. Worker 不可用时降级到 mock 全 0 向量(检索降级 keyword-only,不阻断)。
3. query 向量 LRU 缓存(高频相同 query 复用)。
4. 0.5.1 B2: bge-small-zh-v1.5(512 维)输出 padding 至 STORAGE_DIM(1024) 与 DB vector(1024) 对齐。

0.5.1 改造(2026-08-09):
- 模型类分派: 模型名含 "m3" → BGEM3FlagModel; 其他(bge-small 系) → FlagModel。
- C1 维度校验: 模型输出维度 != 期望 model_dim 时抛 EmbeddingError(拒绝非法向量入库)。
- worker 故障容错: 捕获异常返回全 0 mock + ERROR 日志(V7 降级链,不抛异常阻断入库)。
- _resolve_model_path 四级回退: 显式 model_path → HF_HUB_CACHE → 项目根自动探测 → HF 默认缓存。
- select_model_by_memory() 废弃(C3 固定单一模型, 切换模型=必须全量重灌)。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from private_agent.knowledge.models import Chunk

logger = logging.getLogger(__name__)

# 0.5.1: bge-small-zh-v1.5 输出维(以 ModelScope 官方 BAAI/bge-small-zh-v1.5 为准,
# hidden_size=512, 2026-08-09 蒋先生确认)。DB 列 vector(1024) 中前 512 维为真实值。
MODEL_DIM = 512
# DB 存储维(kb_chunks.embedding = vector(1024) NOT NULL, 见 schema.sql)
STORAGE_DIM = 1024
# 模型名含此 token → BGEM3FlagModel(bge-m3 架构); 否则 FlagModel(BERT 架构)
BGE_M3_TOKEN = "m3"


class EmbeddingError(Exception):
    """Embedding 操作失败。"""


class EmbeddingService:
    """Embedding 服务(蓝图 §4.10)。

    MVP 实现:
    - embed_chunks: 批量 embedding(Worker 进程)。
    - embed_single: 单条 query embedding(带 LRU 缓存)。
    - embed_with_fallback: Worker 不可用时降级 mock。

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

        # 0.5.1 C3: 固定单一模型(默认 bge-small-zh-v1.5, 与 DB 512 padding 对齐);
        # 不再运行时按内存自动切换(切换模型 = 全量重灌, 由启动自检强制)。
        self._model_name = self._config.get(
            "local_default", "BAAI/bge-small-zh-v1.5"
        )
        self._light_model = self._config.get(
            "local_light", "BAAI/bge-small-zh-v1.5"
        )
        # 存储维(DB 列维度), 默认 1024
        self._storage_dim = int(self._config.get("storage_dim", STORAGE_DIM))
        # 模型路径(空则走 _resolve_model_path 自动探测)
        self._model_path = self._config.get("model_path", "")

    async def embed_chunks(self, chunks: list[Chunk]) -> list[list[float]]:
        """批量 embedding(蓝图 §4.10 Worker 集成)。

        Args:
            chunks: Chunk 列表。

        Returns:
            向量列表,每项为 float 列表(存储维 STORAGE_DIM)。

        Raises:
            EmbeddingError: 模型输出维度非法时。
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
            向量(float 列表,存储维 STORAGE_DIM)。
        """
        return list(await self._embed_query_cached(text))

    async def embed_with_fallback(
        self,
        chunks: list[Chunk],
        session_id: str | None = None,
        turn: int = 0,
    ) -> list[list[float]]:
        """Worker 不可用时降级(蓝图 §4.10 云端降级)。

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
            return await self._cloud_embed(chunks)

    # ── 内部实现 ────────────────────────────────────────────────────────

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """发送文本到 Worker 进行 embedding。

        - Worker 未配置(mock/测试): 返回全 0 mock(存储维)。
        - Worker 故障(异常): 降级全 0 mock + ERROR 日志(不阻断入库,
          检索走 vector_search 零向量检测 → keyword-only 降级)。
        - 模型输出维度非法: 抛 EmbeddingError(拒绝非法向量入库)。
        """
        if self._worker_pool is None:
            # Mock/测试分支:Worker 未就绪,返回 mock 向量(全 0,存储维)
            logger.info(
                "Worker pool not configured, returning mock %d-dim vectors",
                self._storage_dim,
            )
            return [[0.0] * self._storage_dim for _ in texts]

        loop = asyncio.get_event_loop()
        try:
            t0 = time.monotonic()
            vectors = await loop.run_in_executor(
                self._worker_pool,
                _embed_worker_fn,
                texts,
                self._model_name,
                self._model_path,
            )
            validated = self._validate_and_pad(vectors)
            elapsed = time.monotonic() - t0
            logger.info(
                "embed %d text(s) model=%s dim=%d->%d elapsed=%.2fs",
                len(texts), self._model_name,
                self.get_model_dim(), self._storage_dim, elapsed,
            )
            return validated
        except EmbeddingError:
            raise
        except Exception as e:  # noqa: BLE001 - worker 故障统一降级
            logger.error(
                "Worker embedding failed (%s), degrading to mock %d-dim "
                "vectors (keyword-only retrieval)", e, self._storage_dim,
            )
            return [[0.0] * self._storage_dim for _ in texts]

    def _validate_and_pad(self, vectors: list[list[float]]) -> list[list[float]]:
        """C1 维度校验 + B2 padding。

        校验: 模型输出每项长度 == get_model_dim(); 否则抛 EmbeddingError
        (DB 无法校验真实维度, 必须业务层拦截, 拒绝非法向量入库)。
        padding: model_dim < storage_dim 时尾部补零到存储维。
        """
        expected = self.get_model_dim()
        result: list[list[float]] = []
        for v in vectors:
            v = [float(x) for x in v]
            if len(v) != expected:
                raise EmbeddingError(
                    f"model output dim {len(v)} != expected {expected}"
                )
            if self._storage_dim > expected:
                v = v + [0.0] * (self._storage_dim - expected)
            result.append(v)
        return result

    async def _cloud_embed(
        self, chunks: list[Chunk]
    ) -> list[list[float]]:
        """云端 embedding 降级。

        MVP 简化:返回 mock 向量。
        V2 实现实际云端 API 调用(技术债务 D3)。
        """
        logger.info(
            "Cloud embedding fallback (MVP mock), dim=%d", self._storage_dim
        )
        return [[0.0] * self._storage_dim for _ in chunks]

    @lru_cache(maxsize=512)
    async def _embed_query_cached(
        self, query: str
    ) -> tuple[float, ...]:
        """query 向量 LRU 缓存(蓝图 §4.10 LRU 缓存)。

        使用 lru_cache,key=query 文本,有效期直到 cache_clear()。
        """
        if self._worker_pool is None:
            return tuple([0.0] * self._storage_dim)
        vectors = await self._embed_texts([query])
        return tuple(vectors[0])

    def get_model_dim(self) -> int:
        """模型真实输出维(m3 → 1024; bge-small 系 → 512)。"""
        return 1024 if BGE_M3_TOKEN in self._model_name else MODEL_DIM

    def _get_vector_dim(self) -> int:
        """存储维(DB 列维度, 含 padding)。快照/日志展示用。"""
        return self._storage_dim

    def clear_query_cache(self) -> None:
        """清理 query 向量 LRU 缓存(蓝图 §4.10 每 10 分钟触发)。"""
        self._embed_query_cached.cache_clear()
        logger.info("Query embedding LRU cache cleared")

    @staticmethod
    def select_model_by_memory() -> str:
        """[DEPRECATED] 0.5.1 C3: 固定单一模型, 不再运行时自动切换。

        保留方法仅兼容既有测试; __init__ 不再调用。切换模型 = 全量重灌,
        由启动自检(verify_embedding_consistency)程序强制, 不靠人为纪律。
        """
        try:
            import psutil

            avail_gb = psutil.virtual_memory().available / (1024**3)
            if avail_gb < 6.0:
                logger.warning(
                    "Available memory %.1fGB < 6GB, using light model", avail_gb
                )
                return "BAAI/bge-small-zh-v1.5"
        except ImportError:
            pass
        return "BAAI/bge-m3"


# ══════════════════════════════════════════════════════════════════════════
# Worker 进程池与模型加载
# ══════════════════════════════════════════════════════════════════════════

# 专用单 worker 进程池(0.5.1: 强制 max_workers=1 防内存叠加 OOM;
# 不复用 core/executor.get_pool() 的 2-worker 全局池)
_embedding_pool: concurrent.futures.ProcessPoolExecutor | None = None


def get_embedding_pool() -> concurrent.futures.ProcessPoolExecutor:
    """获取 embedding 专用进程池(懒创建, 强制 1 worker)。

    Windows spawn 模式: worker 进程内惰性加载模型(首次调用才 import),
    单 worker 常驻复用, 内存上限 ≈ 主进程 + 1 × 模型内存。
    """
    global _embedding_pool
    if _embedding_pool is None:
        _embedding_pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=1
        )
    return _embedding_pool


def apply_extra_python_path(extra_path: str) -> None:
    """将用户配置的本地 ML 依赖目录注入 sys.path(方案1, 2026-08-12)。

    打包版 venv 不含 torch/FlagEmbedding 等重型 ML 依赖(见 build-electron.bat
    2026-08-12 排除规则)。用户可在设置页填入本地已装好这些依赖的
    site-packages 目录(如开发机 venv 的 site-packages, 或 conda env 的
    site-packages), 后端启动时调用本函数注入, 后续 _embed_worker_fn /
    _rerank_worker_fn 的 `from FlagEmbedding import ...` 即可命中。

    安全约束:
    - 空字符串/None: 直接返回(未配置, 走 mock 降级)
    - 目录不存在: 记录 warning 并返回(不崩溃, 走 mock 降级)
    - 目录存在: insert 到 sys.path[0](优先级高于打包 venv 的 site-packages)
    - 已存在: 不重复插入(避免 sys.path 累积污染)

    注: ProcessPoolExecutor spawn 模式会继承主进程 sys.path, 故主进程启动
    时注入即可, worker 不需要再注入。
    """
    if not extra_path:
        return
    p = Path(extra_path)
    if not p.is_dir():
        logger.warning(
            "extra_python_path configured but not a directory: %s, "
            "RAG will degrade to mock embeddings",
            extra_path,
        )
        return
    sp = str(p)
    if sp in sys.path:
        return
    sys.path.insert(0, sp)
    logger.info("extra_python_path injected to sys.path[0]: %s", sp)


def _find_cache_root(base: str | None, repo_dir: str) -> str | None:
    """在候选根下定位含 {repo_dir}/snapshots 的 HF 缓存根。

    兼容两种结构:
    - 标准: {base}/models--{org}--{name}/snapshots/main
    - 历史双层嵌套(本机实测): {base}/models--{org}--{name}/models--{org}--{name}/snapshots/main

    Returns:
        缓存根(设置 HF_HUB_CACHE 用的目录), 未命中返回 None。
    """
    if not base:
        return None
    base = Path(base)
    if (base / repo_dir / "snapshots").is_dir():
        return str(base)
    if (base / repo_dir / repo_dir / "snapshots").is_dir():
        return str(base / repo_dir)
    return None


def _resolve_model_path(model_name: str, model_path: str = "") -> tuple[str, str | None]:
    """解析模型加载位置(0.5.1: 代码层兜底, 不单纯依赖环境变量)。

    四级回退:
    1. config 显式 model_path(本地目录, 直接加载);
    2. HF_HUB_CACHE 环境变量(缓存目录含 models--{org}--{name});
    3. 项目根自动探测(cwd.parent / cwd / home 下的 models--{org}--{name});
    4. HF 默认缓存(可能触发在线下载)。

    Returns:
        (path_or_repo_id, cache_dir | None)。cache_dir 非空时 worker 内设置
        HF_HUB_CACHE 后按 repo id 离线加载。
    """
    repo_dir = "models--" + model_name.replace("/", "--")

    if model_path:
        # 两种语义: ① 缓存根(含 models--{repo}/snapshots) → 返回 (repo, cache);
        # ② 直接模型目录(snapshots/main 或 HF 仓库根) → 直接路径加载。
        cache = _find_cache_root(model_path, repo_dir)
        if cache is not None:
            return model_name, cache
        p = Path(model_path)
        if p.is_dir():
            return str(p), None
        logger.warning("configured model_path not found: %s, falling back", model_path)

    cache = _find_cache_root(os.environ.get("HF_HUB_CACHE", ""), repo_dir)
    if cache is not None:
        return model_name, cache

    for base in (Path.cwd().parent, Path.cwd(), Path.home()):
        cache = _find_cache_root(str(base), repo_dir)
        if cache is not None:
            logger.info("auto-detected HF cache at %s", cache)
            return model_name, cache

    return model_name, None


def _check_memory_watermark(min_available_gb: float = 0.5) -> None:
    """worker 内模型加载前内存水位检查(0.5.1)。

    可用内存 < 阈值 → 拒绝加载模型(抛 RuntimeError, 上层降级 mock)。
    psutil 缺失时不阻断(降级到模型加载自身失败路径)。
    """
    try:
        import psutil

        avail_gb = psutil.virtual_memory().available / (1024**3)
        if avail_gb < min_available_gb:
            raise RuntimeError(
                f"available memory {avail_gb:.1f}GB < {min_available_gb}GB "
                "watermark, refusing to load embedding model"
            )
    except ImportError:
        pass


def _embed_worker_fn(
    texts: list[str], model_name: str, model_path: str = ""
) -> list[list[float]]:
    """Worker 进程 embedding 函数(蓝图 §4.10, 0.5.1 模型分派)。

    在 Worker 进程内执行:
    - 模型名含 "m3" → BGEM3FlagModel(bge-m3, 1024 维 dense);
    - 其他(bge-small 系) → FlagModel(512 维)。
    FlagEmbedding 不可用时返回 mock 全 0 向量(上游 _validate_and_pad
    会按 model_dim 校验, mock 维度必须与模型一致)。
    """
    try:
        resolved, cache_dir = _resolve_model_path(model_name, model_path)
        # 强制离线加载: 本地缓存命中即用, 不 HEAD 在线校验。
        # 本机 HTTPS 代理指向失效端口, 在线校验必 SSL 失败;
        # 且模型权重已在本地(D 盘), 无网络依赖。
        # 必须在 import huggingface_hub/transformers 之前设置(常量在 import 时读取)。
        if cache_dir:
            os.environ["HF_HUB_CACHE"] = cache_dir
        os.environ["HF_HUB_OFFLINE"] = "1"
        from FlagEmbedding import BGEM3FlagModel, FlagModel

        _check_memory_watermark()
        if cache_dir:
            os.environ["HF_HUB_CACHE"] = cache_dir

        if BGE_M3_TOKEN in model_name:
            model = BGEM3FlagModel(resolved, use_fp16=True)
            embeddings = model.encode(
                texts, batch_size=32, max_length=8192
            )["dense_vecs"]
        else:
            model = FlagModel(resolved, use_fp16=True)
            embeddings = model.encode(texts, batch_size=32)
        return embeddings.tolist()
    except ImportError:
        logger.warning("FlagEmbedding not available, using mock embeddings")
        dim = 1024 if BGE_M3_TOKEN in model_name else MODEL_DIM
        return [[0.0] * dim for _ in texts]
    except Exception as e:  # noqa: BLE001 - 传播给上层统一降级
        logger.error("Embedding worker error: %s", e)
        raise
