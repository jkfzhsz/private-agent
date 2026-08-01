"""B6 P1-5 AC-8, AC-9 - reranker 测试。

Source: plan/b6-rag-fullstack phase 3 (AC-8, AC-9)
"""
import sys
from unittest.mock import MagicMock, patch


def test_rerank_worker_fn_returns_scores():
    """AC-8: _rerank_worker_fn 返回分数列表。"""
    mock_model = MagicMock()
    mock_model.compute_score.return_value = [0.9, 0.5, 0.1]
    mock_reranker = MagicMock()
    mock_reranker.FlagReranker.return_value = mock_model
    with patch.dict(sys.modules, {"FlagEmbedding": mock_reranker}):
        from private_agent.knowledge.reranker_service import _rerank_worker_fn
        result = _rerank_worker_fn("query", ["doc1", "doc2", "doc3"])
        assert len(result) == 3
        assert result[0][0] == 0
        assert result[0][1] == 0.9


def test_rerank_worker_fn_returns_mock_when_unavailable():
    """FlagEmbedding 不可用时返回降级分数。"""
    with patch.dict(sys.modules, {"FlagEmbedding": None}):
        from private_agent.knowledge.reranker_service import _rerank_worker_fn
        result = _rerank_worker_fn("query", ["doc1", "doc2"])
        assert len(result) == 2
        assert result[0][1] == 1.0
        assert result[1][1] == 1.0


def test_reranker_runs_without_worker_pool():
    """AC-9: reranker worker_pool=None 降级不阻断。"""
    import asyncio

    from private_agent.knowledge.reranker_service import RerankerService
    from private_agent.knowledge.models import Chunk

    async def _run():
        svc = RerankerService(worker_pool=None)
        chunks = [
            Chunk(text="text1", doc_id=1, score=0.0),
            Chunk(text="text2", doc_id=1, score=0.0),
        ]
        result = await svc.rerank("query", chunks, top_k=2)
        assert len(result) == 2
        assert result[0].score == 1.0

    asyncio.run(_run())