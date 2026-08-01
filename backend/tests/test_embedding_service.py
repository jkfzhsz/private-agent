"""B6 P0-5/P0-6 AC-3, AC-7 - embedding_service + bge-small 测试。

Source: plan/b6-rag-fullstack phase 2 (AC-3, AC-7)
"""
import sys
from unittest.mock import MagicMock, patch

from private_agent.knowledge.embedding_service import EmbeddingService


def test_select_model_by_memory_returns_light_when_under_6gb():
    """AC-7: 内存 <6GB 返回 light 模型。"""
    mock_psutil = MagicMock()
    mock_psutil.virtual_memory.return_value.available = 4 * 1024**3
    with patch.dict(sys.modules, {"psutil": mock_psutil}):
        result = EmbeddingService.select_model_by_memory()
        assert "bge-small" in result


def test_select_model_by_memory_returns_default_when_over_6gb():
    """AC-7: 内存 >=6GB 返回 default 模型。"""
    mock_psutil = MagicMock()
    mock_psutil.virtual_memory.return_value.available = 8 * 1024**3
    with patch.dict(sys.modules, {"psutil": mock_psutil}):
        result = EmbeddingService.select_model_by_memory()
        assert "bge-m3" in result


def test_select_model_by_memory_returns_default_when_psutil_missing():
    """psutil 不可用时返回 default 模型。"""
    with patch.dict(sys.modules, {"psutil": None}):
        result = EmbeddingService.select_model_by_memory()
        assert "bge-m3" in result


def test_embed_worker_fn_returns_correct_dim():
    """AC-3: _embed_worker_fn 返回 1024 维向量。"""
    mock_model = MagicMock()
    mock_model.encode.return_value = {"dense_vecs": MagicMock()}
    mock_model.encode.return_value["dense_vecs"].tolist.return_value = [
        [0.1] * 1024, [0.2] * 1024
    ]
    mock_bge = MagicMock()
    mock_bge.BGEM3FlagModel.return_value = mock_model
    with patch.dict(sys.modules, {"FlagEmbedding": mock_bge}):
        from private_agent.knowledge.embedding_service import _embed_worker_fn
        result = _embed_worker_fn(["hello", "world"], "BAAI/bge-m3")
        assert len(result) == 2
        assert len(result[0]) == 1024


def test_embed_worker_fn_returns_mock_when_unavailable():
    """FlagEmbedding 不可用时返回 mock 向量。"""
    with patch.dict(sys.modules, {"FlagEmbedding": None}):
        from private_agent.knowledge.embedding_service import _embed_worker_fn
        result = _embed_worker_fn(["hello"], "BAAI/bge-m3")
        assert len(result) == 1
        assert len(result[0]) == 1024
        assert result[0][0] == 0.0