"""M4 §8.12 eval/version_compare.py - 版本对比(蓝图 §8.12)。

Source: plan/m4-version-compare-rollback step 1
- EvalComparator: 双维度筛选(同 model_id + 同 skill_version)+ 取最新成功基线
- _compute_diff: 计算指标差值,标记 improved/degraded/stable
- InsufficientDataError: 缺数据异常
- 退化仅标记,不自动阻断(蓝图 §8.13)
"""
from __future__ import annotations

from private_agent.eval.repos import EvalRunRepo

__all__ = ["EvalComparator", "InsufficientDataError"]


class InsufficientDataError(Exception):
    """版本对比数据不足异常(蓝图 §8.12)。"""


class EvalComparator:
    """版本对比器(蓝图 §8.12,AC-1, AC-2, AC-6)。

    双维度筛选:同 model_id + 同 skill_version,取最新成功基线(completed run)。
    """

    def __init__(self, eval_repo: EvalRunRepo) -> None:
        self._eval_repo = eval_repo

    async def compare_versions(
        self,
        *,
        skill_name: str,
        base_version: str,
        target_version: str,
        model_id: str | None = None,
    ) -> dict:
        """对比两个版本的评估结果(AC-1)。

        Args:
            skill_name: Skill 名称(用于日志/校验,实际 list_runs 不按 skill_name 过滤)。
            base_version: 基线版本。
            target_version: 目标版本。
            model_id: 模型 ID(None 时跨模型,但蓝图建议同模型)。

        Returns:
            {base_version, target_version, model_id, base_metrics, target_metrics, diff}

        Raises:
            InsufficientDataError: base 或 target 无 completed runs。
        """
        base_runs = await self._eval_repo.list_runs(
            skill_version=base_version, model_id=model_id, status="completed"
        )
        target_runs = await self._eval_repo.list_runs(
            skill_version=target_version, model_id=model_id, status="completed"
        )
        if not base_runs:
            raise InsufficientDataError(
                f"base_version='{base_version}' 无 completed runs(model_id={model_id})"
            )
        if not target_runs:
            raise InsufficientDataError(
                f"target_version='{target_version}' 无 completed runs(model_id={model_id})"
            )
        # list_runs 按 started_at DESC 排序,取 [0] 为最新
        base_metrics = base_runs[0].get("metrics") or {}
        target_metrics = target_runs[0].get("metrics") or {}
        diff = self._compute_diff(base_metrics, target_metrics)
        return {
            "base_version": base_version,
            "target_version": target_version,
            "model_id": model_id,
            "base_metrics": base_metrics,
            "target_metrics": target_metrics,
            "diff": diff,
        }

    def _compute_diff(self, base: dict, target: dict) -> dict:
        """计算指标差值,标记 improved/degraded/stable(AC-2, AC-6)。

        正数=提升,负数=退化,零=稳定。退化仅标记,不阻断(蓝图 §8.13)。

        Args:
            base: 基线 metrics({category: {metric: value}})。
            target: 目标 metrics。

        Returns:
            {category: {metric: {delta, status}}}。
        """
        diff: dict = {}
        for category, base_metrics in base.items():
            if not isinstance(base_metrics, dict):
                continue
            target_metrics = target.get(category, {})
            if not isinstance(target_metrics, dict):
                continue
            diff[category] = {}
            for metric, base_val in base_metrics.items():
                target_val = target_metrics.get(metric, 0)
                if not isinstance(base_val, (int, float)) or not isinstance(
                    target_val, (int, float)
                ):
                    continue
                delta = target_val - base_val
                if delta > 0:
                    status = "improved"
                elif delta < 0:
                    status = "degraded"
                else:
                    status = "stable"
                diff[category][metric] = {"delta": delta, "status": status}
        return diff
