"""M4 §8.8 HybridEvaluator 混合评判编排器测试。

Source: plan/m4-metrics-judge step 11 (AC-9)
覆盖:
- AC-9: HybridEvaluator.evaluate_sample() 返回五类指标,Judge 失败不阻塞
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from private_agent.eval.hybrid_eval import HybridEvaluator
from private_agent.eval.judge import LLMJudge
from private_agent.eval.models import EvalSample, ExpectedToolCall, ExpectedTrace
from private_agent.models.base import ChatResult, ProviderError


def _make_sample() -> EvalSample:
    """构造测试样本。"""
    return EvalSample(
        sample_id="test_001",
        scenario="office",
        skill_name="office",
        skill_version="1.0.0",
        case_type="normal",
        difficulty="easy",
        split="test",
        input="汇总销售数据",
        expected_react_trace=ExpectedTrace(
            tool_calls=[ExpectedToolCall(tool="file_read", args={"path": "sales.xlsx"})],
            expected_output_contains=["销售额"],
        ),
        expected_output="已汇总销售额",
    )


@pytest.mark.asyncio
async def test_evaluate_sample_normal():
    """AC-9: 规则指标 + Judge 均成功,五键齐全。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(
        return_value=ChatResult(
            content='{"response_quality": 4, "task_completion": 5, "quality_reason": "good", "completion_reason": "all"}'
        )
    )
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    evaluator = HybridEvaluator(judge=judge)

    sample = _make_sample()
    events = [
        {"event_type": "tool_call", "tool": "file_read", "args": {"path": "sales.xlsx"}, "timestamp": "2026-08-01T10:00:00Z"},
        {"event_type": "final", "timestamp": "2026-08-01T10:00:05Z", "payload": {"total_tokens": 100}},
    ]
    result = await evaluator.evaluate_sample(sample, actual_output="已汇总销售额", actual_events=events)

    assert result["sample_id"] == "test_001"
    assert result["actual_output"] == "已汇总销售额"
    assert result["actual_events"] == events
    assert set(result["metrics"].keys()) == {"task_completion", "tool_calls", "efficiency", "security", "llm_judge"}
    assert result["metrics"]["task_completion"]["completion_rate"] == 1.0
    assert result["metrics"]["llm_judge"]["response_quality"] == 4


@pytest.mark.asyncio
async def test_evaluate_sample_judge_failed():
    """AC-9: Judge 降级,llm_judge 返回 0 分,其余四类正常。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(side_effect=ProviderError("glm", "timeout"))
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    evaluator = HybridEvaluator(judge=judge)

    sample = _make_sample()
    result = await evaluator.evaluate_sample(sample, actual_output="done", actual_events=[])

    # 四类规则指标正常
    assert "task_completion" in result["metrics"]
    assert "tool_calls" in result["metrics"]
    assert "efficiency" in result["metrics"]
    assert "security" in result["metrics"]
    # Judge 降级返回 0 分
    assert result["metrics"]["llm_judge"]["response_quality"] == 0
    assert result["metrics"]["llm_judge"]["task_completion"] == 0
    assert "judge_call_failed" in result["metrics"]["llm_judge"]["quality_reason"]


@pytest.mark.asyncio
async def test_evaluate_sample_empty_events():
    """AC-9: actual_events 为空,规则指标返回零值,Judge 正常。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(
        return_value=ChatResult(
            content='{"response_quality": 3, "task_completion": 2, "quality_reason": "x", "completion_reason": "y"}'
        )
    )
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    evaluator = HybridEvaluator(judge=judge)

    sample = _make_sample()
    result = await evaluator.evaluate_sample(sample, actual_output="", actual_events=[])

    # 规则指标零值
    assert result["metrics"]["efficiency"]["react_turns"] == 0
    assert result["metrics"]["efficiency"]["tool_calls_count"] == 0
    assert result["metrics"]["security"]["security_score"] == 100
    assert result["metrics"]["tool_calls"]["actual_calls_count"] == 0
    # Judge 正常
    assert result["metrics"]["llm_judge"]["response_quality"] == 3
