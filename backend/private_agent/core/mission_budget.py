"""0.6.0 F1-7: 改道预算逻辑层 BudgetLedger(纯函数, 防漂移第二道防线)。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.3
- 改道 = 子任务失败换替代方案(换镜像/换数据源/换工具), 计一次 fallback;
- 超过 budget.max_fallbacks → mission 置 escalated, 停止一切尝试等用户裁决;
- 用户批准(mission_control.approve_fallback) → max_fallbacks +1 续跑。

数据面落点(F1-1/F1-2 已定):
- budget.max_fallbacks: 预算上限(missions.budget JSONB);
- journal 条目 kind='fallback': 每次改道追加(missions.journal JSONB)。

使用方:
- D-3 纠偏动作执行器: redelegate 前调 can_fallback/consume_fallback 判定;
- F1-2 mission_status: count_fallbacks 统计预算消耗;
- mission_control.approve_fallback: approve_fallback() 持久化落点语义。

纯函数无 IO —— D2 档可独立单测(本模块)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "FALLBACK_JOURNAL_KIND",
    "default_budget",
    "count_fallbacks",
    "budget_status",
    "can_fallback",
    "consume_fallback",
    "approve_fallback",
    "make_fallback_entry",
]

FALLBACK_JOURNAL_KIND = "fallback"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_budget(max_fallbacks: int = 2, max_total_sec: int = 3600) -> dict:
    """默认预算(与 mission_cfg 默认一致; F1-2 mission_create 未显式传 budget 时)。"""
    return {"max_fallbacks": int(max_fallbacks), "max_total_sec": int(max_total_sec)}


def count_fallbacks(journal: list[dict] | str | None) -> int:
    """统计 journal 中改道次数(kind='fallback' 条目数; 容忍 JSONB str)。"""
    if journal is None:
        return 0
    if isinstance(journal, str):
        import json

        try:
            journal = json.loads(journal)
        except (ValueError, TypeError):
            return 0
    if not isinstance(journal, list):
        return 0
    return sum(
        1
        for e in journal
        if isinstance(e, dict) and e.get("kind") == FALLBACK_JOURNAL_KIND
    )


def budget_status(budget: dict | None, journal: list[dict] | str | None) -> dict:
    """预算消耗快照: {used, max, remaining, exhausted}(mission_status/监督轮共用)。"""
    b = budget or {}
    used = count_fallbacks(journal)
    mx = int(b.get("max_fallbacks", 2))
    return {
        "used": used,
        "max": mx,
        "remaining": max(0, mx - used),
        "exhausted": used >= mx,
    }


def can_fallback(budget: dict | None, journal: list[dict] | str | None) -> tuple[bool, str]:
    """改道前判定: (allowed, reason)。

    reason 面向模型可理解(与 delegate 类型限流拒绝语义一致: 一次改道即成功)。
    """
    st = budget_status(budget, journal)
    if st["exhausted"]:
        return False, (
            f"改道预算已耗尽({st['used']}/{st['max']})。"
            "请停止尝试并将 mission 置 escalated 等待用户裁决"
            "(mission_control.approve_fallback 可追加预算)。"
        )
    return True, f"改道 {st['used'] + 1}/{st['max']}"


def make_fallback_entry(detail: str, subagent_id: int | None = None) -> dict[str, Any]:
    """构造 kind='fallback' journal 条目(调用方 append 进 missions.journal)。"""
    entry: dict[str, Any] = {
        "kind": FALLBACK_JOURNAL_KIND,
        "ts": _now_iso(),
        "detail": detail,
    }
    if subagent_id is not None:
        entry["subagent_id"] = subagent_id
    return entry


def consume_fallback(
    budget: dict | None,
    journal: list[dict] | None,
    detail: str,
    subagent_id: int | None = None,
) -> tuple[dict, list[dict], bool, str]:
    """消费一次改道: 判定 → journal 追加(纯数据, 持久化由调用方写回 DB)。

    Returns:
        (budget, new_journal, allowed, reason)
        allowed=False 时 journal 不变(调用方应转 escalate 流程)。
    """
    b = budget or default_budget()
    j = journal if isinstance(journal, list) else []
    allowed, reason = can_fallback(b, j)
    if not allowed:
        return b, j, False, reason
    return b, j + [make_fallback_entry(detail, subagent_id)], True, reason


def approve_fallback(budget: dict | None) -> dict:
    """用户批准改道: max_fallbacks +1(mission_control.approve_fallback 语义)。"""
    b = budget or default_budget()
    return {**b, "max_fallbacks": int(b.get("max_fallbacks", 2)) + 1}
