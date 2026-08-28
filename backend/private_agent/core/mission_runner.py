"""0.6.0 D-1: MissionRunner —— mission 后台编排器(长任务核心, asyncio 生命周期)。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §三/§4.1
职责边界: **编排不含模型调用** —— 模型只出现在子代理(SubagentRunner)与
监督轮(D-2)里; MissionRunner 是确定性 asyncio 编排代码。

生命周期(状态机见 missions.state):
  planning → (spawn) → executing → (全里程碑 done) → done
                    └→ (里程碑失败) → escalated(保守; D-2 监督轮纠偏或等用户)
  任意运行态 → cancelled(mission_control.abort) / paused(轮询响应)
  进程重启 → escalated(interrupted_by_restart; 用户 approve 后重新 spawn 续跑,
             已完成里程碑按 plan.status 跳过)

F1 交接接线点消项(交接文档 §二):
  W1 spawn 接线(mission_create runner_factory)  W4 白名单装配
  W5 MissionRegistry acquire/release            W9 WS mission_* 事件推送
  W2/W3 工具装配在 main.py(D-1 一并完成)

并发语义(慎改, 项目教训清单):
- 终态写入一律 WHERE state IN ('executing','supervising') 条件更新(幂等);
- registry release 在 finally(异常路径不泄漏名额);
- cancel 后必须 await join(cancel_wait_sec) —— 不 join 会拖死子资源
  (2026-08-13 Searchpin 通道事故根因);
- asyncpg execute() 返回状态串, 行数判定用 _rowcount(恒 False 教训)。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from private_agent.core.mission_assembly import (
    filter_tools_for_task_type,
    mission_registry,
)
from private_agent.core.mission_budget import budget_status, consume_fallback
from private_agent.core.subagent import (
    SubagentRunner,
    _rowcount,
    grace_expired_ids,
    kill_tasks,
    lifetime_exceeded_ids,
    scan_and_mark_stalled,
    subagent_cfg,
)
from private_agent.observability.logging import setup_logger
from private_agent.storage import db

__all__ = [
    "MissionRunner",
    "build_delegation_prompt",
    "cleanup_missions_on_startup",
    "mission_tasks",
]

logger = setup_logger("private_agent.mission_runner")

# 进程级: mission_id → 后台编排 task(spawn 注册; 测试/重启判定/取消用)
mission_tasks: dict[int, asyncio.Task] = {}

_MILESTONE_TERMINAL_OK = ("done", "succeeded")
_RUNNABLE_STATES = ("executing", "supervising")
_POLL_SEC = 2.0          # 状态轮询间隔(pause/abort/子任务终态粗粒度)
_WATCHDOG_POLL_SEC = 5.0  # 子代理终态轮询 + watchdog 扫描间隔


def build_delegation_prompt(charter: dict, ms: dict) -> str:
    """宪章锚定委派指令(§4.3 第一道防线): 由 charter+milestone 模板生成,
    **不从主对话上下文自由生成** —— 子代理拿到的指令永远锚定原始目标。"""
    lines = [
        f"[任务目标] {charter.get('goal', '-')}",
    ]
    constraints = charter.get("constraints") or []
    if constraints:
        lines.append("[硬约束] " + "; ".join(str(c) for c in constraints))
    lines.append(f"[当前阶段] {ms.get('milestone', '-')}")
    if ms.get("dod"):
        lines.append("[完成标准] " + "; ".join(str(d) for d in ms["dod"]))
    lines.append(
        "[执行要求] 严格围绕当前阶段产出, 不得自行更换数据源/方案/目标; "
        "遇到失败如实报告原因(给出可操作的替代建议), 不得反复盲试; "
        "最终输出: 结果文本(≤2000 字, 含结论与关键证据)。"
    )
    return "\n".join(lines)


def _now_journal(kind: str, detail: str) -> dict:
    from datetime import datetime, timezone

    return {
        "kind": kind,
        "ts": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }


class MissionRunner:
    """单个 mission 的后台编排器(一个 mission 一个 run() 协程)。"""

    def __init__(
        self,
        *,
        cfg: dict,
        mission_id: int,
        session_id: int,
        event_sink: Callable[[dict], Awaitable[None]],
        system_prompt_factory: Callable[..., Awaitable[str]],
        adapter_factory: Callable[[str | None], Any],
        compress_adapter: Any | None = None,
        tools: list[Any] | None = None,
    ) -> None:
        self._cfg = cfg
        self._mission_id = mission_id
        self._session_id = session_id
        self._event_sink = event_sink
        self._system_prompt_factory = system_prompt_factory
        self._adapter_factory = adapter_factory
        self._compress_adapter = compress_adapter
        self._tools = list(tools or [])
        self._sub_tasks: dict[int, asyncio.Task] = {}  # subagent_id → task(watchdog kill 用)
        self._aborted = False

    # ── spawn(W1/W5): mission_create 的 runner_factory 落点 ─────────────

    @classmethod
    async def spawn(
        cls,
        *,
        cfg: dict,
        mission_id: int,
        session_id: int,
        event_sink: Callable[[dict], Awaitable[None]],
        system_prompt_factory: Callable[..., Awaitable[str]],
        adapter_factory: Callable[[str | None], Any],
        compress_adapter: Any | None = None,
        tools: list[Any] | None = None,
    ) -> bool:
        """启动后台编排(planning/escalated → executing)。

        Returns:
            True=已启动; False=registry 满/状态条件更新失败(调用方如实反馈)。
        """
        if not await mission_registry.acquire(timeout_sec=30.0):
            logger.warning(
                "mission spawn rejected: registry full (mission_id=%s)", mission_id
            )
            return False
        conn = await db.connect(cfg)
        try:
            st = await conn.execute(
                "UPDATE missions SET state='executing', updated_at=now() "
                "WHERE id=$1 AND state IN ('planning','escalated')",
                mission_id,
            )
            if _rowcount(st) == 0:
                mission_registry.release()
                logger.warning(
                    "mission spawn skipped: state not runnable (id=%s)", mission_id
                )
                return False
        finally:
            await conn.close()

        runner = cls(
            cfg=cfg,
            mission_id=mission_id,
            session_id=session_id,
            event_sink=event_sink,
            system_prompt_factory=system_prompt_factory,
            adapter_factory=adapter_factory,
            compress_adapter=compress_adapter,
            tools=tools,
        )
        task = asyncio.create_task(runner.run())
        mission_tasks[mission_id] = task
        await cls._safe_push(event_sink, {
            "type": "mission_created",
            "mission_id": mission_id,
            "session_id": session_id,
            "state": "executing",
        })
        return True

    # ── 主编排循环 ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """按 plan 顺序执行里程碑; 终态统一落库 + registry 释放。"""
        conn = await db.connect(self._cfg)
        try:
            row = await conn.fetchrow(
                "SELECT charter, plan, budget, state FROM missions WHERE id=$1",
                self._mission_id,
            )
            if row is None:
                logger.error("mission row missing: id=%s", self._mission_id)
                return
            charter = row["charter"] or {}
            plan = row["plan"] or []
            if isinstance(charter, str):
                charter = json.loads(charter)
            if isinstance(plan, str):
                plan = json.loads(plan)

            await self._push_update("executing", "编排启动")
            for ms in plan:
                if not isinstance(ms, dict) or ms.get("status") in _MILESTONE_TERMINAL_OK:
                    continue  # 重启续跑: 已完成里程碑跳过
                await self._wait_if_paused(conn)
                if self._aborted:
                    return
                # 失败重试循环(D-2 监督轮): 预算内每次失败 → 监督判定 →
                # redelegate(方向正确, 换法重派) / wait_user(escalate 等用户)
                while True:
                    ok, detail = await self._run_milestone(conn, charter, ms)
                    ms["status"] = "done" if ok else "failed"
                    await conn.execute(
                        "UPDATE missions SET plan=$2, updated_at=now() WHERE id=$1",
                        self._mission_id,
                        json.dumps(plan, ensure_ascii=False),
                    )
                    await self._append_journal(
                        conn,
                        "milestone_done" if ok else "milestone_failed",
                        f"[{ms.get('id')}] {ms.get('milestone', '')}: {detail}",
                    )
                    if ok:
                        break
                    outcome = await self._handle_milestone_failure(
                        conn, charter, plan, ms, detail
                    )
                    if outcome == "retry":
                        continue  # 监督轮 redelegate: 预算已消耗, 重派
                    return  # escalated(等用户)
            # 全部完成
            st = await conn.execute(
                "UPDATE missions SET state='done', completed_at=now(), updated_at=now() "
                "WHERE id=$1 AND state IN ('executing','supervising')",
                self._mission_id,
            )
            if _rowcount(st) > 0:
                await self._safe_push(self._event_sink, {
                    "type": "mission_done",
                    "mission_id": self._mission_id,
                    "session_id": self._session_id,
                    "state": "done",
                    "result": f"全部 {len(plan)} 个里程碑完成",
                })
        except asyncio.CancelledError:
            # 进程关闭/显式取消: 状态交由 startup 清理(escalated)处置
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("mission run crashed: id=%s", self._mission_id)
            await self._fail(str(e))
        finally:
            mission_tasks.pop(self._mission_id, None)
            mission_registry.release()
            await mission_registry.notify()
            await conn.close()

    # ── 里程碑派发 ──────────────────────────────────────────────────────

    async def _run_milestone(
        self, conn, charter: dict, ms: dict
    ) -> tuple[bool, str]:
        et = ms.get("executor_type")
        if et == "subagent":
            return await self._run_subagent_milestone(conn, charter, ms)
        if et == "script":
            return await self._run_script_milestone(ms)
        if et == "wait":
            wait_sec = float(ms.get("wait_sec") or 60)
            await asyncio.sleep(wait_sec)
            return True, f"等待 {wait_sec}s 完成"
        return False, f"未知 executor_type={et!r}"

    async def _run_subagent_milestone(
        self, conn, charter: dict, ms: dict
    ) -> tuple[bool, str]:
        """派发 subagent 执行体: 建行(宪章锚定 prompt + 类型白名单工具) + 轮询终态。"""
        task_type = ms.get("task_type") or "other"
        prompt = ms.get("prompt_template") or build_delegation_prompt(charter, ms)
        # W4: 白名单装配 —— 工具集按任务类型收紧(编排/监控工具禁区恒剔除)
        sub_tools = filter_tools_for_task_type(self._tools, task_type)
        parent_turn = await conn.fetchval(
            "SELECT COALESCE(MAX(turn), 0) FROM messages WHERE session_id=$1",
            self._session_id,
        )
        parent_model = await conn.fetchval(
            "SELECT model_id FROM sessions WHERE id=$1", self._session_id
        )
        subagent_id = await conn.fetchval(
            """
            INSERT INTO subagents (session_id, parent_turn, parent_task, prompt,
                                   model_id, status, task_type)
            VALUES ($1, $2, $3, $4, $5, 'pending', $6)
            RETURNING id
            """,
            self._session_id, parent_turn, f"mission-{self._mission_id}-{ms.get('id')}",
            prompt, parent_model, task_type,
        )
        subagent_id = int(subagent_id)
        runner = SubagentRunner(
            cfg=self._cfg,
            subagent_id=subagent_id,
            task_id=f"mission-{self._mission_id}-{ms.get('id')}",
            prompt=prompt,
            parent_session_id=self._session_id,
            parent_turn=int(parent_turn or 0),
            tools=sub_tools,
            event_sink=self._event_sink,
            system_prompt_factory=self._system_prompt_factory,
            adapter_factory=self._adapter_factory,
            compress_adapter=self._compress_adapter,
        )
        task = asyncio.create_task(runner.run())
        self._sub_tasks[subagent_id] = task
        try:
            return await self._wait_subagent_terminal(conn, subagent_id, task)
        finally:
            self._sub_tasks.pop(subagent_id, None)

    async def _wait_subagent_terminal(
        self, conn, subagent_id: int, task: asyncio.Task
    ) -> tuple[bool, str]:
        """轮询子代理终态 + 复用 watchdog(stale/grace/kill + 硬总时长)。

        与 delegate handler 的 _watchdog_wait 同源逻辑(ADR-012 §3.3):
        watchdog 判定基于 DB 心跳(不依赖 WS), kill 走 cancel+等待窗口。
        """
        sc = subagent_cfg(self._cfg)
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_WATCHDOG_POLL_SEC)
            if task in done:
                # runner 自行落库终态; 读 DB 取结果/错误
                row = await conn.fetchrow(
                    "SELECT status, result, error FROM subagents WHERE id=$1",
                    subagent_id,
                )
                if row and row["status"] == "succeeded":
                    return True, (row["result"] or "")[:200] or "OK"
                err = (row["error"] if row else None) or "failed"
                return False, f"subagent#{subagent_id}: {err}"
            # watchdog 扫描(条件更新幂等, 与 delegate 同实现)
            stalled = await scan_and_mark_stalled(conn, [subagent_id], sc["heartbeat_timeout_sec"])
            grace = await grace_expired_ids(conn, [subagent_id], sc["grace_sec"])
            lifetime = await lifetime_exceeded_ids(conn, [subagent_id], sc["max_total_lifetime_sec"])
            to_kill = [*grace, *lifetime]
            if to_kill:
                await kill_tasks(
                    conn, {subagent_id: task}, to_kill, sc["cancel_wait_sec"],
                    parent_session_id=self._session_id, turn=0,
                    reason="heartbeat_timeout" if grace else "max_lifetime_exceeded",
                )
            # 终态读取( watchdog kill 或 runner 自行完成 )
            row = await conn.fetchrow(
                "SELECT status, result, error FROM subagents WHERE id=$1", subagent_id
            )
            if row and row["status"] in ("succeeded", "failed", "cancelled"):
                if row["status"] == "succeeded":
                    return True, (row["result"] or "")[:200] or "OK"
                return False, (
                    f"subagent#{subagent_id}: {row['error'] or row['status']}"
                )

    async def _run_script_milestone(self, ms: dict) -> tuple[bool, str]:
        """script 执行体: code_execution(无 LLM 循环; code 由计划给出)。"""
        code = ms.get("script_code")
        if not code:
            return False, "script 类型里程碑缺少 script_code(计划未给出代码)"
        from private_agent.tools.builtins.code_execution import (
            code_execution_handler,
        )

        tr = await code_execution_handler({
            "code": str(code),
            "timeout": float(ms.get("script_timeout") or 300),
            "session_id": str(self._session_id),
        })
        if tr.error:
            return False, f"script 执行失败: {tr.error}"
        output = (tr.output or "").strip()
        return True, output[:200] or "script OK"

    # ── 状态响应(pause/abort/escalated 等待) ────────────────────────────

    async def _wait_if_paused(self, conn) -> None:
        """里程碑间检查点: paused → 等待; cancelled → 退出; escalated → 等待
        (D-2 监督轮纠偏降回 executing 或用户 approve 后继续)。"""
        while True:
            state = await conn.fetchval(
                "SELECT state FROM missions WHERE id=$1", self._mission_id
            )
            if state == "cancelled":
                self._aborted = True
                return
            if state == "paused":
                await asyncio.sleep(_POLL_SEC)
                continue
            if state == "escalated":
                # 保守等待: D-2 纠偏(→executing)或用户 approve(→executing)后继续
                await asyncio.sleep(_POLL_SEC)
                continue
            return

    # ── 失败处置(F1-7 BudgetLedger 接入点; 保守 escalate) ────────────────

    async def _handle_milestone_failure(
        self, conn, charter: dict, plan: list, ms: dict, detail: str
    ) -> str:
        """里程碑失败处置(D-2 监督轮): 预算判定 → 监督裁决 → retry/escalate。

        Returns:
            "retry"    — 监督轮判定方向正确(redelegate), 预算已消耗, 调用方重派;
            "escalate" — 预算耗尽 / 监督判定 wait_user / 监督调用异常, 等用户。
        """
        row = await conn.fetchrow(
            "SELECT budget, journal FROM missions WHERE id=$1", self._mission_id
        )
        budget = row["budget"] or {}
        journal = row["journal"] or []
        if isinstance(budget, str):
            budget = json.loads(budget)
        if isinstance(journal, str):
            journal = json.loads(journal)
        st = budget_status(budget, journal)
        if st["exhausted"]:
            await self._escalate(
                conn, f"里程碑 [{ms.get('id')}] 失败且改道预算耗尽: {detail}"
            )
            return "escalate"
        # 消耗一次改道(journal kind=fallback)
        _, new_journal, allowed, reason = consume_fallback(
            budget, journal, f"[{ms.get('id')}] {detail}"
        )
        if allowed:
            await conn.execute(
                "UPDATE missions SET journal=$2, updated_at=now() "
                "WHERE id=$1 AND state IN ('executing','supervising')",
                self._mission_id,
                json.dumps(new_journal, ensure_ascii=False),
            )
            journal = new_journal
        # D-2 监督轮: 单次 LLM 纠偏判定(异常/解析失败 → wait_user 保守回退)
        decision = {"action": "wait_user", "reason": "监督未执行"}
        try:
            from private_agent.core.mission_supervisor import supervise_failure

            adapter = self._adapter_factory(None)
            decision = await supervise_failure(
                adapter, charter, plan, journal, ms, detail
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("supervisor unavailable: %s", e)
        await self._append_journal(
            conn,
            "supervision",
            f"action={decision['action']} reason={decision['reason'][:150]}",
        )
        if decision["action"] == "redelegate":
            # 降回 executing(允许后续 escalate 条件更新命中)并通知前端纠偏
            await conn.execute(
                "UPDATE missions SET state='executing', updated_at=now() "
                "WHERE id=$1 AND state IN ('executing','supervising')",
                self._mission_id,
            )
            # W8: mission_report 状态汇报(上下文隔离, 前端状态卡片)
            await self._push_report(
                conn,
                f"里程碑 [{ms.get('id')}] 失败, 监督判定方向正确, 重派"
                f"(改道 {reason})。判定: {decision['reason'][:120]}",
            )
            return "retry"
        await self._escalate(
            conn,
            f"里程碑 [{ms.get('id')}] 失败, 监督判定等用户: "
            f"{decision['reason'][:200]} (失败详情: {detail[:200]})",
        )
        return "escalate"

    async def _escalate(self, conn, reason: str) -> None:
        st = await conn.execute(
            "UPDATE missions SET state='escalated', journal = journal || $2::jsonb, "
            "updated_at=now() WHERE id=$1 AND state IN ('executing','supervising')",
            self._mission_id,
            json.dumps([_now_journal("escalated", reason)], ensure_ascii=False),
        )
        if _rowcount(st) > 0:
            await self._safe_push(self._event_sink, {
                "type": "mission_escalated",
                "mission_id": self._mission_id,
                "session_id": self._session_id,
                "reason": reason[:300],
            })

    async def _fail(self, error: str) -> None:
        conn = await db.connect(self._cfg)
        try:
            st = await conn.execute(
                "UPDATE missions SET state='failed', error=$2, completed_at=now(), "
                "updated_at=now() WHERE id=$1 AND state IN ('executing','supervising')",
                self._mission_id, error[:500],
            )
            if _rowcount(st) > 0:
                await self._safe_push(self._event_sink, {
                    "type": "mission_done",
                    "mission_id": self._mission_id,
                    "session_id": self._session_id,
                    "state": "failed",
                    "result": error[:300],
                })
        finally:
            await conn.close()

    # ── journal/WS helpers ──────────────────────────────────────────────

    async def _append_journal(self, conn, kind: str, detail: str) -> None:
        entry = _now_journal(kind, detail[:300])
        await conn.execute(
            "UPDATE missions SET journal = journal || $2::jsonb, updated_at=now() "
            "WHERE id=$1",
            self._mission_id,
            json.dumps([entry], ensure_ascii=False),
        )
        await self._safe_push(self._event_sink, {
            "type": "mission_journal",
            "mission_id": self._mission_id,
            "session_id": self._session_id,
            "entry": entry,
        })

    async def _push_update(self, state: str, detail: str) -> None:
        await self._safe_push(self._event_sink, {
            "type": "mission_update",
            "mission_id": self._mission_id,
            "session_id": self._session_id,
            "state": state,
            "detail": detail,
        })

    async def _push_report(self, conn, content: str) -> None:
        """W8: mission_report 状态汇报落 messages(msg_kind 隔离, 不进上下文)。"""
        try:
            from private_agent.tools.builtins.mission_tools import (
                append_mission_report,
            )

            await append_mission_report(
                conn, self._session_id, self._mission_id, content
            )
        except Exception:  # noqa: BLE001
            logger.warning("mission_report append failed: #%s", self._mission_id)

    @staticmethod
    async def _safe_push(event_sink, ev: dict) -> None:
        try:
            await event_sink(ev)
        except Exception:  # noqa: BLE001
            logger.warning("mission WS push failed: type=%s", ev.get("type"))


async def cleanup_missions_on_startup(conn, cfg: dict | None) -> int:
    """进程重启恢复(T3/§5.2): 运行中 mission 统一置 escalated(等用户裁决)。

    自动续跑有副作用重复风险(子代理/脚本可能已部分执行) —— 保守:
    escalated + journal 记录; 用户 approve_fallback 后 runner_factory 重新
    spawn, 已完成里程碑按 plan.status 跳过(_run 循环 CONTINUE 语义)。
    幂等(WHERE state IN (...) 条件更新)。
    """
    entry = json.dumps(
        [_now_journal("interrupted", "后端重启, 任务中断等待裁决")],
        ensure_ascii=False,
    )
    st = await conn.execute(
        "UPDATE missions SET state='escalated', journal = journal || $1::jsonb, "
        "updated_at=now() WHERE state IN ('planning','executing','supervising')",
        entry,
    )
    n = _rowcount(st)
    if n > 0:
        logger.warning("startup: %d interrupted mission(s) -> escalated", n)
    return n
