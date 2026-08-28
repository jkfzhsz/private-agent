"""0.6.0 F1-9: 监督触发间隔自适应纯函数 —— 全分支单测(设计文档 §4.4.1)。

验收(设计文档 F1-9): 校正规则全分支; 验收 V7-② 连续 2 次无变化 → ×1.5 且
journal 留痕由 D-2 接线后端到端验证, 本模块验证纯函数语义。
"""
import pytest

from private_agent.core.supervision_interval import (
    COMPLEXITY_STEP_THRESHOLD,
    DEFAULT_INTERVALS,
    INTERVAL_MAX_SEC,
    INTERVAL_MIN_SEC,
    adjust_interval,
    initial_interval,
    make_journal_entry,
)


# ── 第一级: 类型分级初始值 ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "executor_type,task_type,expected",
    [
        ("script", "other", 30),
        ("script", "code", 30),      # script 不按 task_type 分档
        ("subagent", "search", 90),
        ("subagent", "analysis", 120),
        ("subagent", "code", 180),
        ("subagent", "other", 120),
        ("subagent", "unknown_type", 120),  # 未知 task_type 回退中性档
        ("wait", "other", 0),        # wait 到点触发, 无监督意义
    ],
)
def test_initial_interval_type_ladder(executor_type, task_type, expected):
    assert initial_interval(executor_type, task_type) == expected


def test_initial_interval_complex_flag_multiplier():
    """complex 显式标记 → 档位 ×1.5(上限 600 clamp)。"""
    assert initial_interval("subagent", "search", complex_task=True) == 135   # 90*1.5
    assert initial_interval("script", complex_task=True) == 45                # 30*1.5
    # code 180*1.5=270 < 600 不触顶
    assert initial_interval("subagent", "code", complex_task=True) == 270


def test_initial_interval_estimated_steps_threshold():
    """预计步骤 > 10 视为复杂(与显式标记等价); 恰好 10 步不视为复杂。"""
    assert initial_interval("subagent", "search", estimated_steps=COMPLEXITY_STEP_THRESHOLD) == 90
    assert initial_interval("subagent", "search", estimated_steps=COMPLEXITY_STEP_THRESHOLD + 1) == 135


def test_initial_interval_clamped_upper():
    """极端组合不超上限 600(虽然当前档位组合达不到, clamp 语义需成立)。"""
    assert initial_interval("subagent", "code", complex_task=True) <= INTERVAL_MAX_SEC


# ── 第二级: 运行内自校正 ────────────────────────────────────────────────

def test_adjust_shrunk_on_incident():
    """incident → ×0.7(下限 30), 安全优先。"""
    new, reason = adjust_interval(120, no_change_streak=0, incident=True)
    assert new == 84  # 120*0.7
    assert reason == "interval_shrunk_on_incident"


def test_adjust_incident_wins_over_no_change_streak():
    """incident 与 streak>=2 同时存在 → 缩短优先(安全优先)。"""
    new, reason = adjust_interval(120, no_change_streak=5, incident=True)
    assert new == 84
    assert reason == "interval_shrunk_on_incident"


def test_adjust_extended_after_two_no_change():
    """连续 2 次无变化 → ×1.5(上限 600)。"""
    new, reason = adjust_interval(120, no_change_streak=2, incident=False)
    assert new == 180
    assert reason == "interval_extended_on_no_change"


def test_adjust_no_change_below_threshold():
    """streak=1 不足阈值 → 不变。"""
    new, reason = adjust_interval(120, no_change_streak=1, incident=False)
    assert new == 120
    assert reason is None


def test_adjust_shrink_floored_at_min():
    """多次 incident 缩至下限 30 后不再缩(返回原因 None)。"""
    new, reason = adjust_interval(INTERVAL_MIN_SEC, no_change_streak=0, incident=True)
    assert new == INTERVAL_MIN_SEC
    assert reason is None


def test_adjust_extend_capped_at_max():
    """多次无变化涨至上限 600 后不再涨(返回原因 None)。"""
    new, reason = adjust_interval(INTERVAL_MAX_SEC, no_change_streak=3, incident=False)
    assert new == INTERVAL_MAX_SEC
    assert reason is None


def test_adjust_cycle_scenario():
    """真实场景循环: 120 →(2次无变化)→ 180 →(再2次)→ 270 →(incident)→ 189 →(incident)→ 132 →..."""
    cur = 120
    cur, r = adjust_interval(cur, no_change_streak=2, incident=False)
    assert (cur, r) == (180, "interval_extended_on_no_change")
    cur, r = adjust_interval(cur, no_change_streak=4, incident=False)
    assert (cur, r) == (270, "interval_extended_on_no_change")
    cur, r = adjust_interval(cur, no_change_streak=0, incident=True)
    assert cur == 189  # 270*0.7
    cur, r = adjust_interval(cur, no_change_streak=0, incident=True)
    assert cur == 132  # 189*0.7=132.3 → round=132


# ── journal 记录结构 ────────────────────────────────────────────────────

def test_make_journal_entry_shape():
    """journal 记录: kind/ts/from_sec/to_sec/reason 五字段(D 批接线契约)。"""
    entry = make_journal_entry(120, 180, "interval_extended_on_no_change")
    assert entry["kind"] == "interval_adjusted"
    assert entry["from_sec"] == 120
    assert entry["to_sec"] == 180
    assert entry["reason"] == "interval_extended_on_no_change"
    assert "T" in entry["ts"]  # ISO 时间戳(UTC)


def test_default_intervals_ladder_ordering():
    """分级表单调性: script < search < analysis <= other < code(设计文档 §4.4.1)。"""
    assert DEFAULT_INTERVALS["script"] < DEFAULT_INTERVALS["search"]
    assert DEFAULT_INTERVALS["search"] < DEFAULT_INTERVALS["analysis"]
    assert DEFAULT_INTERVALS["analysis"] <= DEFAULT_INTERVALS["other"]
    assert DEFAULT_INTERVALS["other"] < DEFAULT_INTERVALS["code"]
