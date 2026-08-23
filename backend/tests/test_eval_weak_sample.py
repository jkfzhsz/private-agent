"""M4 m4-continuous-evolution AC-1 - WeakSampleExtractor 测试。

Source: spec/m4-continuous-evolution AC-1
- WeakSampleExtractor.extract_from_low_score_runs(threshold=0.6, limit=50)
  从 eval_runs 提取低分样本(completion_rate < threshold)加入审核队列
- 每条低分样本加入 review_queue_repo,标记 suggested_as="boundary"
- 返回提取的候选列表
"""
from __future__ import annotations

from unittest.mock import AsyncMock

from private_agent.eval.weak_sample import WeakSampleExtractor


def _make_low_score_sample(sample_id: str, completion_rate: float) -> dict:
    """模拟 EvalRunRepo.get_low_score_samples 返回的低分样本。"""
    return {
        "sample_id": sample_id,
        "completion_rate": completion_rate,
    }


# ── AC-1: extract_from_low_score_runs ───────────────────────────────────


async def test_extract_returns_candidates_from_low_score_samples(tmp_path):
    """AC-1: extract 从 eval_repo.get_low_score_samples 返回低分样本,放入审核队列。"""
    eval_repo = AsyncMock()
    eval_repo.get_low_score_samples = AsyncMock(return_value=[
        _make_low_score_sample("low1", 0.3),
        _make_low_score_sample("low2", 0.5),
    ])
    review_queue_repo = AsyncMock()
    review_queue_repo.add = AsyncMock(side_effect=[1, 2])

    extractor = WeakSampleExtractor(
        eval_repo=eval_repo,
        review_queue_repo=review_queue_repo,
    )
    result = await extractor.extract_from_low_score_runs(threshold=0.6, limit=50)

    assert len(result) == 2
    assert result[0]["sample_id"] == "low1"
    assert result[1]["sample_id"] == "low2"
    # 调用 get_low_score_samples 时使用阈值 0.6
    eval_repo.get_low_score_samples.assert_awaited_once_with(0.6, 50)
    # 每条都加入 review_queue
    assert review_queue_repo.add.await_count == 2


async def test_extract_marks_suggested_as_boundary(tmp_path):
    """AC-1: 加入审核队列的项 suggested_as='boundary'。"""
    eval_repo = AsyncMock()
    eval_repo.get_low_score_samples = AsyncMock(return_value=[
        _make_low_score_sample("low1", 0.3),
    ])
    review_queue_repo = AsyncMock()
    review_queue_repo.add = AsyncMock(return_value=1)

    extractor = WeakSampleExtractor(
        eval_repo=eval_repo,
        review_queue_repo=review_queue_repo,
    )
    await extractor.extract_from_low_score_runs()

    add_call = review_queue_repo.add.await_args
    item = add_call.args[0]
    assert item["suggested_as"] == "boundary"
    assert item["status"] == "pending"
    assert item["sample_id"] == "low1"


async def test_extract_passes_threshold_and_limit_to_eval_repo():
    """AC-1: extract 透传 threshold + limit 给 eval_repo.get_low_score_samples。"""
    eval_repo = AsyncMock()
    eval_repo.get_low_score_samples = AsyncMock(return_value=[])
    review_queue_repo = AsyncMock()

    extractor = WeakSampleExtractor(
        eval_repo=eval_repo,
        review_queue_repo=review_queue_repo,
    )
    await extractor.extract_from_low_score_runs(threshold=0.4, limit=10)

    eval_repo.get_low_score_samples.assert_awaited_once_with(0.4, 10)


async def test_extract_uses_default_threshold_and_limit():
    """AC-1: 默认 threshold=0.6, limit=50(蓝图 §8.16)。"""
    eval_repo = AsyncMock()
    eval_repo.get_low_score_samples = AsyncMock(return_value=[])
    review_queue_repo = AsyncMock()

    extractor = WeakSampleExtractor(
        eval_repo=eval_repo,
        review_queue_repo=review_queue_repo,
    )
    await extractor.extract_from_low_score_runs()

    eval_repo.get_low_score_samples.assert_awaited_once_with(0.6, 50)


async def test_extract_returns_empty_list_when_no_low_score_samples():
    """AC-1: 无低分样本时返回空列表(不抛错)。"""
    eval_repo = AsyncMock()
    eval_repo.get_low_score_samples = AsyncMock(return_value=[])
    review_queue_repo = AsyncMock()

    extractor = WeakSampleExtractor(
        eval_repo=eval_repo,
        review_queue_repo=review_queue_repo,
    )
    result = await extractor.extract_from_low_score_runs()
    assert result == []
    review_queue_repo.add.assert_not_awaited()


async def test_extract_includes_failure_reason_in_queue_item():
    """AC-1: 审核项含 failure_reason(低分原因描述)。"""
    eval_repo = AsyncMock()
    eval_repo.get_low_score_samples = AsyncMock(return_value=[
        {"sample_id": "low1", "completion_rate": 0.3},
    ])
    review_queue_repo = AsyncMock()
    review_queue_repo.add = AsyncMock(return_value=1)

    extractor = WeakSampleExtractor(
        eval_repo=eval_repo,
        review_queue_repo=review_queue_repo,
    )
    await extractor.extract_from_low_score_runs()

    item = review_queue_repo.add.await_args.args[0]
    assert "failure_reason" in item
    assert "0.3" in item["failure_reason"] or "completion_rate" in item["failure_reason"]
