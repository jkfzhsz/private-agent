"""0.6.0 F1-7: 改道预算逻辑层 BudgetLedger —— 计数/超限/追加状态机全分支单测。

验收(设计文档 F1-7): 计数/超限/追加状态机; V2 防漂移语义前置:
连续失败 2 次(默认预算) → 第 3 次尝试前 allowed=False(转 escalate)。
"""
import pytest

from private_agent.core.mission_budget import (
    FALLBACK_JOURNAL_KIND,
    approve_fallback,
    budget_status,
    can_fallback,
    consume_fallback,
    count_fallbacks,
    default_budget,
    make_fallback_entry,
)


# ── count_fallbacks ─────────────────────────────────────────────────────

def test_count_fallbacks_empty_and_none():
    assert count_fallbacks(None) == 0
    assert count_fallbacks([]) == 0


def test_count_fallbacks_jsonb_str():
    """JSONB 返回 str(2026-08-15 教训) → 正常解析计数。"""
    journal = '[{"kind": "fallback", "detail": "换镜像"}, {"kind": "created"}]'
    assert count_fallbacks(journal) == 1


def test_count_fallbacks_only_counts_fallback_kind():
    journal = [
        {"kind": "created"},
        {"kind": "fallback", "detail": "a"},
        {"kind": "fallback", "detail": "b"},
        {"kind": "interval_adjusted"},  # 非 fallback 不计
    ]
    assert count_fallbacks(journal) == 2


def test_count_fallbacks_malformed_str_returns_zero():
    assert count_fallbacks("not-json{") == 0


# ── budget_status ───────────────────────────────────────────────────────

def test_budget_status_snapshot():
    budget = {"max_fallbacks": 2, "max_total_sec": 3600}
    journal = [{"kind": "fallback", "detail": "a"}]
    st = budget_status(budget, journal)
    assert st == {"used": 1, "max": 2, "remaining": 1, "exhausted": False}


def test_budget_status_exhausted():
    budget = {"max_fallbacks": 1}
    journal = [{"kind": "fallback", "detail": "a"}]
    st = budget_status(budget, journal)
    assert st["exhausted"] is True and st["remaining"] == 0


def test_budget_status_none_budget_defaults():
    """budget 缺失 → 按默认 max_fallbacks=2 语义(与 mission_cfg 一致)。"""
    st = budget_status(None, [])
    assert st["max"] == 2 and st["exhausted"] is False


# ── can_fallback / consume_fallback ─────────────────────────────────────

def test_can_fallback_allowed_then_exhausted():
    budget = default_budget()  # max=2
    journal: list = []
    ok, r = can_fallback(budget, journal)
    assert ok and "1/2" in r
    # 第一次改道
    budget, journal, ok, r = consume_fallback(budget, journal, "换镜像源")
    assert ok
    # 第二次改道(最后一次)
    budget, journal, ok, r = consume_fallback(budget, journal, "换数据源")
    assert ok
    # 第三次 → 拒绝(V2 防漂移: 第 3 次尝试前停)
    budget, journal, ok, r = consume_fallback(budget, journal, "再换工具")
    assert not ok
    assert "耗尽" in r and "escalated" in r
    assert len(journal) == 2  # 拒绝时 journal 不变


def test_consume_fallback_appends_entry_shape():
    budget = default_budget(max_fallbacks=3)
    _, journal, ok, _ = consume_fallback(
        budget, [], "换镜像", subagent_id=42
    )
    assert ok
    entry = journal[-1]
    assert entry["kind"] == FALLBACK_JOURNAL_KIND
    assert entry["subagent_id"] == 42
    assert entry["detail"] == "换镜像"
    assert "T" in entry["ts"]


def test_consume_fallback_none_journal_start():
    """journal=None(存量行缺省) → 从空台账开始计数。"""
    budget, journal, ok, _ = consume_fallback(default_budget(), None, "x")
    assert ok and len(journal) == 1


def test_make_fallback_entry_kind():
    assert make_fallback_entry("d")["kind"] == "fallback"


# ── approve_fallback ────────────────────────────────────────────────────

def test_approve_fallback_increments_max():
    assert approve_fallback({"max_fallbacks": 2})["max_fallbacks"] == 3


def test_approve_fallback_then_consume_allowed():
    """批准后预算续跑: 耗尽 → approve → 再改道 allowed。"""
    budget = default_budget()
    journal: list = []
    for _ in range(2):
        budget, journal, ok, _ = consume_fallback(budget, journal, "x")
        assert ok
    ok, _ = can_fallback(budget, journal)
    assert not ok
    budget = approve_fallback(budget)
    budget, journal, ok, _ = consume_fallback(budget, journal, "用户批准后改道")
    assert ok and count_fallbacks(journal) == 3


def test_approve_fallback_none_budget():
    assert approve_fallback(None)["max_fallbacks"] == 3  # 默认 2 + 1
