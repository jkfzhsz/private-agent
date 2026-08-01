"""M4 §8.9 EvalRunner - 评估执行编排器(蓝图 §8.9,AC-7, AC-8)。

Source: spec/m4-eval-runner-replay AC-7, AC-8 + plan step 7
- 离线批量(offline):仅调模型,不执行工具,actual_events=[]
- 交互式回放(replay):调 ReplayExecutor.run_replay,获取 actual_output + actual_events
- sample_subset="quick" 取前 regression_subset 条(从 cfg["eval"]["regression_subset"] 读)
- 失败时 fail_run(metrics.error),不阻塞已完成的样本
- 每条样本调 HybridEvaluator.evaluate_sample 计算五类指标
"""
from __future__ import annotations

from typing import Any

import asyncpg

from private_agent.eval.hybrid_eval import HybridEvaluator
from private_agent.eval.models import EvalSample
from private_agent.eval.repos import EvalDatasetRepo, EvalRunRepo, VersionSnapshotRepo
from private_agent.eval.replay import ReplayExecutor
from private_agent.models.base import ModelAdapter
from private_agent.observability.logging import setup_logger
from private_agent.skills.loader import SkillLoader
from private_agent.skills.models import Skill
from private_agent.tools.registry import ToolRegistry

__all__ = ["EvalRunner"]


class EvalRunner:
    """评估执行编排器(蓝图 §8.9)。

    编排离线批量评估与交互式回放,逐条样本执行 → HybridEvaluator 评判 → 汇总 metrics。

    Args:
        dataset_repo: eval_datasets 表 CRUD。
        eval_repo: eval_runs 表 CRUD。
        snapshot_repo: version_snapshots 表 CRUD(供 load_version 用,本 spec 预留)。
        skill_loader: Skill 加载器(offline 不需要,replay 需要 skill.system_prompt + 白名单)。
        model_adapter: 模型适配器。
        hybrid_evaluator: 混合评判器(规则指标 + LLM-Judge)。
        cfg: 配置 dict(读 cfg["eval"]["regression_subset"])。
        context_manager_cls: ContextManager 类(replay 模式需要)。
        tool_registry: ToolRegistry(replay 模式需要)。
        mock_data_dir: mock 数据目录(replay + mock_enabled=True 时需要)。
    """

    def __init__(
        self,
        *,
        dataset_repo: EvalDatasetRepo,
        eval_repo: EvalRunRepo,
        snapshot_repo: VersionSnapshotRepo,
        skill_loader: SkillLoader,
        model_adapter: ModelAdapter,
        hybrid_evaluator: HybridEvaluator,
        cfg: dict | None = None,
        context_manager_cls: type | None = None,
        tool_registry: ToolRegistry | None = None,
        mock_data_dir: str | None = None,
    ) -> None:
        self._dataset_repo = dataset_repo
        self._eval_repo = eval_repo
        self._snapshot_repo = snapshot_repo
        self._skill_loader = skill_loader
        self._model_adapter = model_adapter
        self._hybrid_evaluator = hybrid_evaluator
        self._cfg = cfg or {}
        self._context_manager_cls = context_manager_cls
        self._tool_registry = tool_registry
        self._mock_data_dir = mock_data_dir
        self._logger = setup_logger("private_agent.eval.runner")

    async def run_evaluation(
        self,
        *,
        skill_name: str,
        skill_version: str,
        model_id: str,
        eval_mode: str,
        mock_enabled: bool = False,
        sample_subset: str | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> str:
        """执行评估,返回 run_id(蓝图 §8.9,AC-7, AC-8)。

        流程:
        1. 加载数据集(dataset_repo.load_test_set)
        2. sample_subset="quick" 时取前 regression_subset 条
        3. 创建评估运行(eval_repo.create_run)
        4. 逐条执行样本(_eval_sample)
        5. 汇总 metrics + sample_results(eval_repo.update_run_metrics)
        6. 完成(eval_repo.complete_run)
        失败时 eval_repo.fail_run。

        Args:
            skill_name: Skill 名(用于查数据集 scenario)。
            skill_version: Skill 版本。
            model_id: 模型 ID。
            eval_mode: "offline" | "replay"。
            mock_enabled: replay 模式是否启用 mock(offline 忽略)。
            sample_subset: None=全量, "quick"=前 regression_subset 条。
            conn: asyncpg.Connection。

        Returns:
            run_id(str)。
        """
        if conn is None:
            raise ValueError("run_evaluation 需要 conn 参数")

        # replay 模式前置校验
        if eval_mode == "replay":
            if self._tool_registry is None:
                raise ValueError(
                    "replay 模式需要 tool_registry(EvalRunner 构造函数未配置)"
                )
            if self._context_manager_cls is None:
                raise ValueError(
                    "replay 模式需要 context_manager_cls(EvalRunner 构造函数未配置)"
                )

        # 1. 加载数据集
        samples = await self._dataset_repo.load_test_set(skill_name, skill_version)

        # 2. sample_subset="quick" 取前 regression_subset 条
        if sample_subset == "quick":
            regression_subset = self._cfg.get("eval", {}).get("regression_subset", 5)
            samples = samples[:regression_subset]

        # 3. 创建评估运行
        run_id = await self._eval_repo.create_run(
            skill_name=skill_name,
            skill_version=skill_version,
            model_id=model_id,
            dataset_version=skill_version,
            eval_mode=eval_mode,
            mock_enabled=mock_enabled,
        )

        # 4. 逐条执行样本(单条失败标记后继续下一条,spec Edge cases)
        # replay 模式:加载 Skill(供 system_prompt + 工具白名单)
        skill: Skill | None = None
        try:
            if eval_mode == "replay":
                skill = await self._skill_loader.load(skill_name, conn=conn)
        except Exception as e:
            # Skill 加载失败 → 整体 fail_run(无 skill 无法 replay)
            self._logger.exception("EvalRunner Skill 加载失败: run_id=%s", run_id)
            await self._eval_repo.fail_run(run_id, f"SkillLoadError: {e}")
            return run_id

        sample_results: list[dict] = []
        for sample in samples:
            try:
                result = await self._eval_sample(
                    sample=sample,
                    eval_mode=eval_mode,
                    mock_enabled=mock_enabled,
                    skill=skill,
                    model_id=model_id,
                    conn=conn,
                )
                sample_results.append(result)
            except Exception as e:
                # 单条样本失败:记录后继续下一条(spec Edge cases: sample 标记 failed)
                self._logger.exception(
                    "EvalRunner 单样本失败: run_id=%s, sample_id=%s",
                    run_id,
                    sample.sample_id,
                )
                sample_results.append({
                    "sample_id": sample.sample_id,
                    "actual_output": "",
                    "actual_events": [],
                    "metrics": {"error": f"{type(e).__name__}: {e}"},
                })

        # 5. 汇总 metrics + sample_results
        aggregated_metrics = self._aggregate_metrics(sample_results)
        await self._eval_repo.update_run_metrics(run_id, aggregated_metrics, sample_results)

        # 6. 完成
        await self._eval_repo.complete_run(run_id)
        return run_id

    async def _eval_sample(
        self,
        *,
        sample: EvalSample,
        eval_mode: str,
        mock_enabled: bool,
        skill: Skill | None,
        model_id: str,
        conn: asyncpg.Connection,
    ) -> dict:
        """评估单条样本(蓝图 §8.9)。

        offline: 仅调模型(不执行工具),actual_events=[]
        replay: 调 ReplayExecutor.run_replay,获取 actual_output + actual_events

        Args:
            sample: 评估样本。
            eval_mode: "offline" | "replay"。
            mock_enabled: replay 模式是否启用 mock。
            skill: Skill 实例(replay 模式需要,offline 为 None)。
            model_id: 模型 ID(透传到 ReplayExecutor 创建临时会话)。
            conn: asyncpg.Connection。

        Returns:
            HybridEvaluator.evaluate_sample 结果(sample_id + actual_output + actual_events + metrics)。
        """
        if eval_mode == "offline":
            # 离线模式:仅调模型,不执行工具
            messages = [{"role": "user", "content": sample.input}]
            result = await self._model_adapter.chat(messages, tools=[])
            actual_output = result.content
            actual_events: list[dict] = []
        elif eval_mode == "replay":
            # 回放模式:调 ReplayExecutor
            if skill is None:
                raise ValueError("replay 模式需要 skill(_eval_sample 收到 skill=None)")
            if self._mock_data_dir is None and mock_enabled:
                raise ValueError("replay + mock_enabled=True 需要 mock_data_dir")
            executor = ReplayExecutor(
                context_manager_cls=self._context_manager_cls,
                model_adapter=self._model_adapter,
                tool_registry=self._tool_registry,
                mock_data_dir=self._mock_data_dir,
            )
            actual_output, actual_events = await executor.run_replay(
                sample=sample,
                skill=skill,
                model_id=model_id,
                mock_enabled=mock_enabled,
                conn=conn,
            )
        else:
            raise ValueError(f"未知 eval_mode: {eval_mode}")

        # HybridEvaluator 评判
        return await self._hybrid_evaluator.evaluate_sample(
            sample=sample,
            actual_output=actual_output,
            actual_events=actual_events,
        )

    @staticmethod
    def _aggregate_metrics(sample_results: list[dict]) -> dict:
        """汇总所有样本的 metrics(平均值)。

        Args:
            sample_results: 每条样本的 evaluate_sample 结果列表。

        Returns:
            汇总 metrics dict(含 task_completion/tool_calls/efficiency/security/llm_judge 平均值 + sample_count)。
        """
        if not sample_results:
            return {"sample_count": 0}

        # 收集五类指标的平均值
        metric_keys = ["task_completion", "tool_calls", "efficiency", "security", "llm_judge"]
        aggregated: dict[str, Any] = {"sample_count": len(sample_results)}

        for key in metric_keys:
            values = []
            for sr in sample_results:
                metrics = sr.get("metrics", {})
                if key in metrics and isinstance(metrics[key], dict):
                    # 取子指标的平均值(如 task_completion.completion_rate)
                    sub_values = [
                        v for v in metrics[key].values() if isinstance(v, (int, float))
                    ]
                    if sub_values:
                        values.append(sum(sub_values) / len(sub_values))
                elif isinstance(metrics.get(key), (int, float)):
                    values.append(metrics[key])
            if values:
                aggregated[key] = sum(values) / len(values)

        return aggregated
