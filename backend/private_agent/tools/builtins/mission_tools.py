"""0.6.0 Mission 层工具集(F1-2) —— mission_create / mission_status / mission_control。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.1
- mission_create: 提交宪章+计划 → 建 missions 行 → 立即返回 mission_id(非阻塞)。
  **spawn 接线点(TODO D-1)**: main.py 构建工具时注入 runner_factory;
  F1 批传 None(工具可用但不自动执行), D-1 实现 MissionRunner 后接线激活。
- mission_status: 只读, 用户问进度时无涯调用(结构化状态, 仅转述不自行改任务)。
- mission_control: 用户裁决通道(approve_fallback / abort / pause);
  pause 仅置数据面 state, 执行面响应由 D-1 MissionRunner 实现。

装配约定(F1 批零 main.py 改动): 本模块交付工具定义 + 单测; main.py 装配与
EnvironmentProfile 注入、MissionRunner spawn 一起在 D 批激活。
F1-1 迁移事故防御: asyncpg execute() 返回 "UPDATE N" 状态串, 恒等比较不可靠
→ 统一 _rowcount() 解析(项目教训 2026-08-13)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["build_mission_tools", "mission_cfg", "MISSION_EXECUTOR_TYPES"]

MISSION_EXECUTOR_TYPES = ("subagent", "script", "wait")

MISSION_TOOL_NAMES = ("mission_create", "mission_status", "mission_control")


def mission_cfg(cfg: dict | None) -> dict:
    """读取 tools.mission 配置段(带默认值; §八裁决 3: max_running 默认 2)。"""
    m = (cfg or {}).get("tools", {}).get("mission", {}) or {}
    return {
        "max_running": int(m.get("max_running", 2)),
        "max_fallbacks": int(m.get("max_fallbacks", 2)),
        "max_total_sec": int(m.get("max_total_sec", 3600)),
    }


def _rowcount(status: str) -> int:
    """asyncpg execute() 返回 'UPDATE N' 状态串 → 解析受影响行数(教训防御)。"""
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal_entry(kind: str, detail: str) -> dict:
    return {"kind": kind, "ts": _now_iso(), "detail": detail}


def _validate_charter(charter: Any) -> str | None:
    """校验 charter; 返回错误信息或 None。"""
    if not isinstance(charter, dict):
        return "charter 必须为对象"
    goal = str(charter.get("goal") or "").strip()
    if not goal:
        return "charter.goal 必填(非空字符串)"
    for opt_key in ("dod", "constraints"):
        val = charter.get(opt_key)
        if val is not None and not isinstance(val, list):
            return f"charter.{opt_key} 必须为数组"
    env = charter.get("env_snapshot")
    if env is not None and not isinstance(env, dict):
        return "charter.env_snapshot 必须为对象"
    return None


def _validate_plan(plan: Any) -> str | None:
    """校验 plan(milestones); 返回错误信息或 None。"""
    if not isinstance(plan, list) or not plan:
        return "plan 必须为非空数组(milestones)"
    seen_ids: set[str] = set()
    for i, ms in enumerate(plan):
        if not isinstance(ms, dict):
            return f"plan[{i}] 必须为对象"
        ms_id = ms.get("id")
        if not ms_id or not isinstance(ms_id, str):
            return f"plan[{i}].id 必填(非空字符串)"
        if ms_id in seen_ids:
            return f"plan[{i}].id 重复: {ms_id}"
        seen_ids.add(ms_id)
        if not str(ms.get("milestone") or "").strip():
            return f"plan[{i}].milestone 必填(里程碑描述)"
        et = ms.get("executor_type")
        if et not in MISSION_EXECUTOR_TYPES:
            return (
                f"plan[{i}].executor_type 必须为 "
                f"{'/'.join(MISSION_EXECUTOR_TYPES)} 之一, 收到 {et!r}"
            )
    return None


def build_mission_tools(
    *,
    conn,
    cfg: dict,
    session_id: int,
    runner_factory: Callable[[int], Awaitable[None]] | None = None,
) -> list[ToolDef]:
    """构建 mission 工具集(闭包注入模式, 同 delegate_subtask —— 多会话无串扰)。

    Args:
        conn: 会话 DB 连接(工具内复用; 子代理/runner 自开独立连接)。
        cfg: 合并后的配置 dict。
        session_id: 父会话 id(mission 归属会话)。
        runner_factory: async (mission_id) -> None —— **D-1 接线点**:
            MissionRunner.spawn(mission_id); F1 批 main.py 传 None。
    """
    mc = mission_cfg(cfg)

    # ── mission_create ────────────────────────────────────────────────
    async def _create_handler(args: dict) -> ToolResult:
        charter = args.get("charter")
        err = _validate_charter(charter)
        if err:
            return ToolResult(output="", error=f"mission_create: {err}")
        plan = args.get("plan")
        err = _validate_plan(plan)
        if err:
            return ToolResult(output="", error=f"mission_create: {err}")
        budget_in = args.get("budget") or {}
        if not isinstance(budget_in, dict):
            return ToolResult(output="", error="mission_create: budget 必须为对象")
        budget = {
            "max_fallbacks": int(budget_in.get("max_fallbacks", mc["max_fallbacks"])),
            "max_total_sec": int(budget_in.get("max_total_sec", mc["max_total_sec"])),
        }
        # 并发上限(§八裁决 3: 默认 2): DB 级检查(单实例下与进程级注册表等价;
        # F1-6 注册表做运行时 acquire/release, 此处为入口防线)
        running = await conn.fetchval(
            "SELECT count(*) FROM missions "
            "WHERE state IN ('planning','executing','supervising','paused')"
        )
        if int(running or 0) >= mc["max_running"]:
            return ToolResult(
                output="",
                error=(
                    f"mission_create: 运行中 mission 已达上限 {mc['max_running']}"
                    f"(当前 {running})。请等待现有 mission 完成后再创建。"
                ),
            )
        # 计划默认预算: 每个里程碑标记复杂度(供 D-2 间隔分级, §4.4.1)
        entry = _journal_entry("created", f"goal={charter['goal'][:80]}")
        mission_id = await conn.fetchval(
            """
            INSERT INTO missions (session_id, charter, plan, budget, state, journal)
            VALUES ($1, $2, $3, $4, 'planning', $5)
            RETURNING id
            """,
            session_id,
            json.dumps(charter, ensure_ascii=False),
            json.dumps(plan, ensure_ascii=False),
            json.dumps(budget, ensure_ascii=False),
            json.dumps([entry], ensure_ascii=False),
        )
        mid = int(mission_id)
        # ── TODO(D-1 接线点): MissionRunner.spawn ─────────────────────
        # D 批在 main.py 构建工具时注入 runner_factory=
        #   lambda mid: MissionRunner.spawn(conn_pool, cfg, mid, ...)
        # F1 批 runner_factory=None → mission 停留在 planning(不自动执行)。
        if runner_factory is not None:
            try:
                await runner_factory(mid)
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    output=(
                        f"Mission #{mid} 已创建(state=planning), "
                        f"但后台编排器启动失败: {e}"
                    ),
                    metadata={"mission_id": mid},
                )
        return ToolResult(
            output=(
                f"Mission #{mid} 已创建(state=planning)。"
                f"目标: {charter['goal'][:60]} | 里程碑 {len(plan)} 个 | "
                f"改道预算 {budget['max_fallbacks']}。"
                + (
                    "后台编排已启动。"
                    if runner_factory is not None
                    else "(执行编排待 MissionRunner 装配后激活)"
                )
            ),
            metadata={"mission_id": mid},
        )

    # ── mission_status(只读) ─────────────────────────────────────────
    async def _status_handler(args: dict) -> ToolResult:
        mission_id = args.get("mission_id")
        if mission_id is None:
            return ToolResult(output="", error="mission_status: mission_id 必填")
        row = await conn.fetchrow(
            "SELECT id, charter, plan, budget, journal, state, error, "
            "created_at, completed_at FROM missions WHERE id=$1",
            int(mission_id),
        )
        if row is None:
            return ToolResult(output="", error=f"mission_status: #{mission_id} 不存在")
        plan = row["plan"] or []
        if isinstance(plan, str):
            plan = json.loads(plan)
        journal = row["journal"] or []
        if isinstance(journal, str):
            journal = json.loads(journal)
        budget = row["budget"] or {}
        if isinstance(budget, str):
            budget = json.loads(budget)
        charter = row["charter"] or {}
        if isinstance(charter, str):
            charter = json.loads(charter)
        # F1-7: 预算统计统一走 BudgetLedger(与 D-3 纠偏判定同一实现)
        from private_agent.core.mission_budget import budget_status

        bstat = budget_status(budget, journal)
        fallbacks_used, max_fb = bstat["used"], bstat["max"]
        lines = [
            f"Mission #{row['id']} state={row['state']}",
            f"目标: {charter.get('goal', '-')}",
            f"预算: 改道已用 {fallbacks_used}/{max_fb}"
            f", 总时长上限 {budget.get('max_total_sec', '-')}s",
            "里程碑:",
        ]
        for ms in plan:
            lines.append(f"  - [{ms.get('id')}] {ms.get('milestone', '-')}"
                         f" ({ms.get('executor_type')}, status={ms.get('status', 'pending')})")
        lines.append(f"台账尾部(共 {len(journal)} 条):")
        for e in journal[-5:]:
            detail = str(e.get("detail", ""))[:80]
            lines.append(f"  - [{e.get('kind')}] {e.get('ts', '')} {detail}")
        if row["error"]:
            lines.append(f"错误: {row['error']}")
        return ToolResult(output="\n".join(lines))

    # ── mission_control(用户裁决通道) ────────────────────────────────
    async def _control_handler(args: dict) -> ToolResult:
        mission_id = args.get("mission_id")
        action = args.get("action")
        if mission_id is None or not action:
            return ToolResult(
                output="",
                error="mission_control: mission_id 与 action 必填"
                      "(approve_fallback/abort/pause)",
            )
        mid = int(mission_id)
        if action == "approve_fallback":
            # 改道预算 +1(F1-7 BudgetLedger 的持久化落点; journal 留痕)
            row = await conn.fetchrow(
                "SELECT budget, state FROM missions WHERE id=$1", mid
            )
            if row is None:
                return ToolResult(output="", error=f"mission_control: #{mid} 不存在")
            budget = row["budget"] or {}
            if isinstance(budget, str):
                budget = json.loads(budget)
            budget["max_fallbacks"] = int(budget.get("max_fallbacks", mc["max_fallbacks"])) + 1
            journal_entry = _journal_entry(
                "fallback_approved", f"改道预算增至 {budget['max_fallbacks']}"
            )
            st = await conn.execute(
                """
                UPDATE missions SET budget=$2, state='executing',
                    journal = journal || $3::jsonb, updated_at=now()
                WHERE id=$1 AND state='escalated'
                """,
                mid, json.dumps(budget, ensure_ascii=False),
                json.dumps([journal_entry], ensure_ascii=False),
            )
            if _rowcount(st) == 0:
                return ToolResult(
                    output="",
                    error=(
                        f"mission_control: #{mid} 状态非 escalated,"
                        "无需批准改道(仅 escalated 状态可追加预算)"
                    ),
                )
            return ToolResult(
                output=f"Mission #{mid} 改道预算已增至 {budget['max_fallbacks']},"
                       "状态 escalated→executing, 继续执行。"
            )
        if action == "abort":
            journal_entry = _journal_entry("aborted", "用户裁决中止")
            st = await conn.execute(
                """
                UPDATE missions SET state='cancelled',
                    journal = journal || $2::jsonb, completed_at=now(), updated_at=now()
                WHERE id=$1 AND state IN
                    ('planning','executing','supervising','paused','escalated')
                """,
                mid, json.dumps([journal_entry], ensure_ascii=False),
            )
            if _rowcount(st) == 0:
                return ToolResult(
                    output="",
                    error=f"mission_control: #{mid} 已是终态或不存在, 无法中止",
                )
            return ToolResult(output=f"Mission #{mid} 已中止(state=cancelled)。")
        if action == "pause":
            # 数据面置 state=paused; 执行面响应由 D-1 MissionRunner 实现
            journal_entry = _journal_entry("paused", "用户暂停")
            st = await conn.execute(
                """
                UPDATE missions SET state='paused',
                    journal = journal || $2::jsonb, updated_at=now()
                WHERE id=$1 AND state IN ('executing','supervising')
                """,
                mid, json.dumps([journal_entry], ensure_ascii=False),
            )
            if _rowcount(st) == 0:
                return ToolResult(
                    output="",
                    error=(
                        f"mission_control: #{mid} 非运行状态, 无法暂停"
                        "(仅 executing/supervising 可暂停)"
                    ),
                )
            return ToolResult(
                output=f"Mission #{mid} 已暂停(state=paused)。"
                       "恢复待 MissionRunner 装配后通过 resume 动作或重新裁决。"
            )
        return ToolResult(
            output="",
            error=f"mission_control: 未知 action={action!r}"
                  "(支持 approve_fallback/abort/pause)",
        )

    return [
        ToolDef(
            name="mission_create",
            description=(
                "创建长任务(mission): 提交任务宪章(charter: 目标/完成标准/约束)"
                "与里程碑计划(plan), 后台编排执行, 立即返回不阻塞对话。"
                "适合分钟~小时级的多阶段任务; 短任务请直接使用普通工具。"
                "创建前必须明确: 目标一句话可验证、每个里程碑有 executor_type"
                "(subagent=多步推理/script=纯计算脚本/wait=定时等待)。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "charter": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string", "description": "任务目标(一句话可验证)"},
                            "dod": {
                                "type": "array",
                                "description": "完成标准(Definition of Done)清单",
                            },
                            "constraints": {
                                "type": "array",
                                "description": "硬约束(如禁换数据源/禁装依赖)",
                            },
                        },
                        "required": ["goal"],
                    },
                    "plan": {
                        "type": "array",
                        "description": "里程碑数组, 每项 {id, milestone, executor_type}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "milestone": {"type": "string"},
                                "executor_type": {
                                    "type": "string",
                                    "enum": list(MISSION_EXECUTOR_TYPES),
                                },
                                "estimated_steps": {
                                    "type": "integer",
                                    "description": "预计步骤数(>10 视为复杂任务)",
                                },
                            },
                            "required": ["id", "milestone", "executor_type"],
                        },
                    },
                    "budget": {
                        "type": "object",
                        "properties": {
                            "max_fallbacks": {
                                "type": "integer",
                                "description": "改道预算(默认 2, 超限升级用户裁决)",
                            },
                            "max_total_sec": {
                                "type": "integer",
                                "description": "总时长上限秒(默认 3600)",
                            },
                        },
                    },
                },
                "required": ["charter", "plan"],
            },
            handler=_create_handler,
            safety_level="none",
            risk_level="medium",
            is_kernel=True,  # 始终注入: 长任务编排是主模型关键能力(同 delegate)
        ),
        ToolDef(
            name="mission_status",
            description=(
                "查询 mission 长任务状态(只读): 状态/里程碑进度/台账尾部/预算消耗。"
                "用户询问任务进度时调用; **仅作状态转述与解释, 不得基于查询结果"
                "自行改道或调整任务**(改道只走监督轮/用户裁决通道)。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "integer", "description": "mission id"},
                },
                "required": ["mission_id"],
            },
            handler=_status_handler,
            safety_level="none",
            is_kernel=True,
        ),
        ToolDef(
            name="mission_control",
            description=(
                "mission 用户裁决通道: approve_fallback=批准改道(追加预算, 仅 "
                "escalated 状态)/abort=中止/pause=暂停。仅在用户明确表达相应"
                "意图时调用, 不得自主发起。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "mission_id": {"type": "integer", "description": "mission id"},
                    "action": {
                        "type": "string",
                        "enum": ["approve_fallback", "abort", "pause"],
                        "description": "裁决动作",
                    },
                    "note": {"type": "string", "description": "备注(可选)"},
                },
                "required": ["mission_id", "action"],
            },
            handler=_control_handler,
            safety_level="none",
            is_kernel=True,
        ),
    ]
