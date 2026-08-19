"""M4 §8.8 HybridEvaluator 混合评判编排器(蓝图 §8.8)。

Source: plan/m4-metrics-judge step 8 (AC-9)
- HybridEvaluator: 编排规则指标(同步) + LLM-Judge(异步)
- evaluate_sample: 返回完整 metrics 含五类指标(四类规则 + llm_judge)

Judge 调用失败不阻塞,降级返回 0 分。
"""
from __future__ import annotations

from private_agent.eval.judge import (
    LLMJudge,
    build_judge_adapter,
    load_judge_prompt,
)
from private_agent.eval.metrics import compute_all_metrics
from private_agent.eval.models import EvalSample

__all__ = ["HybridEvaluator"]


class HybridEvaluator:
    """混合评判编排器:规则指标(同步) + LLM-Judge(异步)(蓝图 §8.8)。

    Args:
        judge: LLMJudge 实例,用于异步调用 Judge 模型。
    """

    def __init__(self, *, judge: LLMJudge) -> None:
        self._judge = judge

    @classmethod
    def from_cfg(cls, cfg: dict) -> "HybridEvaluator":
        """B1 P1-10: 从 cfg 构造 HybridEvaluator(供 api/eval.py _build_eval_runner 使用)。

        Args:
            cfg: 配置 dict,需含 eval.judge_model + eval.judge_prompt_dir。

        Returns:
            HybridEvaluator 实例。
        """
        adapter = build_judge_adapter(cfg)
        prompt = load_judge_prompt(cfg)
        return cls(judge=LLMJudge(adapter=adapter, prompt_template=prompt))

    async def evaluate_sample(
        self,
        sample: EvalSample,
        actual_output: str,
        actual_events: list[dict],
    ) -> dict:
        """混合评判:规则指标 + LLM-Judge(蓝图 §8.8)。

        规则指标用 compute_all_metrics(同步纯函数),LLM-Judge 调 judge.judge(异步)。
        Judge 失败不阻塞,降级返回 0 分。

        Args:
            sample: 评估样本(含 expected_react_trace + expected_output)。
            actual_output: 实际输出文本。
            actual_events: 实际事件列表。

        Returns:
            {
                sample_id: str,
                actual_output: str,
                actual_events: list[dict],
                metrics: {task_completion, tool_calls, efficiency, security, llm_judge}
            }
        """
        # 规则指标(同步纯函数,计算量极小,不需 asyncio.to_thread)
        rule_metrics = compute_all_metrics(
            sample.expected_react_trace, actual_output, actual_events
        )

        # LLM-Judge(异步,降级不阻塞)
        llm_judge_result = await self._judge.judge(
            user_input=sample.input,
            agent_response=actual_output,
            expected_output=sample.expected_output,
        )

        return {
            "sample_id": sample.sample_id,
            "actual_output": actual_output,
            "actual_events": actual_events,
            "metrics": {
                **rule_metrics,
                "llm_judge": llm_judge_result,
            },
        }
