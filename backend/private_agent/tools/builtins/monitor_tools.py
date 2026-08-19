"""0.5.0 P1: 主智能体监控工具集(四窗口架构, 仅 monitor 会话白名单装配)。

工具清单(见 docs/next-phase-plan-2026-08-08-four-windows.md §4.2):
- system_metrics_query: 查询历史指标(范围/聚合) —— 低风险, 安全等级 none
- system_status:       即时采集一次当前系统状态 —— 低风险, 安全等级 none
- optim_plan:          优化建议落库 optim_log(pending) —— 低风险, 安全等级 none
- apply_optim:         执行已批准的优化方案 —— 高风险, 安全等级 elevated
                        (走 WS 60s 权限确认 + 会话缓存)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

# apply_optim 允许的低风险优化动作白名单(安全边界, 见设计文档 §4.4):
# V1 仅允许"上下文压缩参数调整"类可回滚配置修改; 文件操作/高危配置禁止
LOW_RISK_ACTIONS = {"context.compression", "context.memory"}


def _collector() -> Any:
    """延迟获取采集器单例(避免 import 循环; main.py 启动时注入)。"""
    from private_agent.core.metrics_collector import MetricsCollector  # noqa: F401

    # main.py 通过 app.state.metrics_collector 注入实例
    from private_agent.main import app

    return getattr(app.state, "metrics_collector", None)


async def _system_metrics_query_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """查询历史系统指标(主智能体分析用)。

    Args:
        names: 指标名列表(可选, 如 ["cpu_percent", "ram_percent"])。
        since_hours: 最近 N 小时(默认 24)。
        session_id: 会话过滤(可选)。
        limit: 返回条数上限(默认 200)。
    """
    collector = _collector()
    if collector is None:
        return ToolResult(output="", error="metrics collector not initialized")
    conn = None
    try:
        from private_agent.storage import db

        conn = await db.connect()
        rows = await collector.query(
            conn,
            names=args.get("names"),
            since_hours=float(args.get("since_hours", 24)),
            session_id=args.get("session_id"),
            limit=int(args.get("limit", 200)),
        )
        if not rows:
            return ToolResult(output="(查询范围内无指标数据)")
        # 摘要输出(避免全文刷屏): 按 name 聚合展示最近值
        latest: dict[str, dict] = {}
        for r in rows:
            if r["name"] not in latest:
                latest[r["name"]] = r
        lines = [f"共 {len(rows)} 条, 展示各指标最近值:"]
        for name, r in latest.items():
            sid = f" session={r['session_id']}" if r["session_id"] else ""
            lines.append(
                f"- {name}: {r['value']:.2f} (at {r['ts'].isoformat()}){sid}"
            )
        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"system_metrics_query failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


async def _system_status_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """即时采集一次系统状态(不依赖最近指标快照)。"""
    collector = _collector()
    if collector is None:
        return ToolResult(output="", error="metrics collector not initialized")
    conn = None
    try:
        from private_agent.storage import db

        conn = await db.connect()
        metrics = await collector.collect_once(conn)
        if not metrics:
            return ToolResult(output="(采集无数据)")
        lines = [
            f"system_status @ {datetime.now(timezone.utc).isoformat()}",
        ]
        for name, value in sorted(metrics.items()):
            lines.append(f"- {name}: {value:.2f}")
        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"system_status failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


async def _optim_plan_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """将优化建议落库 optim_log(状态=pending, 供用户审批)。

    Args:
        proposal: 优化建议文本(必填)。
        category: 类别(context/tool/model/memory/performance, 可选)。
        plan: 结构化执行步骤 JSON 数组(可选, 如
              [{"tool": "code_execution", "args": {...}}, ...])。
    """
    proposal = str(args.get("proposal") or "").strip()
    if not proposal:
        return ToolResult(output="", error="proposal required")
    category = str(args.get("category") or "performance")
    plan = args.get("plan")
    if plan is None:
        # 默认建议用户审批后由 apply_optim 生成 plan
        plan = []
    conn = None
    try:
        from private_agent.storage import db

        conn = await db.connect()
        session_id = None
        if ctx is not None:
            session_id = getattr(ctx, "session_id", None)
        row = await conn.fetchrow(
            """
            INSERT INTO optim_log (proposal, category, plan_json, session_id)
            VALUES ($1, $2, $3, $4)
            RETURNING id, status
            """,
            proposal,
            category,
            json.dumps(plan, ensure_ascii=False),
            session_id,
        )
        return ToolResult(
            output=(
                f"优化建议已提交 #id={row['id']} 状态={row['status']}。\n"
                "请在监控窗口的「优化审批」列表中确认后执行(apply_optim)。"
            )
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"optim_plan failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


async def _apply_optim_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """执行已批准的优化方案(高安全: 仅限 optim_log status=approved)。

    2026-08-16(阶段1-b, agent-upgrader 设计文档 §3): 从"模拟执行"改为
    **真执行** —— approved 后按 plan 逐步执行白名单动作(code_execution /
    file_write / file_read), 打通"无涯提方案 → 用户审批 → 自动执行"闭环。
    plan 内动作不再单独触发权限确认(已由 apply_optim 的确认覆盖)。

    安全边界:
    - 仅 approved 可执行; pending/rejected/applied 拒绝
    - plan 工具白名单: code_execution(跑脚本/测试) + file_write(写文件)
      + file_read(只读); 其他工具跳过并记录
    - 无 plan 时回退低风险 category 路径(context 配置类, 保留旧行为)

    Args:
        optim_id: optim_log id(必填)。
    """
    optim_id = args.get("optim_id")
    if optim_id is None:
        return ToolResult(output="", error="optim_id required")
    conn = None
    try:
        from private_agent.storage import db

        conn = await db.connect()
        row = await conn.fetchrow(
            "SELECT id, proposal, category, status, plan_json FROM optim_log "
            "WHERE id = $1",
            int(optim_id),
        )
        if row is None:
            return ToolResult(output="", error=f"optim_log #{optim_id} 不存在")
        if row["status"] != "approved":
            return ToolResult(
                output="",
                error=(
                    f"optim_log #{optim_id} 状态为 {row['status']}, "
                    "仅 approved 可执行(请先在审批列表批准)"
                ),
            )
        plan = row["plan_json"] or []
        # asyncpg JSONB 返回 str(2026-08-15 教训): 需解析为 list
        if isinstance(plan, str):
            try:
                plan = json.loads(plan)
            except Exception:  # noqa: BLE001
                plan = []
        if not isinstance(plan, list):
            plan = []
        category = row["category"] or ""

        # ── 真执行: 按 plan 逐步执行白名单动作 ──────────────────────────
        if plan:
            from private_agent.tools.builtins.code_execution import (
                code_execution_handler,
            )
            from private_agent.tools.builtins.file_write import (
                file_write_handler,
            )

            allowed = {"code_execution", "file_write", "file_read"}
            session_id = None
            if ctx is not None:
                session_id = getattr(ctx, "session_id", None)
            results: list[str] = []
            executed = 0
            skipped: list[str] = []
            for i, step in enumerate(plan, start=1):
                if not isinstance(step, dict):
                    skipped.append(f"step{i}: 非法步骤(非 dict)")
                    continue
                tool_name = str(step.get("tool", ""))
                step_args = dict(step.get("args") or {})
                if tool_name not in allowed:
                    skipped.append(f"step{i}[{tool_name}]: 不在白名单")
                    continue
                try:
                    if tool_name == "code_execution":
                        if session_id is not None:
                            step_args.setdefault("session_id", str(session_id))
                        tr = await code_execution_handler(step_args)
                    elif tool_name == "file_write":
                        tr = await file_write_handler(step_args)
                    else:  # file_read 只读
                        from private_agent.tools.builtins.file_read import (
                            file_read_handler,
                        )

                        tr = await file_read_handler(step_args)
                except Exception as e:  # noqa: BLE001
                    results.append(
                        f"step{i}[{tool_name}] 执行异常: {type(e).__name__}: {e}"
                    )
                    continue
                if tr.error:
                    results.append(f"step{i}[{tool_name}] 失败: {tr.error}")
                else:
                    executed += 1
                    output_snippet = str(tr.output or "")[:200].replace("\n", " ")
                    results.append(f"step{i}[{tool_name}] OK: {output_snippet}")
            skip_note = f"; 跳过 {len(skipped)} 步" if skipped else ""
            result = (
                f"已执行优化 #{row['id']} [{category}] {executed} 步{skip_note}\n"
                + "\n".join(results)
            )
            if skipped:
                result += "\n跳过明细: " + "; ".join(skipped[:5])
            await conn.execute(
                "UPDATE optim_log SET status='applied', result=$2, reviewed_at=now() "
                "WHERE id=$1",
                int(optim_id),
                result,
            )
            return ToolResult(output=result)

        # ── 无 plan: 回退低风险 category 路径(保留旧模拟行为) ────────────
        if category not in LOW_RISK_ACTIONS and category not in ("context",):
            return ToolResult(
                output="",
                error=(
                    f"category={category} 无 plan 且不在自动执行白名单 "
                    f"({sorted(LOW_RISK_ACTIONS)})"
                ),
            )
        result = (
            f"已执行优化 #{row['id']} [{category}]: "
            f"{row['proposal'][:80]}...\n"
            f"(无 plan, 仅记录执行摘要)"
        )
        await conn.execute(
            "UPDATE optim_log SET status='applied', result=$2, reviewed_at=now() "
            "WHERE id=$1",
            int(optim_id),
            result,
        )
        return ToolResult(output=result)
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"apply_optim failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


async def _subagent_status_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """查询所有会话最近的子代理委派状态(成功/取消/失败/超时)。

    2026-08-13 修复(问题3 第一环): 全局智能体此前无跨会话子任务查询工具,
    "调查子任务取消原因连事件都找不到"。本工具打通 subagents 表查询。

    Args:
        since_hours: 最近 N 小时(默认 24)。
        status: 可选过滤(cancelled/failed/succeeded/running)。
        limit: 返回条数上限(默认 50)。
    """
    conn = None
    try:
        from collections import Counter

        from private_agent.storage import db

        conn = await db.connect()
        since_hours = float(args.get("since_hours", 24))
        status = args.get("status")
        limit = int(args.get("limit", 50))
        if status:
            rows = await conn.fetch(
                """
                SELECT id, session_id, status, error, tool_calls,
                       created_at, started_at, finished_at
                FROM subagents
                WHERE created_at > now() - make_interval(hours => $1)
                  AND status = $2
                ORDER BY id DESC LIMIT $3
                """,
                since_hours, str(status), limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, session_id, status, error, tool_calls,
                       created_at, started_at, finished_at
                FROM subagents
                WHERE created_at > now() - make_interval(hours => $1)
                ORDER BY id DESC LIMIT $2
                """,
                since_hours, limit,
            )
        if not rows:
            return ToolResult(output=f"(最近 {since_hours}h 无子代理委派记录)")
        cnt = Counter(r["status"] for r in rows)
        dist = ", ".join(f"{k}={v}" for k, v in cnt.items())
        lines = [f"最近 {since_hours}h 共 {len(rows)} 条子代理记录, 状态分布: {dist}"]
        for r in rows:
            lines.append(
                f"- #{r['id']} session={r['session_id']} status={r['status']} "
                f"tool_calls={r['tool_calls']} error={r['error'] or '-'} "
                f"started={r['started_at']} finished={r['finished_at']}"
            )
        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"subagent_status failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


async def _session_events_handler(
    args: dict, ctx: Any | None = None, **kwargs: Any
) -> ToolResult:
    """查询指定会话的 react_events 事件流(跨会话, 全局智能体可查任意会话)。

    2026-08-13 修复(问题3 第一环): 让全局智能体"回放"任意会话的事件,
    定位子任务取消/超时/报错的真实原因。

    Args:
        session_id: 会话 id(必填)。
        event_type: 可选过滤(subagent/error/tool_error/tool_result/final 等)。
        limit: 返回条数上限(默认 100)。
    """
    session_id = args.get("session_id")
    if session_id is None:
        return ToolResult(output="", error="session_id required")
    conn = None
    try:
        from private_agent.storage import db

        conn = await db.connect()
        event_type = args.get("event_type")
        limit = int(args.get("limit", 100))
        if event_type:
            rows = await conn.fetch(
                """
                SELECT id, turn, event_type, payload, created_at
                FROM react_events
                WHERE session_id = $1 AND event_type = $2
                ORDER BY id DESC LIMIT $3
                """,
                int(session_id), str(event_type), limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, turn, event_type, payload, created_at
                FROM react_events
                WHERE session_id = $1
                ORDER BY id DESC LIMIT $2
                """,
                int(session_id), limit,
            )
        if not rows:
            return ToolResult(output=f"(session {session_id} 无匹配事件)")
        lines = [f"session {session_id} 共 {len(rows)} 条事件(倒序):"]
        for r in rows:
            payload = r["payload"]
            if isinstance(payload, str):
                snippet = payload[:140]
            else:
                snippet = str(payload)[:140]
            lines.append(
                f"- [{r['event_type']}] turn={r['turn']} {snippet} "
                f"(at {r['created_at']})"
            )
        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"session_events failed: {e}")
    finally:
        if conn is not None:
            await conn.close()


# 工具注册表(monitor 会话专属白名单, 由 skills/loader 或 main.py 装配)
MONITOR_TOOLS: list[ToolDef] = [
    ToolDef(
        name="system_metrics_query",
        description=(
            "查询系统历史性能指标(CPU/内存/WS连接/会话token/工具失败率)。"
            "用于主智能体分析系统状态, 支持按指标名/时间范围/会话过滤。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "指标名列表(可选, 空=全部)",
                },
                "since_hours": {
                    "type": "number",
                    "description": "最近 N 小时(默认 24)",
                },
                "session_id": {"type": "integer", "description": "会话过滤(可选)"},
                "limit": {"type": "integer", "description": "返回条数上限"},
            },
        },
        handler=_system_metrics_query_handler,
        is_kernel=False,
        safety_level="none",
    ),
    ToolDef(
        name="system_status",
        description="即时采集一次当前系统状态(CPU/内存/连接数等), 不依赖历史快照。",
        parameters_schema={"type": "object", "properties": {}},
        handler=_system_status_handler,
        is_kernel=False,
        safety_level="none",
    ),
    ToolDef(
        name="optim_plan",
        description=(
            "提交系统优化建议到审批列表(optim_log, 状态 pending)。"
            "主智能体分析发现可优化点时调用, 用户审批后执行。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "proposal": {"type": "string", "description": "优化建议文本(必填)"},
                "category": {
                    "type": "string",
                    "description": "类别: context/tool/model/memory/performance",
                },
                "plan": {
                    "type": "array",
                    "description": "结构化执行步骤(可选)",
                },
            },
            "required": ["proposal"],
        },
        handler=_optim_plan_handler,
        is_kernel=False,
        safety_level="none",
    ),
    ToolDef(
        name="apply_optim",
        description=(
            "执行一条已批准(approved)的优化方案: 按 plan 真执行白名单动作"
            "(code_execution/file_write/file_read), 会触发权限确认。"
            "plan 内步骤不再单独确认(已由本确认覆盖)。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "optim_id": {"type": "integer", "description": "optim_log id(必填)"},
            },
            "required": ["optim_id"],
        },
        handler=_apply_optim_handler,
        is_kernel=False,
        safety_level="elevated",  # 触发 WS 60s 权限确认
    ),
    ToolDef(
        name="subagent_status",
        description=(
            "查询所有会话最近的子代理委派状态(成功/取消/失败/超时)。"
            "用于全局智能体发现其他对话中子任务被取消/超时的异常, "
            "支持按时间范围/状态过滤。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "since_hours": {
                    "type": "number",
                    "description": "最近 N 小时(默认 24)",
                },
                "status": {
                    "type": "string",
                    "description": "状态过滤: cancelled/failed/succeeded/running",
                },
                "limit": {"type": "integer", "description": "返回条数上限(默认 50)"},
            },
        },
        handler=_subagent_status_handler,
        is_kernel=False,
        safety_level="none",
    ),
    ToolDef(
        name="session_events",
        description=(
            "查询指定会话的 react_events 事件流(跨会话, 可查任意会话)。"
            "用于全局智能体回放某次对话的事件, 定位子任务取消/工具超时/报错"
            "的真实原因。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "integer", "description": "会话 id(必填)"},
                "event_type": {
                    "type": "string",
                    "description": (
                        "事件类型过滤: subagent/error/tool_error/tool_result/"
                        "final/tool_confirmation_required 等"
                    ),
                },
                "limit": {"type": "integer", "description": "返回条数上限(默认 100)"},
            },
            "required": ["session_id"],
        },
        handler=_session_events_handler,
        is_kernel=False,
        safety_level="none",
    ),
]
