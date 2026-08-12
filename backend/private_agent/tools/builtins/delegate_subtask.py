"""delegate_subtask 内置工具(ADR-012 §3.5) - 并行子任务委派。

handler 采用**闭包注入**而非模块级全局(区别于 code_execution 的
set_sandbox_config 模式): main.py 每次构建 ReactLoop 时调用
build_delegate_subtask_tool(...) 生成绑定当轮上下文(conn/cfg/session_id/
event_sink/tools)的 ToolDef —— 多会话并发时无全局串扰。

核心语义:
- 阻塞式工具(与现有工具一致, 工具执行期间主循环挂起, 并行发生在 runner 层);
- 轮询式等待(绝不用裸 await asyncio.wait) + 心跳扫描(watchdog) ——
  子代理挂掉时主对话能及时释放, 不等满工具超时 300s;
- CancelledError(父会话停止 / 工具超时 wait_for) → 级联取消全部 runner +
  DB 批量置 cancelled → 重抛;
- 聚合结果截断后回主模型(ADR R12)。

嵌套深度: 子代理工具列表由 main.py 附加(仅父会话), 子代理天然不含本工具
→ 嵌套深度恒 1(< max_nesting_depth=2); 防御性校验拒绝 kind='sub' 会话再委派。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from private_agent.core.subagent import (
    SubagentRunner,
    grace_expired_ids,
    kill_tasks,
    lifetime_exceeded_ids,
    scan_and_mark_stalled,
    subagent_cfg,
)
from private_agent.observability.logging import setup_logger
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["DELEGATE_SCHEMA", "build_delegate_subtask_tool", "DELEGATE_TOOL_NAME"]

logger = setup_logger("private_agent.delegate")

DELEGATE_TOOL_NAME = "delegate_subtask"

DELEGATE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "子任务标识(聚合结果中引用)",
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "自包含的委派指令: 必须明确子任务边界"
                            "(输入给什么、要求输出什么、长度上限)"
                        ),
                    },
                },
                "required": ["id", "prompt"],
            },
        }
    },
    "required": ["subtasks"],
}


def build_delegate_subtask_tool(
    *,
    conn,
    cfg: dict,
    session_id: int,
    event_sink: Callable[[dict], Awaitable[None]],
    tools: list[Any],
    system_prompt_factory: Callable[..., Awaitable[str]],
    adapter_factory: Callable[[str | None], Any],
    compress_adapter: Any | None = None,
) -> ToolDef:
    """构建绑定当轮上下文的 delegate_subtask 工具(ADR-012 §3.5)。

    Args:
        conn: 父会话连接(建行/扫描/聚合; 子代理 runner 自开独立连接)。
        cfg: 合并后的配置 dict。
        session_id: 父会话 id。
        event_sink: async (ev: dict) -> None, 主会话 WS 推送。
        tools: 父会话工具列表(子代理继承, 不含本工具 → 深度恒 1)。
        system_prompt_factory: async (conn, sub_session_id) -> str。
        adapter_factory: (model_id) -> ModelAdapter。
        compress_adapter: 上下文压缩适配器(可 None)。
    """
    sc = subagent_cfg(cfg)

    async def _handler(args: dict) -> ToolResult:
        return await _delegate_handler(
            conn=conn,
            cfg=cfg,
            session_id=session_id,
            event_sink=event_sink,
            tools=tools,
            args=args,
            sc=sc,
            system_prompt_factory=system_prompt_factory,
            adapter_factory=adapter_factory,
            compress_adapter=compress_adapter,
        )

    return ToolDef(
        name=DELEGATE_TOOL_NAME,
        description=(
            "将 1~3 个相互独立的子任务并行委派给子代理执行(各自独立上下文)。"
            "适合: 并行调研/多文件独立修改/多路检索。返回每个子任务的结果文本;"
            "子任务失败不中断其他子任务, 失败原因随结果返回。"
            "必须明确子任务边界(输入给什么、要求输出什么、长度上限), "
            "指令须自包含(子代理看不到主对话历史)。"
            "子代理不可再委派(嵌套深度上限 2, 当前实现限 1)。"
        ),
        parameters_schema=DELEGATE_SCHEMA,
        handler=_handler,
        safety_level="none",
        risk_level="medium",
        # ToolSelector 锚点: 始终注入模型(委派是主模型的关键编排能力,
        # 不能因 top-N 关键词评分被裁掉)
        is_kernel=True,
    )


async def _delegate_handler(
    *,
    conn,
    cfg: dict,
    session_id: int,
    event_sink: Callable[[dict], Awaitable[None]],
    tools: list[Any],
    args: dict,
    sc: dict,
    system_prompt_factory,
    adapter_factory,
    compress_adapter,
) -> ToolResult:
    """委派 handler: 校验 → 建行 → 并行 spawn → 轮询等待 + watchdog → 聚合。"""
    subtasks = args.get("subtasks", [])
    if not isinstance(subtasks, list) or not subtasks:
        return ToolResult(output="", error="delegate_subtask: subtasks 不能为空")
    if len(subtasks) > sc["max_parallel"]:
        return ToolResult(
            output="",
            error=(
                f"delegate_subtask: 单轮最多 {sc['max_parallel']} 个并行子任务,"
                f" 收到 {len(subtasks)}"
            ),
        )
    for i, st in enumerate(subtasks):
        if not isinstance(st, dict) or not st.get("id") or not st.get("prompt"):
            return ToolResult(
                output="",
                error=f"delegate_subtask: subtasks[{i}] 必须含非空 id 与 prompt",
            )
        if not isinstance(st["id"], str) or not isinstance(st["prompt"], str):
            return ToolResult(
                output="",
                error=f"delegate_subtask: subtasks[{i}].id/prompt 必须为字符串",
            )
    # 防御性嵌套校验(子代理工具列表不含本工具, 深度恒 1; 保底防异常注入)
    kind = await conn.fetchval("SELECT kind FROM sessions WHERE id=$1", session_id)
    if kind == "sub":
        return ToolResult(
            output="",
            error="delegate_subtask: 子代理不支持嵌套委派(嵌套深度上限)",
        )

    # 触发委派的轮次(当前轮 user 消息已 append, MAX(turn) = 当前轮)
    parent_turn = await conn.fetchval(
        "SELECT COALESCE(MAX(turn), 0) FROM messages WHERE session_id=$1",
        session_id,
    )
    parent_model = await conn.fetchval(
        "SELECT model_id FROM sessions WHERE id=$1", session_id
    )

    # 1) 创建 subagents 行(pending) + 推 subagent_start
    subagent_ids: list[int] = []
    for st in subtasks:
        sid = await conn.fetchval(
            """
            INSERT INTO subagents (session_id, parent_turn, parent_task, prompt,
                                   model_id, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            RETURNING id
            """,
            session_id, parent_turn, st["id"], st["prompt"], parent_model,
        )
        subagent_ids.append(int(sid))
        await _safe_push(event_sink, {
            "type": "subagent_start",
            "subagent_id": int(sid),
            "session_id": session_id,
            "task_id": st["id"],
            "prompt": st["prompt"],
        })

    # 2) 并行 spawn runner(数量已 ≤ max_parallel, 无需再 Semaphore 限流)
    runners = [
        SubagentRunner(
            cfg=cfg,
            subagent_id=sid,
            task_id=st["id"],
            prompt=st["prompt"],
            parent_session_id=session_id,
            parent_turn=parent_turn,
            tools=tools,
            event_sink=event_sink,
            system_prompt_factory=system_prompt_factory,
            adapter_factory=adapter_factory,
            compress_adapter=compress_adapter,
        )
        for sid, st in zip(subagent_ids, subtasks)
    ]
    tasks = {sid: asyncio.create_task(r.run()) for sid, r in zip(subagent_ids, runners)}
    tasks_by_id: dict[int, "asyncio.Task"] = dict(tasks)

    # 3) 轮询式等待 + 心跳扫描(watchdog) —— 绝不用裸 await asyncio.wait
    try:
        await _watchdog_wait(
            conn=conn,
            event_sink=event_sink,
            subagent_ids=subagent_ids,
            tasks_by_id=tasks_by_id,
            sc=sc,
            parent_session_id=session_id,
            parent_turn=parent_turn,
        )
    except asyncio.CancelledError:
        # 父会话停止 / 工具超时(wait_for 300s) → 级联取消 + DB 批量置 cancelled
        logger.warning(
            "delegate_subtask interrupted: session=%s cancelling %d subagents",
            session_id, len(subagent_ids),
        )
        for t in tasks_by_id.values():
            if not t.done():
                t.cancel()
        await conn.execute(
            """
            UPDATE subagents SET status='cancelled', finished_at=now()
            WHERE id = ANY($1::bigint[]) AND status='running'
            """,
            subagent_ids,
        )
        raise

    # 4) 聚合结果(截断防超长, ADR R12)
    rows = await conn.fetch(
        """
        SELECT id, parent_task, status, result, error, tool_calls
        FROM subagents WHERE id = ANY($1::bigint[]) ORDER BY id
        """,
        subagent_ids,
    )
    lines: list[str] = []
    for r in rows:
        task_label = r["parent_task"] or f"#{r['id']}"
        if r["status"] == "succeeded":
            body = (r["result"] or "").strip()
            lines.append(f"[子任务 {task_label}] succeeded:\n{body}")
        else:
            err = r["error"] or r["status"]
            lines.append(f"[子任务 {task_label}] {r['status']}: {err}")
    output = "\n\n".join(lines)
    # 结果截断(复用注入防护的 token 阈值截断)
    try:
        from private_agent.core.injection_guard import InjectionGuard

        output = InjectionGuard().truncate_tool_result(output, "mcp")
    except Exception:  # noqa: BLE001
        if len(output) > 8000:
            output = output[:8000] + "\n...[截断]"
    return ToolResult(output=output, metadata={"subagent_ids": subagent_ids})


async def _watchdog_wait(
    *,
    conn,
    event_sink: Callable[[dict], Awaitable[None]],
    subagent_ids: list[int],
    tasks_by_id: dict[int, "asyncio.Task"],
    sc: dict,
    parent_session_id: int,
    parent_turn: int,
) -> None:
    """轮询式等待全部子代理完成 + 心跳扫描(stale/grace/kill + 硬总时长)。

    判定规则(§3.3b, R5): grace 从 stalled_at(检出时刻)起算, 非最后心跳。
    所有 DB 变更走条件更新(幂等, 返回行 = 本次处置, 不重复推事件)。
    M4: stale/kill/zombie 关键节点写入父会话 react_events 埋点(可观测)。
    """
    pending = set(tasks_by_id.values())
    while pending:
        done, pending = await asyncio.wait(pending, timeout=sc["heartbeat_poll_sec"])
        # 收集已完成的 runner(正常/异常均视为完成; 终态已由 runner 落库)
        for d in done:
            try:
                d.result()
            except asyncio.CancelledError:
                pass  # 本 handler kill 或外部取消的 runner, 已自行清理
            except Exception:  # noqa: BLE001
                pass  # runner 内部已捕获所有业务异常, 此处仅兜底

        # ── 心跳扫描 ──
        # stale 首次检出: 原子置 stalled_at + 推 subagent_stalled(黄色警示)
        stalled = await scan_and_mark_stalled(
            conn, subagent_ids, sc["heartbeat_timeout_sec"]
        )
        for sid in stalled:
            await _safe_push(event_sink, {
                "type": "subagent_stalled",
                "subagent_id": sid,
                "session_id": parent_session_id,
                "stale_sec": sc["heartbeat_timeout_sec"],
            })
            await _emit_obs(
                conn, parent_session_id, parent_turn,
                kind="stalled", subagent_id=sid,
                detail=f"心跳超时 {sc['heartbeat_timeout_sec']}s, 进入 grace 宽限",
            )
        # grace 已耗尽(自 stalled_at 起算)仍无新心跳 → kill
        grace_expired = await grace_expired_ids(conn, subagent_ids, sc["grace_sec"])
        if grace_expired:
            for sid in grace_expired:
                await _safe_push(event_sink, {
                    "type": "subagent_error",
                    "subagent_id": sid,
                    "session_id": parent_session_id,
                    "status": "failed",
                    "error": "heartbeat_timeout",
                })
                await _emit_obs(
                    conn, parent_session_id, parent_turn,
                    kind="killed", subagent_id=sid,
                    detail="grace 耗尽仍无心跳 → failed(heartbeat_timeout)",
                )
            await kill_tasks(
                conn, tasks_by_id, grace_expired, sc["cancel_wait_sec"],
                parent_session_id=parent_session_id, turn=parent_turn,
                reason="heartbeat_timeout",
            )
        # 硬总时长兜底(与心跳无关, 防御心跳 bug 死循环)
        lifetime_expired = await lifetime_exceeded_ids(
            conn, subagent_ids, sc["max_total_lifetime_sec"]
        )
        if lifetime_expired:
            for sid in lifetime_expired:
                await _safe_push(event_sink, {
                    "type": "subagent_error",
                    "subagent_id": sid,
                    "session_id": parent_session_id,
                    "status": "failed",
                    "error": "max_lifetime_exceeded",
                })
                await _emit_obs(
                    conn, parent_session_id, parent_turn,
                    kind="max_lifetime_exceeded", subagent_id=sid,
                    detail=f"超过硬总时长 {sc['max_total_lifetime_sec']}s",
                )
            await kill_tasks(
                conn, tasks_by_id, lifetime_expired, sc["cancel_wait_sec"],
                parent_session_id=parent_session_id, turn=parent_turn,
                reason="max_lifetime_exceeded",
            )


async def _safe_push(event_sink, ev: dict) -> None:
    try:
        await event_sink(ev)
    except Exception:  # noqa: BLE001
        logger.exception(
            "subagent WS event push failed: type=%s subagent_id=%s",
            ev.get("type"), ev.get("subagent_id"),
        )


async def _emit_obs(conn, parent_session_id: int, parent_turn: int, *, kind: str,
                    subagent_id: int, detail: str | None = None) -> None:
    """M4 埋点(父会话 react_events, 失败静默)。"""
    from private_agent.core.subagent import _emit_observability_event

    await _emit_observability_event(
        conn,
        parent_session_id=parent_session_id,
        turn=parent_turn,
        kind=kind,
        subagent_id=subagent_id,
        detail=detail,
    )
