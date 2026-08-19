"""KB 服务装配工厂(0.5.1) —— 统一依赖注入, 消除 7 处重复装配 + 启动自检。

职责:
1. build_embedding_service: 注入专用单 worker 池 + kb.embedding 配置;
   `PA_EMBEDDING_MOCK=1` 时强制 mock(worker_pool=None), 测试隔离用。
2. build_kb_service: 注入 embedding_service(processor 可选)。
3. verify_embedding_consistency: 启动自检, 校验存量向量与当前模型维度匹配
   (防混存脏库, 程序强制, 不靠人为纪律)。
4. KB 模块 fail-fast 状态: 自检失败后所有 KB 使用路径返回明确错误,
   应用其余功能正常; `PA_KB_STRICT=1` 时由调用方(启动钩子)阻断进程。
"""
from __future__ import annotations

import logging
import os
import struct
from typing import Any

from private_agent.config import loader
from private_agent.knowledge.embedding_service import (
    MODEL_DIM,
    EmbeddingService,
    get_embedding_pool,
)
from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.knowledge.kb_service import KnowledgeBaseService

logger = logging.getLogger(__name__)

# KB 模块 fail-fast 原因(空串 = 可用); 自检失败后置位
_KB_FASTFAIL_REASON: str = ""


class KBUnavailableError(RuntimeError):
    """KB 模块不可用(启动自检失败等阻断原因)。"""


class KBEmbeddingInconsistencyError(RuntimeError):
    """向量库与当前模型不匹配(混存脏库 / 模型切换未重灌)。"""


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════


def build_embedding_service(
    cfg: dict[str, Any] | None = None,
) -> EmbeddingService:
    """构建 EmbeddingService(0.5.1 统一装配)。

    - 正常: 注入专用单 worker 进程池 + kb.embedding 配置;
    - `PA_EMBEDDING_MOCK=1`: 强制 worker_pool=None(mock 全 0 分支),
      pytest/conftest 统一设置, 杜绝测试误加载真实模型。
    """
    cfg = cfg or loader.load_config()
    kb_cfg = (cfg.get("kb") or {}).get("embedding", {}) or {}
    if os.environ.get("PA_EMBEDDING_MOCK") == "1":
        logger.info("PA_EMBEDDING_MOCK=1: embedding in mock mode")
        return EmbeddingService(worker_pool=None, config=kb_cfg)
    return EmbeddingService(worker_pool=get_embedding_pool(), config=kb_cfg)


def build_kb_service(
    conn: Any,
    cfg: dict[str, Any] | None = None,
    processor: Any | None = None,
) -> KnowledgeBaseService:
    """构建 KnowledgeBaseService(0.5.1 统一装配)。

    启动自检失败(fail-fast 置位)时抛 KBUnavailableError, 所有 KB 使用路径
    (admin 上传/检索、search_knowledge 工具、auto_retrieve)得到明确错误。
    """
    reason = kb_failfast_reason()
    if reason:
        raise KBUnavailableError(reason)
    cfg = cfg or loader.load_config()
    repo = KnowledgeBaseRepo(conn)
    embedding_service = build_embedding_service(cfg)
    return KnowledgeBaseService(
        kb_repo=repo,
        processor=processor,
        embedding_service=embedding_service,
        config=cfg.get("kb") or {},
    )


# ══════════════════════════════════════════════════════════════════════════
# KB 模块 fail-fast 状态
# ══════════════════════════════════════════════════════════════════════════


def set_kb_failfast(reason: str) -> None:
    """置位 KB 模块 fail-fast(启动自检失败时调用)。"""
    global _KB_FASTFAIL_REASON
    _KB_FASTFAIL_REASON = reason
    logger.error("KB module fail-fast: %s", reason)


def clear_kb_failfast() -> None:
    """清除 fail-fast 状态(重灌后重启应用自检通过自动清除)。"""
    global _KB_FASTFAIL_REASON
    _KB_FASTFAIL_REASON = ""


def kb_failfast_reason() -> str:
    """当前 fail-fast 原因(空串 = KB 可用)。"""
    return _KB_FASTFAIL_REASON


# ══════════════════════════════════════════════════════════════════════════
# 启动自检
# ══════════════════════════════════════════════════════════════════════════


async def verify_embedding_consistency(
    conn: Any,
    cfg: dict[str, Any] | None = None,
) -> None:
    """启动自检: 校验存量向量与当前配置模型维度匹配(0.5.1)。

    判定规则(抽样 5 条活跃 chunk):
    - 空库 → 通过;
    - 向量后段(MODEL_DIM:1024)全 0 → padding-512 向量(小模型生成)
      → 当前模型必须也是 512 维(非 m3);
    - 后段存在非零 → 真实 1024 向量(m3 生成) → 当前模型必须 m3;
    - 两种并存 → 混存脏库。

    不匹配/混存 → 抛 KBEmbeddingInconsistencyError(调用方: 置 fail-fast
    或 PA_KB_STRICT=1 阻断进程)。切换模型必须全量重灌后才允许启动。
    """
    cfg = cfg or loader.load_config()
    kb_cfg = (cfg.get("kb") or {}).get("embedding", {}) or {}
    model_name = kb_cfg.get("local_default", "BAAI/bge-small-zh-v1.5")
    model_dim = 1024 if "m3" in model_name else MODEL_DIM

    rows = await conn.fetch(
        "SELECT embedding FROM kb_chunks WHERE is_active = TRUE LIMIT 5"
    )
    if not rows:
        logger.info("KB consistency: kb_chunks empty, skip check")
        return

    kinds: set[str] = set()
    for r in rows:
        vec = _parse_vector(r["embedding"])
        if vec is None:
            continue
        if len(vec) <= MODEL_DIM or all(
            abs(x) < 1e-9 for x in vec[MODEL_DIM:]
        ):
            kinds.add("padding-512")
        else:
            kinds.add("real-1024")

    if not kinds:
        return

    if len(kinds) > 1:
        raise KBEmbeddingInconsistencyError(
            "kb_chunks 混存向量来源 %s (padding-512 与 real-1024 并存): "
            "需切换模型并全量重灌后再启动" % sorted(kinds)
        )
    kind = kinds.pop()
    if kind == "padding-512" and model_dim != MODEL_DIM:
        raise KBEmbeddingInconsistencyError(
            f"kb_chunks 为 padding-512 向量, 但当前模型 {model_name} 输出 "
            f"{model_dim} 维: 切换模型必须全量重灌"
        )
    if kind == "real-1024" and model_dim != 1024:
        raise KBEmbeddingInconsistencyError(
            f"kb_chunks 为 real-1024 向量, 但当前模型 {model_name} 输出 "
            f"{model_dim} 维: 切换模型必须全量重灌"
        )
    logger.info(
        "KB embedding consistency OK: kind=%s model=%s model_dim=%d",
        kind, model_name, model_dim,
    )


def _parse_vector(v: Any) -> list[float] | None:
    """解析 DB 向量。

    asyncpg 未注册 pgvector 类型码时返回文本表示 "[a,b,...]";
    兼容 bytes(float32 打包) 与 None。
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("[") and s.endswith("]"):
            parts = s[1:-1].split(",")
            try:
                return [float(x) for x in parts if x != ""]
            except ValueError:
                return None
        return None
    if isinstance(v, bytes):
        n = len(v) // 4
        if n == 0:
            return None
        return list(struct.unpack(f"{n}f", v))
    return None
