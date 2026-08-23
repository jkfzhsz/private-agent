"""M4 §8.16 eval/weak_sample.py - 低分案例提取器(蓝图 §8.16)。

Source: spec/m4-continuous-evolution §A (AC-1)
- WeakSampleExtractor.extract_from_low_score_runs(threshold=0.6, limit=50)
  从 eval_runs 提取低分样本(completion_rate < threshold)加入审核队列
- 每条低分样本加入 review_queue_repo,标记 suggested_as="boundary"
- 返回提取的候选列表

Spec drift: spec §A 构造函数签名包含 dataset_repo 参数,但 extract_from_low_score_runs
不直接使用 dataset_repo(入库由 ReviewQueueRepo.update_status 持有 dataset_repo 完成)。
按工程约定"函数参数声明后必须在函数体内使用",移除该参数。
"""
from __future__ import annotations

from private_agent.eval.repos import EvalRunRepo, ReviewQueueRepo
from private_agent.observability.logging import setup_logger

__all__ = ["WeakSampleExtractor"]


class WeakSampleExtractor:
    """低分案例提取器(蓝图 §8.16,AC-1)。

    Args:
        eval_repo: EvalRunRepo,提供 get_low_score_samples 接口。
        review_queue_repo: ReviewQueueRepo,加入审核队列。
    """

    def __init__(
        self,
        *,
        eval_repo: EvalRunRepo,
        review_queue_repo: ReviewQueueRepo,
    ) -> None:
        self._eval_repo = eval_repo
        self._review_queue_repo = review_queue_repo
        self._logger = setup_logger("private_agent.eval.weak_sample")

    async def extract_from_low_score_runs(
        self,
        *,
        threshold: float = 0.6,
        limit: int = 50,
    ) -> list[dict]:
        """AC-1: 从低分评估案例中提取薄弱用例。

        流程:
        1. eval_repo.get_low_score_samples(threshold, limit) 获取低分样本
        2. 每条加入 review_queue_repo,标记 suggested_as="boundary"
        3. 返回提取的候选列表

        Args:
            threshold: 任务完成率阈值,< threshold 视为低分(默认 0.6)。
            limit: 单次提取的最大样本数(默认 50)。

        Returns:
            低分样本候选列表(每项含 sample_id + completion_rate)。
        """
        low_score_samples = await self._eval_repo.get_low_score_samples(
            threshold, limit
        )
        self._logger.info(
            "提取到 %d 条低分样本(threshold=%.2f, limit=%d)",
            len(low_score_samples),
            threshold,
            limit,
        )

        candidates: list[dict] = []
        for sample in low_score_samples:
            sample_id = sample.get("sample_id", "")
            completion_rate = sample.get("completion_rate", 0.0)
            failure_reason = (
                f"task_completion.completion_rate={completion_rate} < threshold={threshold}"
            )
            item = {
                "source_run_id": sample.get("run_id"),
                "sample_id": sample_id,
                "sample_input": sample.get("sample_input", ""),
                "actual_output": sample.get("actual_output", ""),
                "actual_events": sample.get("actual_events", []),
                "failure_reason": failure_reason,
                "suggested_as": "boundary",
                "status": "pending",
            }
            await self._review_queue_repo.add(item)
            candidates.append({
                "sample_id": sample_id,
                "completion_rate": completion_rate,
                "failure_reason": failure_reason,
            })

        return candidates
