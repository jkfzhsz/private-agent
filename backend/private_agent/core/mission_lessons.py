"""0.6.0 D-4: 跨任务经验沉淀 —— mission_lessons 结构化统计 + skill_lessons 文本教训。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.4.1 第三级
- **数值经验(结构化)**: mission 终态(done/failed)统计各子任务实际时长 → 按
  (executor_type, task_type) 聚合 upsert 到 mission_lessons(p50/p75 增量加权
  合并 + sample_count); mission 计划确定时查 p50 作为监督间隔初始值,
  **样本 <3 回退默认档**(小样本不信任); 30 天时间窗由统计查询承担(过期自然淘汰)。
- **文本教训**: mission 终态提取一条失败/成功模式 → skill_lessons
  (EvolutionRepo, scope=monitor / lesson_category=project_evolution, 约束已支持)。
  提取调用异常时静默跳过(不阻塞终态落库)。

聚合键: plan 中 executor_type 众数 × subagents.task_type 众数(mission 无
task_type 列, 由 plan/子代理统计派生 —— 与 §4.4.1 聚合口径一致)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from private_agent.observability.logging import setup_logger

__all__ = [
    "MISSION_LESSONS_MIN_SAMPLES",
    "record_mission_outcome",
    "get_initial_interval",
    "derive_aggregation_key",
    "extract_and_save_lesson",
]

logger = setup_logger("private_agent.mission_lessons")

MISSION_LESSONS_MIN_SAMPLES = 3  # §4.4.1: 样本 <3 回退默认档
_INTERVAL_CLAMP = (30.0, 600.0)  # 与 supervision_interval 上下限一致


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_aggregation_key(plan: list, sub_types: list[str]) -> tuple[str, str]:
    """聚合键派生: plan 中 executor_type 众数 × subagents.task_type 众数。

    空计划/空子类型回退 ('subagent', 'other')。
    """
    from collections import Counter

    ets = [
        str(ms.get("executor_type"))
        for ms in plan if isinstance(ms, dict) and ms.get("executor_type")
    ]
    executor_type = Counter(ets).most_common(1)[0][0] if ets else "subagent"
    valid = {"search", "analysis", "code", "other"}
    tt = Counter(t for t in sub_types if t in valid)
    task_type = tt.most_common(1)[0][0] if tt else "other"
    return executor_type, task_type


async def record_mission_outcome(conn, mission_id: int) -> dict | None:
    """mission 终态(done/failed)统计 → mission_lessons 增量 upsert。

    统计口径: subagents(parent_task LIKE 'mission-{id}-%') 的
    (finished_at - started_at) 秒时长样本。
    Returns:
        {"executor_type", "task_type", "observed": [sec...], "p50", "sample_count"}
        无样本(纯 wait/script 无时长/重启) → None。
    """
    row = await conn.fetchrow("SELECT plan FROM missions WHERE id=$1", mission_id)
    if row is None:
        return None
    plan = row["plan"] or []
    if isinstance(plan, str):
        plan = json.loads(plan)

    sub_rows = await conn.fetch(
        """
        SELECT task_type,
               EXTRACT(EPOCH FROM (finished_at - started_at)) AS dur_sec
        FROM subagents
        WHERE session_id = (SELECT session_id FROM missions WHERE id=$1)
          AND parent_task LIKE $2
          AND started_at IS NOT NULL AND finished_at IS NOT NULL
          AND status IN ('succeeded', 'failed')
        """,
        mission_id,
        f"mission-{mission_id}-%",
    )
    durations = [float(r["dur_sec"]) for r in sub_rows if r["dur_sec"] is not None]
    if not durations:
        return None
    durations.sort()

    def _pct(vals: list[float], q: float) -> float:
        idx = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
        return vals[idx]

    sub_types = [str(r["task_type"]) for r in sub_rows]
    executor_type, task_type = derive_aggregation_key(plan, sub_types)
    obs_p50 = _pct(durations, 0.5)
    obs_p75 = _pct(durations, 0.75)
    n_new = len(durations)

    existing = await conn.fetchrow(
        "SELECT sample_count, p50_duration_sec, p75_duration_sec "
        "FROM mission_lessons WHERE executor_type=$1 AND task_type=$2",
        executor_type, task_type,
    )
    if existing is None:
        new_p50, new_p75, new_count = obs_p50, obs_p75, n_new
        await conn.execute(
            """
            INSERT INTO mission_lessons
                (executor_type, task_type, sample_count,
                 p50_duration_sec, p75_duration_sec, last_interval_sec,
                 updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            """,
            executor_type, task_type, new_count, new_p50, new_p75,
            max(_INTERVAL_CLAMP[0], min(_INTERVAL_CLAMP[1], new_p50)),
        )
    else:
        # 增量加权合并(不做全量重算 —— 不存原始样本, 成本 O(1))
        n_old = int(existing["sample_count"] or 0)
        old_p50 = float(existing["p50_duration_sec"] or obs_p50)
        old_p75 = float(existing["p75_duration_sec"] or obs_p75)
        new_count = n_old + n_new
        new_p50 = (old_p50 * n_old + obs_p50 * n_new) / max(1, new_count)
        new_p75 = (old_p75 * n_old + obs_p75 * n_new) / max(1, new_count)
        await conn.execute(
            """
            UPDATE mission_lessons SET sample_count=$3,
                p50_duration_sec=$4, p75_duration_sec=$5,
                last_interval_sec=$6, updated_at=now()
            WHERE executor_type=$1 AND task_type=$2
            """,
            executor_type, task_type, new_count, new_p50, new_p75,
            max(_INTERVAL_CLAMP[0], min(_INTERVAL_CLAMP[1], new_p50)),
        )
    logger.info(
        "mission lessons recorded: %s/%s observed=%d p50=%.1fs",
        executor_type, task_type, n_new, obs_p50,
    )
    return {
        "executor_type": executor_type,
        "task_type": task_type,
        "observed": durations,
        "observed_p50": obs_p50,   # 本次观测 p50
        "merged_p50": new_p50,     # 增量合并后 p50(落库值)
        "sample_count": new_count,
    }


async def get_initial_interval(
    conn,
    executor_type: str,
    task_type: str = "other",
    fallback_sec: float = 120.0,
) -> float:
    """监督间隔初始值(§4.4.1 第三级消费点): 同类型 p50 → clamp; 样本 <3 或无
    记录 → 回退默认档(调用方传 initial_interval() 的分级值)。

    30 天时间窗: 仅统计 updated_at 近 30 天的记录(过期经验自然淘汰)。
    """
    row = await conn.fetchrow(
        """
        SELECT p50_duration_sec, sample_count FROM mission_lessons
        WHERE executor_type=$1 AND task_type=$2
          AND updated_at > now() - interval '30 days'
        """,
        executor_type, task_type,
    )
    if (
        row is None
        or int(row["sample_count"] or 0) < MISSION_LESSONS_MIN_SAMPLES
        or row["p50_duration_sec"] is None
    ):
        return fallback_sec
    return max(_INTERVAL_CLAMP[0], min(_INTERVAL_CLAMP[1], float(row["p50_duration_sec"])))


async def extract_and_save_lesson(
    conn,
    adapter: Any,
    mission_id: int,
    charter: dict,
    journal: list,
    state: str,
) -> int | None:
    """mission 终态提取一条文本教训 → skill_lessons(scope=monitor/
    project_evolution, 约束已支持)。单次模型调用; 异常静默跳过(不阻塞终态)。

    Returns:
        skill_lessons.id 或 None(未提取/失败)。
    """
    if state not in ("done", "failed", "escalated"):
        return None
    lesson_type = "success" if state == "done" else "failure"
    summary_lines = [f"任务目标: {str(charter.get('goal', '-'))[:150]}"]
    for e in journal[-8:]:
        if isinstance(e, dict):
            summary_lines.append(f"[{e.get('kind')}] {str(e.get('detail', ''))[:100]}")
    prompt = (
        "从以下长任务执行台账中提取一条可复用经验(一句话, ≤120字, "
        "聚焦监督节奏/委派方式/环境约束的教训)。只输出经验文本本身。\n"
        + "\n".join(summary_lines)
    )
    content = ""
    try:
        result = await adapter.chat(
            [{"role": "user", "content": prompt}], max_tokens=200
        )
        content = (getattr(result, "content", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("lesson extraction failed: %s", e)
        return None
    if not content:
        return None
    try:
        from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson

        repo = EvolutionRepo(conn)
        lesson_id = await repo.add(SkillLesson(
            scope="monitor",
            lesson_category="project_evolution",
            task_summary=f"Mission #{mission_id} ({state})",
            lesson_type=lesson_type,
            lesson_content=content[:500],
            tool_chain=[],
        ))
        return lesson_id
    except Exception as e:  # noqa: BLE001
        logger.warning("lesson save failed: %s", e)
        return None
