"""0.6.0 F1-9: 监督触发间隔自适应 —— 类型分级初始值 + 运行内自校正(纯函数)。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.4.1
三级自适应的第一级(类型分级初始值)与第二级(运行内自校正); 第三级(跨任务
经验沉淀)由 D-4 接线, 查询 mission_lessons p50 覆盖初始值(样本<3 回退本模块
默认档)。

D 批接线点(D-2 监督轮):
- mission 计划确定后调 initial_interval() 得到监督轮初始定时触发间隔;
- 每轮监督结束调 adjust_interval(): 传入"连续无变化轮数"与"间隙内是否发生
  失败/停滞(incident)"; 返回 (新间隔, 调整原因); reason 非 None 时调用方
  追加 journal 记录(make_journal_entry())。

纯函数无 IO/无模型调用 —— D1 档可独立单测(本模块)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "DEFAULT_INTERVALS",
    "INTERVAL_MIN_SEC",
    "INTERVAL_MAX_SEC",
    "COMPLEXITY_MULTIPLIER",
    "COMPLEXITY_STEP_THRESHOLD",
    "NO_CHANGE_MULTIPLIER",
    "FAIL_MULTIPLIER",
    "NO_CHANGE_STREAK_THRESHOLD",
    "initial_interval",
    "adjust_interval",
    "make_journal_entry",
]

# ── 第一级: 类型分级初始值(§4.4.1 表) ──────────────────────────────────
# executor_type=script 纯计算无 LLM 循环进展快; subagent 按 task_type 分档。
DEFAULT_INTERVALS: dict[str, int] = {
    "script": 30,    # 纯计算, 早发现早纠偏
    "search": 90,    # subagent: 单模型调用+检索循环
    "analysis": 120,  # subagent: 计算占比更高
    "code": 180,     # subagent: 工具循环长
    "other": 120,    # subagent: 未知类型按中性档(默认间隔)
}

INTERVAL_MIN_SEC = 30
INTERVAL_MAX_SEC = 600

# 复杂任务(预计步骤 > COMPLEXITY_STEP_THRESHOLD 或显式 complex 标记):
# "无变化"是常态, 频繁监督是浪费 → 初始档 ×COMPLEXITY_MULTIPLIER
COMPLEXITY_MULTIPLIER = 1.5
COMPLEXITY_STEP_THRESHOLD = 10

# ── 第二级: 运行内自校正规则 ───────────────────────────────────────────
# 连续 NO_CHANGE_STREAK_THRESHOLD 次监督"无事件、台账零变化" → 拉长
NO_CHANGE_MULTIPLIER = 1.5
NO_CHANGE_STREAK_THRESHOLD = 2
# 监督间隙内发生 stalled/心跳超时/子任务失败(incident) → 缩短
# (安全优先: incident 同时存在无变化 streak 时, 缩短优先)
FAIL_MULTIPLIER = 0.7


def _clamp(interval: float) -> int:
    return max(INTERVAL_MIN_SEC, min(INTERVAL_MAX_SEC, int(round(interval))))


def initial_interval(
    executor_type: str,
    task_type: str = "other",
    *,
    complex_task: bool = False,
    estimated_steps: int = 0,
) -> int:
    """mission 计划确定时计算监督轮初始定时触发间隔(秒)。

    Args:
        executor_type: 执行体类型 subagent/script/wait。
        task_type: 任务类型 search/analysis/code/other(仅 subagent 分档使用)。
        complex_task: 复杂任务显式标记。
        estimated_steps: milestone 预计步骤数(> COMPLEXITY_STEP_THRESHOLD 视为复杂)。

    Returns:
        初始间隔秒数。executor_type='wait' 返回 0(到点触发, 无监督意义)。
    """
    if executor_type == "wait":
        return 0
    if executor_type == "script":
        base = DEFAULT_INTERVALS["script"]
    else:
        # subagent: 按 task_type 分档; 未知 task_type 回退中性档 other
        base = DEFAULT_INTERVALS.get(task_type, DEFAULT_INTERVALS["other"])
    is_complex = complex_task or estimated_steps > COMPLEXITY_STEP_THRESHOLD
    if is_complex:
        return _clamp(base * COMPLEXITY_MULTIPLIER)
    return _clamp(float(base))


def adjust_interval(
    current: int,
    *,
    no_change_streak: int,
    incident: bool,
) -> tuple[int, str | None]:
    """每轮监督结束后自校正间隔(§4.4.1 第二级, 纯规则零模型调用)。

    规则(优先级: incident 缩短 > 无变化拉长 > 不变):
    - incident=True(间隙内有 stalled/心跳超时/子任务失败): 间隔 ×FAIL_MULTIPLIER,
      下限 INTERVAL_MIN_SEC —— 安全优先, 出事即加密观察;
    - incident=False 且 no_change_streak >= NO_CHANGE_STREAK_THRESHOLD
      (连续 2 次无事件台账零变化): 间隔 ×NO_CHANGE_MULTIPLIER, 上限 INTERVAL_MAX_SEC;
    - 其余不变。

    Args:
        current: 当前间隔秒。
        no_change_streak: 连续"无变化"监督轮数(由调用方维护; incident 时应重置)。
        incident: 本监督间隙内是否发生失败/停滞。

    Returns:
        (新间隔秒, 调整原因)。原因 None 表示无调整; 非 None 时调用方应将
        make_journal_entry(...) 追加进 mission.journal(可审计)。
    """
    if incident:
        new = _clamp(current * FAIL_MULTIPLIER)
        if new < current:
            return new, "interval_shrunk_on_incident"
        return current, None  # 已在下限, 不再缩
    if no_change_streak >= NO_CHANGE_STREAK_THRESHOLD:
        new = _clamp(current * NO_CHANGE_MULTIPLIER)
        if new > current:
            return new, "interval_extended_on_no_change"
        return current, None  # 已在上限, 不再涨
    return current, None


def make_journal_entry(
    from_sec: int,
    to_sec: int,
    reason: str,
) -> dict[str, Any]:
    """构造 interval_adjusted journal 记录(调用方 append 进 mission.journal)。"""
    return {
        "kind": "interval_adjusted",
        "ts": datetime.now(timezone.utc).isoformat(),
        "from_sec": from_sec,
        "to_sec": to_sec,
        "reason": reason,
    }
