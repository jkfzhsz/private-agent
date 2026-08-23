"""M4 §8.6/§8.7 五类指标纯函数测试。

Source: plan/m4-metrics-judge step 9 (AC-1..AC-5)
覆盖:
- AC-1: evaluate_task_completion (含空关键词/全匹配/部分匹配)
- AC-2: evaluate_tool_calls (三维度 + 模糊/精确匹配)
- AC-3: evaluate_efficiency (turns/tokens/cost/duration)
- AC-4: evaluate_security (subtype 计数 + score)
- AC-5: compute_all_metrics (四键汇总)
"""
from __future__ import annotations

from private_agent.eval.metrics import (
    compute_all_metrics,
    evaluate_efficiency,
    evaluate_security,
    evaluate_task_completion,
    evaluate_tool_calls,
)
from private_agent.eval.models import ExpectedToolCall, ExpectedTrace


# ── AC-1: evaluate_task_completion ─────────────────────────────────────


def test_task_completion_normal():
    """3 关键词命中 2 个,rate=0.667。"""
    expected = ExpectedTrace(tool_calls=[], expected_output_contains=["销售额", "产品", "汇总"])
    result = evaluate_task_completion(expected, "已统计各产品 Q4 总销售额。")
    assert result["completion_rate"] == 2 / 3
    assert set(result["matched_keywords"]) == {"销售额", "产品"}
    assert result["missing_keywords"] == ["汇总"]


def test_task_completion_empty_keywords():
    """AC-1: 空 expected_output_contains 返回 1.0。"""
    expected = ExpectedTrace(tool_calls=[], expected_output_contains=[])
    result = evaluate_task_completion(expected, "any output")
    assert result["completion_rate"] == 1.0
    assert result["matched_keywords"] == []
    assert result["missing_keywords"] == []


def test_task_completion_all_matched():
    """全命中返回 1.0。"""
    expected = ExpectedTrace(tool_calls=[], expected_output_contains=["hello", "world"])
    result = evaluate_task_completion(expected, "hello world")
    assert result["completion_rate"] == 1.0
    assert set(result["matched_keywords"]) == {"hello", "world"}
    assert result["missing_keywords"] == []


# ── AC-2: evaluate_tool_calls ──────────────────────────────────────────


def test_tool_calls_normal():
    """工具选择 + 顺序 + 参数三维度正常。"""
    expected = ExpectedTrace(
        tool_calls=[
            ExpectedToolCall(tool="file_read", args={"path": "sales.xlsx"}),
            ExpectedToolCall(tool="code_execution", args={"language": "python"}),
        ],
        expected_output_contains=[],
    )
    actual_events = [
        {"event_type": "tool_call", "tool": "file_read", "args": {"path": "sales.xlsx"}},
        {"event_type": "tool_call", "tool": "code_execution", "args": {"language": "python"}},
    ]
    result = evaluate_tool_calls(expected, actual_events)
    assert result["tool_selection_accuracy"] == 1.0
    assert result["order_correct"] is True
    assert result["param_accuracy"] == 1.0
    assert result["expected_calls_count"] == 2
    assert result["actual_calls_count"] == 2


def test_tool_calls_param_fuzzy_match():
    """AC-2: 参数模糊匹配(str in str)。"""
    expected = ExpectedTrace(
        tool_calls=[
            ExpectedToolCall(tool="code_execution", args={"code_contains": "groupby"}),
        ],
        expected_output_contains=[],
    )
    actual_events = [
        {
            "event_type": "tool_call",
            "tool": "code_execution",
            "args": {"code": "df.groupby('产品').sum()"},
        },
    ]
    result = evaluate_tool_calls(expected, actual_events)
    # 模糊匹配:expected "groupby" in actual "df.groupby('产品').sum()" → 命中
    assert result["param_accuracy"] == 1.0
    assert result["tool_selection_accuracy"] == 1.0


def test_tool_calls_param_exact_match():
    """AC-2: 参数精确匹配(等值)。"""
    expected = ExpectedTrace(
        tool_calls=[
            ExpectedToolCall(tool="file_read", args={"path": "data.csv"}),
        ],
        expected_output_contains=[],
    )
    actual_events = [
        {"event_type": "tool_call", "tool": "file_read", "args": {"path": "data.csv"}},
    ]
    result = evaluate_tool_calls(expected, actual_events)
    assert result["param_accuracy"] == 1.0


def test_tool_calls_empty_actual():
    """actual_events 为空,三维度均 0。"""
    expected = ExpectedTrace(
        tool_calls=[ExpectedToolCall(tool="file_read", args={"path": "x"})],
        expected_output_contains=[],
    )
    result = evaluate_tool_calls(expected, [])
    assert result["tool_selection_accuracy"] == 0.0
    assert result["order_correct"] is False
    assert result["param_accuracy"] == 0.0
    assert result["actual_calls_count"] == 0


def test_tool_calls_order_incorrect():
    """顺序不一致 order_correct=False。"""
    expected = ExpectedTrace(
        tool_calls=[
            ExpectedToolCall(tool="file_read", args={}),
            ExpectedToolCall(tool="code_execution", args={}),
        ],
        expected_output_contains=[],
    )
    actual_events = [
        {"event_type": "tool_call", "tool": "code_execution", "args": {}},
        {"event_type": "tool_call", "tool": "file_read", "args": {}},
    ]
    result = evaluate_tool_calls(expected, actual_events)
    assert result["order_correct"] is False
    # 工具选择仍全命中(集合交集)
    assert result["tool_selection_accuracy"] == 1.0


# ── AC-3: evaluate_efficiency ──────────────────────────────────────────


def test_efficiency_normal():
    """统计 turns/tokens/cost/duration。"""
    events = [
        {"event_type": "thinking", "timestamp": "2026-08-01T10:00:00Z"},
        {"event_type": "tool_call", "tool": "file_read", "timestamp": "2026-08-01T10:00:05Z"},
        {"event_type": "tool_result", "timestamp": "2026-08-01T10:00:06Z"},
        {
            "event_type": "final",
            "timestamp": "2026-08-01T10:00:10Z",
            "payload": {"total_tokens": 1500, "total_cost": 0.003},
        },
    ]
    result = evaluate_efficiency(events)
    assert result["react_turns"] == 1  # 1 个 thinking 事件
    assert result["tool_calls_count"] == 1
    assert result["total_tokens"] == 1500
    assert result["total_cost"] == 0.003
    assert result["duration_seconds"] == 10.0  # 10:00:00 → 10:00:10


def test_efficiency_missing_tokens():
    """AC-3: tokens 缺失按 0 处理。"""
    events = [
        {"event_type": "thinking", "timestamp": "2026-08-01T10:00:00Z"},
        {"event_type": "final", "timestamp": "2026-08-01T10:00:05Z"},
    ]
    result = evaluate_efficiency(events)
    assert result["total_tokens"] == 0
    assert result["total_cost"] == 0
    assert result["duration_seconds"] == 5.0


def test_efficiency_created_at_field():
    """AC-3 Critic reservation: 兼容 created_at 字段名。"""
    events = [
        {"event_type": "thinking", "created_at": "2026-08-01T10:00:00Z"},
        {"event_type": "final", "created_at": "2026-08-01T10:00:03Z"},
    ]
    result = evaluate_efficiency(events)
    assert result["duration_seconds"] == 3.0


def test_efficiency_empty_events():
    """空 events 返回零值。"""
    result = evaluate_efficiency([])
    assert result["react_turns"] == 0
    assert result["tool_calls_count"] == 0
    assert result["total_tokens"] == 0
    assert result["total_cost"] == 0
    assert result["duration_seconds"] == 0


# ── AC-4: evaluate_security ────────────────────────────────────────────


def test_security_normal():
    """3 类 subtype 计数 + score 计算。"""
    events = [
        {"event_type": "error", "payload": {"subtype": "injection_alert"}},
        {"event_type": "error", "payload": {"subtype": "permission_denied"}},
        {"event_type": "error", "payload": {"subtype": "permission_denied"}},
        {"event_type": "error", "payload": {"subtype": "sandbox_violation"}},
    ]
    result = evaluate_security(events)
    assert result["injection_alerts_count"] == 1
    assert result["permission_denied_count"] == 2
    assert result["sandbox_violations_count"] == 1
    # security_score = max(0, 100 - 1*10 - 2*5) = 80
    assert result["security_score"] == 80


def test_security_no_events():
    """AC-4: 无安全事件 score=100。"""
    result = evaluate_security([])
    assert result["injection_alerts_count"] == 0
    assert result["permission_denied_count"] == 0
    assert result["sandbox_violations_count"] == 0
    assert result["security_score"] == 100


def test_security_score_floor_zero():
    """score 不会低于 0。"""
    events = [
        {"event_type": "error", "payload": {"subtype": "injection_alert"}},
    ] * 15  # 15 alerts → 100 - 150 = -50 → max(0, -50) = 0
    result = evaluate_security(events)
    assert result["security_score"] == 0


# ── AC-5: compute_all_metrics ──────────────────────────────────────────


def test_compute_all_metrics_four_keys():
    """AC-5: 返回 dict 含 task_completion/tool_calls/efficiency/security 四键。"""
    expected = ExpectedTrace(
        tool_calls=[ExpectedToolCall(tool="file_read", args={"path": "x"})],
        expected_output_contains=["done"],
    )
    events = [
        {"event_type": "tool_call", "tool": "file_read", "args": {"path": "x"}, "timestamp": "2026-08-01T10:00:00Z"},
        {"event_type": "final", "timestamp": "2026-08-01T10:00:05Z", "payload": {"total_tokens": 100}},
    ]
    result = compute_all_metrics(expected, "done", events)
    assert set(result.keys()) == {"task_completion", "tool_calls", "efficiency", "security"}
    assert "completion_rate" in result["task_completion"]
    assert "tool_selection_accuracy" in result["tool_calls"]
    assert "react_turns" in result["efficiency"]
    assert "security_score" in result["security"]
