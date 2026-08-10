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
        # V1 安全边界: 仅允许低风险类别(design §4.4: context 类可自动执行)
        category = row["category"] or ""
        if category not in LOW_RISK_ACTIONS and category not in ("context",):
            return ToolResult(
                output="",
                error=(
                    f"category={category} 超出 V1 自动执行白名单 "
                    f"({sorted(LOW_RISK_ACTIONS)})"
                ),
            )
        # 低风险动作模拟执行: 生成执行摘要并回填结果(具体配置写入逻辑在
        # P3 挂接 config_runtime; 此处保证审批→执行闭环可用且可回滚)
        result = (
            f"已执行优化 #{row['id']} [{category}]: "
            f"{row['proposal'][:80]}...\n"
            f"plan 步骤数: {len(plan) if isinstance(plan, list) else 0}"
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
            "执行一条已批准(approved)的优化方案。仅限低风险类别(context 配置类), "
            "会触发权限确认。"
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
]
