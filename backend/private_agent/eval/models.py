"""M4 §8.4 eval/models.py - Pydantic 校验模型(蓝图 §8.4)。

Source: plan/m4-eval-foundation step 3 (AC-2)
- ExpectedToolCall: 单次工具调用期望(tool + args + expected_result_type)
- ExpectedTrace: 期望的 ReAct 轨迹(tool_calls[] + expected_output_contains[])
- EvalSample: 评估样本聚合根(对应 eval_datasets 表一行)
- InvalidSampleFormatError: 入库前校验失败异常
- validate_expected_trace(trace: dict) -> ExpectedTrace: 入库前校验入口
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ExpectedToolCall",
    "ExpectedTrace",
    "EvalSample",
    "InvalidSampleFormatError",
    "validate_expected_trace",
]


CaseType = Literal["normal", "boundary", "error"]
Difficulty = Literal["easy", "medium", "hard"]
Split = Literal["train", "test"]


class ExpectedToolCall(BaseModel):
    """蓝图 §8.4 单次工具调用期望。"""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expected_result_type: str | None = None


class ExpectedTrace(BaseModel):
    """蓝图 §8.4 期望的 ReAct 轨迹。"""

    tool_calls: list[ExpectedToolCall]
    expected_output_contains: list[str]


class EvalSample(BaseModel):
    """蓝图 §8.4 评估样本聚合根(对应 eval_datasets 表一行)。"""

    sample_id: str
    scenario: str
    skill_name: str
    skill_version: str
    case_type: CaseType
    difficulty: Difficulty
    split: Split
    input: str
    expected_react_trace: ExpectedTrace
    expected_output: str | None = None


class InvalidSampleFormatError(Exception):
    """蓝图 §8.4 样本结构非法异常(入库前校验失败时抛出)。"""


def validate_expected_trace(trace: dict) -> ExpectedTrace:
    """入库前校验 expected_react_trace 结构(蓝图 §8.4,AC-2/AC-3 入口)。

    Args:
        trace: 待校验的 dict(应含 tool_calls 数组 + expected_output_contains 数组)。

    Returns:
        ExpectedTrace 实例。

    Raises:
        InvalidSampleFormatError: 结构非法时抛出(非 dict / 缺 tool_calls /
            tool_calls 非数组 / 缺 expected_output_contains /
            expected_output_contains 非数组)。
    """
    if not isinstance(trace, dict):
        raise InvalidSampleFormatError(
            f"expected_react_trace 必须是 dict,实际类型: {type(trace).__name__}"
        )
    if "tool_calls" not in trace:
        raise InvalidSampleFormatError("expected_react_trace 缺 tool_calls 字段")
    if not isinstance(trace["tool_calls"], list):
        raise InvalidSampleFormatError(
            f"tool_calls 必须是 list,实际类型: {type(trace['tool_calls']).__name__}"
        )
    if "expected_output_contains" not in trace:
        raise InvalidSampleFormatError(
            "expected_react_trace 缺 expected_output_contains 字段"
        )
    if not isinstance(trace["expected_output_contains"], list):
        raise InvalidSampleFormatError(
            "expected_output_contains 必须是 list,实际类型: "
            f"{type(trace['expected_output_contains']).__name__}"
        )
    try:
        return ExpectedTrace.model_validate(trace)
    except Exception as exc:  # Pydantic 校验失败(如 tool_calls 元素结构错)
        raise InvalidSampleFormatError(f"ExpectedTrace 校验失败: {exc}") from exc
