"""M4 §8.4 eval/models.py - Pydantic 校验模型测试。

Source: plan/m4-eval-foundation step 15 (AC-2)
- EvalSample 合法构造
- 非法 expected_react_trace(缺 tool_calls / tool_calls 非数组 / expected_output_contains 非数组)抛 InvalidSampleFormatError
- validate_expected_trace 正常返回 ExpectedTrace
"""
import pytest
from pydantic import ValidationError

from private_agent.eval.models import (
    EvalSample,
    ExpectedToolCall,
    ExpectedTrace,
    InvalidSampleFormatError,
    validate_expected_trace,
)


def _valid_trace_dict() -> dict:
    return {
        "tool_calls": [{"tool": "calculator", "args": {"expr": "1+1"}}],
        "expected_output_contains": ["2"],
    }


def _valid_sample_kwargs() -> dict:
    return {
        "sample_id": "office_001_normal",
        "scenario": "office",
        "skill_name": "office",
        "skill_version": "1.0.0",
        "case_type": "normal",
        "difficulty": "easy",
        "split": "test",
        "input": "计算 1+1",
        "expected_react_trace": _valid_trace_dict(),
        "expected_output": "2",
    }


# ── ExpectedToolCall / ExpectedTrace ────────────────────────────────────


def test_expected_tool_call_defaults():
    """ExpectedToolCall 只 tool 必填,args 默认 {},expected_result_type 默认 None。"""
    call = ExpectedToolCall(tool="calculator")
    assert call.tool == "calculator"
    assert call.args == {}
    assert call.expected_result_type is None


def test_expected_trace_accepts_valid_dict():
    """validate_expected_trace 接受合法 dict,返回 ExpectedTrace 实例。"""
    trace = validate_expected_trace(_valid_trace_dict())
    assert isinstance(trace, ExpectedTrace)
    assert len(trace.tool_calls) == 1
    assert trace.tool_calls[0].tool == "calculator"
    assert trace.expected_output_contains == ["2"]


# ── EvalSample 合法构造 ─────────────────────────────────────────────────


def test_eval_sample_valid_construction():
    """EvalSample 合法字段构造成功。"""
    sample = EvalSample(**_valid_sample_kwargs())
    assert sample.sample_id == "office_001_normal"
    assert sample.scenario == "office"
    assert sample.case_type == "normal"
    assert sample.difficulty == "easy"
    assert sample.split == "test"
    assert sample.expected_output == "2"
    assert isinstance(sample.expected_react_trace, ExpectedTrace)


def test_eval_sample_expected_output_optional():
    """expected_output 可为 None。"""
    kwargs = _valid_sample_kwargs()
    kwargs["expected_output"] = None
    sample = EvalSample(**kwargs)
    assert sample.expected_output is None


def test_eval_sample_case_type_invalid_raises():
    """case_type 非 normal/boundary/error 时 Pydantic 校验失败。"""
    kwargs = _valid_sample_kwargs()
    kwargs["case_type"] = "weird"
    with pytest.raises(ValidationError):
        EvalSample(**kwargs)


def test_eval_sample_difficulty_invalid_raises():
    """difficulty 非 easy/medium/hard 时 Pydantic 校验失败。"""
    kwargs = _valid_sample_kwargs()
    kwargs["difficulty"] = "extreme"
    with pytest.raises(ValidationError):
        EvalSample(**kwargs)


def test_eval_sample_split_invalid_raises():
    """split 非 train/test 时 Pydantic 校验失败。"""
    kwargs = _valid_sample_kwargs()
    kwargs["split"] = "valid"
    with pytest.raises(ValidationError):
        EvalSample(**kwargs)


# ── validate_expected_trace 非法结构抛 InvalidSampleFormatError ─────────


def test_validate_expected_trace_missing_tool_calls_raises():
    """缺 tool_calls 键 → InvalidSampleFormatError。"""
    with pytest.raises(InvalidSampleFormatError):
        validate_expected_trace({"expected_output_contains": ["x"]})


def test_validate_expected_trace_tool_calls_not_list_raises():
    """tool_calls 非数组 → InvalidSampleFormatError。"""
    with pytest.raises(InvalidSampleFormatError):
        validate_expected_trace({"tool_calls": "not-a-list", "expected_output_contains": []})


def test_validate_expected_trace_missing_expected_output_contains_raises():
    """缺 expected_output_contains 键 → InvalidSampleFormatError。"""
    with pytest.raises(InvalidSampleFormatError):
        validate_expected_trace({"tool_calls": []})


def test_validate_expected_trace_expected_output_contains_not_list_raises():
    """expected_output_contains 非数组 → InvalidSampleFormatError。"""
    with pytest.raises(InvalidSampleFormatError):
        validate_expected_trace(
            {"tool_calls": [], "expected_output_contains": "not-a-list"}
        )


def test_validate_expected_trace_not_dict_raises():
    """传入非 dict → InvalidSampleFormatError。"""
    with pytest.raises(InvalidSampleFormatError):
        validate_expected_trace("not-a-dict")  # type: ignore[arg-type]


def test_invalid_sample_format_error_is_exception():
    """InvalidSampleFormatError 是 Exception 子类。"""
    assert issubclass(InvalidSampleFormatError, Exception)
