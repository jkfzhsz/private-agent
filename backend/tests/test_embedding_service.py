"""B6 P0-5/P0-6 AC-3, AC-7 - embedding_service + bge-small 测试。

Source: plan/b6-rag-fullstack phase 2 (AC-3, AC-7)
"""
import sys
from unittest.mock import MagicMock, patch

from private_agent.knowledge.embedding_service import EmbeddingService


# ── 2026-08-12 方案1: extra_python_path 注入测试 (dev-tdd RED) ──────────────


def test_apply_extra_python_path_inserts_valid_dir(monkeypatch, tmp_path):
    """配置的目录存在时, apply_extra_python_path 应将其插入 sys.path[0]。

    场景: 用户在设置页填了本地 torch/FlagEmbedding 所在 site-packages,
    后端启动时调用此函数让后续 import 能命中。
    """
    extra_dir = tmp_path / "my-ml-deps"
    extra_dir.mkdir()
    original_path = list(sys.path)
    try:
        from private_agent.knowledge.embedding_service import apply_extra_python_path

        apply_extra_python_path(str(extra_dir))
        assert str(extra_dir) in sys.path
        # 插入位置应在最前(优先级高于打包 venv 的 site-packages)
        assert sys.path[0] == str(extra_dir)
    finally:
        sys.path[:] = original_path


def test_apply_extra_python_path_ignores_missing_dir(monkeypatch):
    """配置的目录不存在时, 不修改 sys.path, 仅记录 warning。

    场景: 用户填错路径或删除了目录, 启动不应崩溃, 走 mock 降级。
    """
    original_path = list(sys.path)
    try:
        from private_agent.knowledge.embedding_service import apply_extra_python_path

        apply_extra_python_path("/nonexistent/path/xyz123")
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path


def test_apply_extra_python_path_ignores_empty_string(monkeypatch):
    """空字符串(未配置)时不应修改 sys.path。"""
    original_path = list(sys.path)
    try:
        from private_agent.knowledge.embedding_service import apply_extra_python_path

        apply_extra_python_path("")
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path


def test_apply_extra_python_path_idempotent(monkeypatch, tmp_path):
    """重复调用不应重复插入(避免 sys.path 累积污染)。"""
    extra_dir = tmp_path / "ml-deps"
    extra_dir.mkdir()
    original_path = list(sys.path)
    try:
        from private_agent.knowledge.embedding_service import apply_extra_python_path

        apply_extra_python_path(str(extra_dir))
        apply_extra_python_path(str(extra_dir))
        apply_extra_python_path(str(extra_dir))
        count = sys.path.count(str(extra_dir))
        assert count == 1, f"expected 1 occurrence, got {count}"
    finally:
        sys.path[:] = original_path


# ── 2026-08-12 方案1: main.py 启动注入集成测试 ──────────────────────────


def test_inject_rag_paths_applies_extra_python_path(monkeypatch, tmp_path):
    """main._inject_rag_paths 从合并配置读取 extra_python_path 并注入。

    场景: 启动时已从 config_runtime 合并了 extra_python_path,
    _inject_rag_paths 应调用 apply_extra_python_path 注入 sys.path。
    """
    extra_dir = tmp_path / "site-packages"
    extra_dir.mkdir()
    original_path = list(sys.path)
    try:
        from private_agent.main import _inject_rag_paths

        cfg = {
            "knowledge": {
                "embedding": {
                    "extra_python_path": str(extra_dir),
                    "model_path": "",
                }
            }
        }
        _inject_rag_paths(cfg)
        assert str(extra_dir) in sys.path
    finally:
        sys.path[:] = original_path


def test_inject_rag_paths_skips_when_not_configured(monkeypatch):
    """未配置 extra_python_path 时 _inject_rag_paths 不修改 sys.path。"""
    original_path = list(sys.path)
    try:
        from private_agent.main import _inject_rag_paths

        _inject_rag_paths({})
        _inject_rag_paths({"knowledge": {}})
        _inject_rag_paths({"knowledge": {"embedding": {}}})
        assert sys.path == original_path
    finally:
        sys.path[:] = original_path


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