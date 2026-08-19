"""0.5.1 embedding 装配测试 —— 全程 mock, 不加载真实 bge 模型。

覆盖:
- factory.build_embedding_service: PA_EMBEDDING_MOCK 隔离 / 正常注入单 worker 池
- C1 维度校验: _validate_and_pad 拒绝非法维度, 512 → 1024 padding, m3 不 pad
- _resolve_model_path 四级回退(config 显式 → HF_HUB_CACHE → 项目根 → 默认)
- worker 故障降级: _embed_texts 异常返回全 0 mock(不抛, 检索 keyword-only)
- verify_embedding_consistency 启动自检: 空库/匹配/不匹配/混存
- kb_repo._embedding_bytes_to_text: float32 bytes → pgvector 文本
"""
from __future__ import annotations

import asyncio
import struct
from unittest.mock import MagicMock

import pytest

from private_agent.knowledge import factory
from private_agent.knowledge.embedding_service import (
    EmbeddingError,
    EmbeddingService,
    MODEL_DIM,
    STORAGE_DIM,
    _resolve_model_path,
)
from private_agent.knowledge.kb_repo import _embedding_bytes_to_text

# ── factory 装配 ──────────────────────────────────────────────────────────


def test_build_embedding_service_mock_mode(monkeypatch):
    """PA_EMBEDDING_MOCK=1 → worker_pool=None(mock 全 0 分支, 不加载模型)。"""
    monkeypatch.setenv("PA_EMBEDDING_MOCK", "1")
    svc = factory.build_embedding_service(cfg={})
    assert svc._worker_pool is None


def test_build_embedding_service_injects_pool(monkeypatch):
    """非 mock 模式注入单 worker 池 + kb.embedding 配置。"""
    monkeypatch.delenv("PA_EMBEDDING_MOCK", raising=False)
    fake_pool = object()
    monkeypatch.setattr(
        "private_agent.knowledge.factory.get_embedding_pool",
        lambda: fake_pool,
    )
    cfg = {"kb": {"embedding": {"local_default": "BAAI/bge-small-zh-v1.5"}}}
    svc = factory.build_embedding_service(cfg=cfg)
    assert svc._worker_pool is fake_pool
    assert svc._model_name == "BAAI/bge-small-zh-v1.5"


def test_build_kb_service_failfast(monkeypatch):
    """fail-fast 置位后 build_kb_service 抛 KBUnavailableError。"""
    factory.set_kb_failfast("inconsistent vectors")
    try:
        with pytest.raises(factory.KBUnavailableError):
            factory.build_kb_service(conn=MagicMock(), cfg={})
    finally:
        factory.clear_kb_failfast()


# ── C1 维度校验 + B2 padding ──────────────────────────────────────────────


def _svc_small() -> EmbeddingService:
    return EmbeddingService(
        worker_pool=None,
        config={"local_default": "BAAI/bge-small-zh-v1.5"},
    )


def test_validate_and_pad_512_to_1024():
    """bge-small: 512 维输入 → padding 至 1024, 前段保持、后段全 0。"""
    svc = _svc_small()
    out = svc._validate_and_pad([[0.1] * MODEL_DIM])
    assert len(out) == 1
    assert len(out[0]) == STORAGE_DIM
    assert all(abs(v - 0.1) < 1e-9 for v in out[0][:MODEL_DIM])
    assert all(abs(v) < 1e-9 for v in out[0][MODEL_DIM:])


def test_validate_and_pad_rejects_wrong_dim():
    """C1: 模型输出维度 != 512 → EmbeddingError(拒绝非法向量入库)。"""
    svc = _svc_small()
    with pytest.raises(EmbeddingError):
        svc._validate_and_pad([[0.1] * 383])
    with pytest.raises(EmbeddingError):
        svc._validate_and_pad([[0.1] * 1024])


def test_validate_and_pad_m3_no_padding():
    """bge-m3(1024 维)不 padding, 直接通过。"""
    svc = EmbeddingService(
        worker_pool=None, config={"local_default": "BAAI/bge-m3"}
    )
    out = svc._validate_and_pad([[0.1] * 1024])
    assert len(out[0]) == 1024
    with pytest.raises(EmbeddingError):
        svc._validate_and_pad([[0.1] * 512])


# ── 模型路径解析四级回退 ───────────────────────────────────────────────────


def test_resolve_model_path_explicit(monkeypatch, tmp_path):
    """1) config 显式 model_path 存在 → 直接返回该目录。"""
    assert _resolve_model_path(
        "BAAI/bge-small-zh-v1.5", str(tmp_path)
    ) == (str(tmp_path), None)


def test_resolve_model_path_env_cache(monkeypatch, tmp_path):
    """2) HF_HUB_CACHE 环境变量含 models--{repo}/snapshots → 返回 (repo, cache)。"""
    cache = tmp_path / "hf-cache"
    (cache / "models--BAAI--bge-small-zh-v1.5" / "snapshots" / "main").mkdir(
        parents=True
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    repo, cdir = _resolve_model_path("BAAI/bge-small-zh-v1.5", "")
    assert repo == "BAAI/bge-small-zh-v1.5"
    assert cdir == str(cache)


def test_resolve_model_path_double_nested_cache(monkeypatch, tmp_path):
    """双层嵌套历史结构: base/models--{repo}/models--{repo}/snapshots → 内层为缓存根。"""
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    outer = tmp_path / "models--BAAI--bge-small-zh-v1.5"
    (outer / "models--BAAI--bge-small-zh-v1.5" / "snapshots" / "main").mkdir(
        parents=True
    )
    monkeypatch.chdir(tmp_path)
    repo, cdir = _resolve_model_path("BAAI/bge-small-zh-v1.5", "")
    assert cdir == str(outer)


def test_resolve_model_path_probe_project_root(monkeypatch, tmp_path):
    """3) 项目根自动探测(cwd 下 models--{repo}/snapshots 目录)。"""
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    (tmp_path / "models--BAAI--bge-small-zh-v1.5" / "snapshots").mkdir(
        parents=True
    )
    monkeypatch.chdir(tmp_path)
    repo, cdir = _resolve_model_path("BAAI/bge-small-zh-v1.5", "")
    assert cdir == str(tmp_path)


def test_resolve_model_path_default(monkeypatch, tmp_path):
    """4) 全部失败 → 返回 repo id + None(走 HF 默认缓存, 可能在线)。"""
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.chdir(tmp_path)  # 空目录
    assert _resolve_model_path(
        "BAAI/bge-small-zh-v1.5", ""
    ) == ("BAAI/bge-small-zh-v1.5", None)


# ── worker 故障降级 ───────────────────────────────────────────────────────


def test_embed_texts_worker_error_degrades_to_mock(monkeypatch):
    """worker 异常 → 全 0 mock(不抛, 检索 keyword-only 降级链)。"""
    loop = MagicMock()

    async def fake_run(fn, *args, **kwargs):
        raise RuntimeError("model load failed")

    loop.run_in_executor = fake_run
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    svc = EmbeddingService(
        worker_pool=object(), config={"local_default": "BAAI/bge-small-zh-v1.5"}
    )
    result = asyncio.run(svc._embed_texts(["hello"]))
    assert len(result) == 1
    assert len(result[0]) == STORAGE_DIM
    assert all(v == 0.0 for v in result[0])


# ── 启动自检 ──────────────────────────────────────────────────────────────


def _vec_text(values: list[float]) -> str:
    return "[" + ",".join(f"{v}" for v in values) + "]"


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, sql, *args, **kwargs):
        return [{"embedding": r} for r in self._rows]


async def _verify(rows, model_name="BAAI/bge-small-zh-v1.5"):
    conn = _FakeConn(rows)
    cfg = {"kb": {"embedding": {"local_default": model_name}}}
    return await factory.verify_embedding_consistency(conn, cfg)


def test_consistency_empty_db():
    """空库 → 通过。"""
    asyncio.run(_verify([]))


def test_consistency_padding_small_ok():
    """padding-512 向量 + small 模型 → 通过。"""
    vec = [0.1] * MODEL_DIM + [0.0] * (STORAGE_DIM - MODEL_DIM)
    asyncio.run(_verify([_vec_text(vec)]))


def test_consistency_real1024_with_small_model_raises():
    """real-1024 向量 + small 模型 → 抛(模型切换未重灌)。"""
    vec = [0.1] * STORAGE_DIM
    with pytest.raises(factory.KBEmbeddingInconsistencyError):
        asyncio.run(_verify([_vec_text(vec)]))


def test_consistency_real1024_with_m3_ok():
    """real-1024 向量 + m3 模型 → 通过。"""
    vec = [0.1] * STORAGE_DIM
    asyncio.run(_verify([_vec_text(vec)], model_name="BAAI/bge-m3"))


def test_consistency_mixed_kinds_raises():
    """padding-512 与 real-1024 混存 → 抛(混存脏库)。"""
    pad = [0.1] * MODEL_DIM + [0.0] * (STORAGE_DIM - MODEL_DIM)
    real = [0.1] * STORAGE_DIM
    with pytest.raises(factory.KBEmbeddingInconsistencyError):
        asyncio.run(_verify([_vec_text(pad), _vec_text(real)]))


# ── kb_repo 向量文本转换 ──────────────────────────────────────────────────


def test_embedding_bytes_to_text():
    """float32 打包 bytes → pgvector 文本(长度与数值正确)。"""
    vec = [0.5, -0.25, 1.0]
    data = struct.pack("3f", *vec)
    text = _embedding_bytes_to_text(data)
    assert text.startswith("[")
    assert text.endswith("]")
    parts = [float(x) for x in text[1:-1].split(",")]
    assert len(parts) == 3
    assert abs(parts[0] - 0.5) < 1e-5
    assert abs(parts[1] - (-0.25)) < 1e-5
    assert abs(parts[2] - 1.0) < 1e-5
