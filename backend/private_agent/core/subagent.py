"""V1.5 项-1(ADR-012 §3.2/§3.3) - 子代理执行器 + 监听/心跳闭环。

架构:
- SubagentRunner: 为单个委派创建独立子 session(kind='sub', 决策 A 复用
  ReactLoop 全部上下文/压缩/checkpoint 机制) + 独立心跳 task(与执行分离)。
- watchdog 模块函数: delegate handler 轮询式等待时调用
  (scan_and_mark_stalled / grace_expired / lifetime_exceeded / kill_tasks)。
- 全部 DB 状态变更带 WHERE status='running' 原子条件更新(§3.3b 决策 3,
  幂等, 多实例安全 —— 0 行 = 已被其他 worker 处理, 跳过)。
- 时间戳统一 UTC(§3.1 硬约束, 数据库 now() 生成)。

关键决策(M2 spec, 与 ADR §6.1 评审记录对齐):
1. 心跳 task 与执行 task 分离(§3.3a 决策 1): 模型调用阻塞 60s / 工具卡住
   时心跳仍在刷新 —— "慢但活着"不误判。
2. 心跳故障可观测(§3.3a 决策 2): 心跳 task 非正常退出(cancel/异常) →
   ERROR 日志含 `subagent.heartbeat_task_failure` 标识(ADR §6 问题 5:
   M2 以 ERROR 日志落地, 外部 metrics 按需接入); 心跳故障 ≠ 业务卡死,
   业务 task 不被误杀。
3. cancel 等待窗口 + zombie 检测(§3.3c 决策 4 / AC-4): cancel 后
   `await asyncio.wait_for(task, cancel_wait_sec)`, 超时打
   `zombie_task_detected` ERROR 日志; DB 无论如何置 failed —— asyncio
   同步阻塞协程无法被 cancel 是底层约束, 不假装能杀掉。
4. 硬总时长兜底(§3.3d / AC-2): 与心跳无关, 防御心跳 bug 导致死循环。
5. 子代理工具列表不含 delegate_subtask(由 main.py 附加实现) →
   嵌套深度恒 1(< max_nesting_depth=2)。
6. 子代理 permission_manager=None(委派即授权 —— 主模型负责子任务边界,
   权限确认链路留主会话; M4 可细化)。
7. 子代理 memory_manager=None(不注入/提取用户记忆, 防污染用户画像)。
8. 子代理不挂 pause_controller/hook_runner(父会话暂停/钩子不传播)。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import asyncpg

from private_agent.config.loader import resolve_provider_limits
from private_agent.core.context_manager import (
    ContextManager,
    FrozenHashMismatchError,
)
from private_agent.core.react_loop import ReactLoop
from private_agent.observability.logging import setup_logger
from private_agent.storage import db

__all__ = [
    "SubagentRunner",
    "subagent_cfg",
    "subagent_type_registry",
    "SubagentTypeRegistry",
    "scan_and_mark_stalled",
    "grace_expired_ids",
    "lifetime_exceeded_ids",
    "kill_tasks",
    "cleanup_zombies_on_startup",
]

logger = setup_logger("private_agent.subagent")


class SubagentTypeRegistry:
    """进程级: running 子代理的类型并发计数(跨会话/跨轮)。

    2026-08-13 类型感知限流(方案 §4.2): 同一类型的子代理全局并发受限,
    防"3 个搜索子代理并行打爆外部网站(反爬) / 共享 stdio MCP 通道"。
    超限 acquire 等待(type_wait_timeout_sec), 超时返回 False 由调用方拒绝重规划。
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._cond: asyncio.Condition = asyncio.Condition()

    def current(self, typ: str) -> int:
        """当前 running 计数(同步读取, 测试/日志用)。"""
        return self._counts.get(typ, 0)

    async def acquire(
        self, typ: str, max_conc: int, timeout_sec: float = 30.0
    ) -> bool:
        """尝试获取一个类型配额。超限时等待, 超时返回 False。"""

        async def _wait() -> bool:
            async with self._cond:
                while self._counts.get(typ, 0) >= max_conc:
                    await self._cond.wait()
                self._counts[typ] = self._counts.get(typ, 0) + 1
            return True

        if max_conc <= 0:
            return False
        try:
            return await asyncio.wait_for(_wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return False

    async def release(self, typ: str) -> None:
        """释放一个类型配额(子代理终态时调用)。"""
        async with self._cond:
            if self._counts.get(typ, 0) > 0:
                self._counts[typ] -= 1
            self._cond.notify_all()


# 进程级单例(跨会话/跨轮共享)
subagent_type_registry = SubagentTypeRegistry()


def _rowcount(result) -> int:
    """解析 asyncpg execute 的受影响行数("UPDATE N" / "INSERT 0 N" 等状态串)。

    asyncpg 的 execute() 返回命令状态字符串而非整数, 直接 `== 0` 比较恒 False,
    会造成"0 行也继续处理"的隐蔽 bug —— 统一在此解析。
    """
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0


async def _emit_observability_event(
    conn: asyncpg.Connection,
    *,
    parent_session_id: int,
    turn: int,
    kind: str,
    subagent_id: int,
    detail: str | None = None,
) -> None:
    """M4(ADR-012 §6 问题 5): subagent 关键事件埋点入库(父会话 react_events)。

    kind ∈ {heartbeat_task_failure, zombie_task_detected, stalled, killed,
    max_lifetime_exceeded, restart}。失败静默 —— 观测不阻断业务。
    埋点落在父会话 react_events(event_type='subagent'), 前端/管理端可查。
    """
    try:
        from private_agent.storage.react_events import insert_react_event

        await insert_react_event(
            conn,
            session_id=parent_session_id,
            turn=turn,
            event_type="subagent",
            payload={
                "kind": kind,
                "subagent_id": subagent_id,
                "detail": detail,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "subagent observability event insert failed: kind=%s subagent_id=%s",
            kind, subagent_id,
        )


def subagent_cfg(cfg: dict | None) -> dict:
    """读取 tools.subagent 配置段(带默认值, 测试可注入小值加速)。"""
    s = (cfg or {}).get("tools", {}).get("subagent", {}) or {}
    return {
        "heartbeat_interval_sec": int(s.get("heartbeat_interval_sec", 10)),
        "heartbeat_timeout_sec": float(s.get("heartbeat_timeout_sec", 90)),
        "heartbeat_poll_sec": float(s.get("heartbeat_poll_sec", 5)),
        "grace_sec": float(s.get("grace_sec", 30)),
        "max_total_lifetime_sec": float(s.get("max_total_lifetime_sec", 300)),
        "max_parallel": int(s.get("max_parallel", 3)),
        "max_nesting_depth": int(s.get("max_nesting_depth", 2)),
        "cancel_wait_sec": float(s.get("cancel_wait_sec", 5)),
        "max_restarts": int(s.get("max_restarts", 0)),
        # 2026-08-13 类型感知限流(方案 §4.2/§4.4)
        "same_type_max": int(s.get("same_type_max", 1)),
        "type_wait_timeout_sec": float(s.get("type_wait_timeout_sec", 30)),
    }


# ──────────────────────────────────────────────────────────────────────────────
# SubagentRunner
# ──────────────────────────────────────────────────────────────────────────────


class SubagentRunner:
    """单个子代理的执行器(ADR-012 §3.2)。

    生命周期:
      pending → (run) → running(started_at) → succeeded / failed / cancelled

    终态写入一律 `WHERE status='running'` 条件更新 —— 若 watchdog 已先行置
    failed(kill), 本 runner 的终态写入 0 行被跳过, 不覆盖裁决(幂等)。

    Args:
        cfg: 合并后的配置 dict(含 config_runtime 覆盖)。
        subagent_id: subagents.id。
        task_id: 模型分配的父任务 id(透传 WS 事件)。
        prompt: 委派指令(模型生成, 自包含)。
        parent_session_id: 主会话 id(继承 model/skill/workspace 等)。
        parent_turn: 主会话触发委派的轮次。
        tools: 父会话工具列表(不含 delegate_subtask —— 由 main.py 附加实现)。
        event_sink: async (ev: dict) -> None, 主会话 WS 推送(accept 任意 dict)。
        system_prompt_factory: async (conn, sub_session_id) -> str,
            由 main.py 注入(复用 _get_system_prompt, 避免循环依赖)。
        adapter_factory: (model_id) -> ModelAdapter, 由 main.py 注入
            (复用 _build_session_adapter)。
        compress_adapter: 上下文压缩适配器(与父会话同源, 可 None)。
    """

    def __init__(
        self,
        *,
        cfg: dict,
        subagent_id: int,
        task_id: str | None,
        prompt: str,
        parent_session_id: int,
        parent_turn: int,
        tools: list[Any],
        event_sink: Callable[[dict], Awaitable[None]],
        system_prompt_factory: Callable[..., Awaitable[str]],
        adapter_factory: Callable[[str | None], Any],
        compress_adapter: Any | None = None,
    ) -> None:
        self._cfg = cfg
        self._subagent_id = subagent_id
        self._task_id = task_id
        self._prompt = prompt
        self._parent_session_id = parent_session_id
        self._parent_turn = parent_turn
        self._tools = list(tools)
        self._event_sink = event_sink
        self._system_prompt_factory = system_prompt_factory
        self._adapter_factory = adapter_factory
        self._compress_adapter = compress_adapter
        self._sub_cfg = subagent_cfg(cfg)
        # 运行时状态
        self._conn: asyncpg.Connection | None = None
        self._hb_conn: asyncpg.Connection | None = None
        self._hb_task: asyncio.Task | None = None
        self._hb_stopping = False
        self._sub_session_id: int | None = None
        self._final_content: str = ""
        self._error_msg: str | None = None
        self._tool_call_count = 0
        self._finished = False
        # M3(ADR-012 §3.4): 心跳事件 phase 推断(thinking|tool_exec|idle,
        # 由最近事件更新; 前端卡片"最后心跳 Ns 前"计时 + 停滞警示,
        # phase 仅辅助展示)
        self._phase = "idle"
        # M4: 自动重启计数(restart_attempts 由 DB 读取, 失败且 < max_restarts 时重试)
        self._restart_attempts = 0

    # ── 对外: 执行入口 ──────────────────────────────────────────────────────

    async def run(self) -> int:
        """执行子代理(独立子 session + ReactLoop + 心跳), 终态落库。返回 subagent_id。

        异常/CancelledError 均不外抛吞掉: 终态写入 subagents 后重抛,
        由调用方(delegate handler 轮询)统一处置。
        """
        self._conn = await db.connect(self._cfg)
        try:
            # pending → running(条件更新: 已被父 handler 取消则直接退出)
            updated = await self._conn.execute(
                "UPDATE subagents SET status='running', started_at=now() "
                "WHERE id=$1 AND status='pending'",
                self._subagent_id,
            )
            if _rowcount(updated) == 0:
                return self._subagent_id
            await self._start_heartbeat()
            # M4: max_restarts 自动重启(默认 0 关闭)。业务异常(非取消)且
            # 重启次数未达上限 → 同一子 session 续跑(新 turn); 副作用工具
            # 可能重复执行 —— 默认关闭, 开启为用户显式选择(spec 记录限制)。
            while True:
                # 2026-08-15(M2 P2-20): 任务级暂停挂起点 —— 每轮(ReactLoop
                # 完整执行)之间协作式检查; paused → 挂起轮询, cancelled → 中断
                await self._maybe_pause()
                try:
                    await self._run_react_loop()
                    break  # 正常完成
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    max_restarts = int(self._sub_cfg["max_restarts"])
                    if self._restart_attempts >= max_restarts:
                        raise
                    self._restart_attempts += 1
                    try:
                        await self._conn.execute(
                            "UPDATE subagents SET restart_attempts=$2 "
                            "WHERE id=$1 AND status='running'",
                            self._subagent_id, self._restart_attempts,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    logger.warning(
                        "subagent restart: subagent_id=%s attempt=%d/%d err=%s "
                        "(副作用工具可能重复执行, 默认关闭场景无此问题)",
                        self._subagent_id, self._restart_attempts,
                        max_restarts, type(e).__name__,
                    )
                    await _emit_observability_event(
                        self._conn,
                        parent_session_id=self._parent_session_id,
                        turn=self._parent_turn,
                        kind="restart",
                        subagent_id=self._subagent_id,
                        detail=(
                            f"attempt {self._restart_attempts}/{max_restarts}: "
                            f"{type(e).__name__}: {e}"
                        ),
                    )
                    # 重置单轮捕获(final/error), 供重试轮使用
                    self._final_content = ""
                    self._error_msg = None
            # 正常完成路径
            if self._final_content:
                await self._finish("succeeded", result=self._final_content)
                await self._push({
                    "type": "subagent_result",
                    "subagent_id": self._subagent_id,
                    "session_id": self._parent_session_id,
                    "status": "succeeded",
                    "result": self._final_content,
                })
            else:
                err = self._error_msg or "子代理未产生最终结果(final 事件缺失)"
                await self._finish("failed", error=err[:2000])
                await self._push({
                    "type": "subagent_error",
                    "subagent_id": self._subagent_id,
                    "session_id": self._parent_session_id,
                    "status": "failed",
                    "error": err,
                })
        except asyncio.CancelledError:
            # 被父会话取消/watchdog kill: 条件更新置 cancelled(已 failed 则跳过),
            # 推送失败事件后重抛(保持取消语义)。
            await self._mark_cancelled()
            await self._push({
                "type": "subagent_error",
                "subagent_id": self._subagent_id,
                "session_id": self._parent_session_id,
                "status": "cancelled",
                "error": "cancelled",
            })
            raise
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            logger.exception(
                "subagent failed: subagent_id=%s err=%s",
                self._subagent_id, err,
            )
            await self._finish("failed", error=err[:2000])
            await self._push({
                "type": "subagent_error",
                "subagent_id": self._subagent_id,
                "status": "failed",
                "error": err,
            })
        finally:
            # 收尾: 先标记心跳停止再 cancel(避免 done callback 误报故障)
            self._hb_stopping = True
            if self._hb_task is not None:
                self._hb_task.cancel()
            for c in (self._hb_conn, self._conn):
                if c is not None:
                    try:
                        await c.close()
                    except Exception:  # noqa: BLE001
                        pass
        return self._subagent_id

    # ── 子会话构建 + ReactLoop 复用 ─────────────────────────────────────────

    async def _create_sub_session(self) -> int:
        """创建独立子 session(kind='sub'), 继承父会话 model/skill/workspace/
        permission_mode/memory_enabled(ADR §3.2 决策 A + 打开问题 1/4)。"""
        row = await self._conn.fetchrow(
            "SELECT model_id, locked_skill_name, locked_skill_version, "
            "workspace, permission_mode, memory_enabled "
            "FROM sessions WHERE id=$1",
            self._parent_session_id,
        )
        row = row or {}
        parent_workspace = row.get("workspace")
        # 继承父会话工作区(画地为牢): 覆盖 cfg.system.workspace_root
        if parent_workspace:
            self._cfg = {
                **self._cfg,
                "system": {
                    **self._cfg.get("system", {}),
                    "workspace_root": parent_workspace,
                },
            }
        sid = await self._conn.fetchval(
            """
            INSERT INTO sessions (
                kind, title, model_id, locked_skill_name, locked_skill_version,
                workspace, permission_mode, memory_enabled
            ) VALUES ('sub', $1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            f"[subagent {self._subagent_id}] {self._task_id or ''}".strip(),
            row.get("model_id"),
            row.get("locked_skill_name"),
            row.get("locked_skill_version"),
            parent_workspace,
            row.get("permission_mode", "default"),
            row.get("memory_enabled", True),
        )
        self._sub_session_id = int(sid)
        return self._sub_session_id

    async def _maybe_pause(self) -> None:
        """2026-08-15(M2 P2-20): 任务级暂停挂起点(协作式)。

        每轮 ReactLoop 之间检查 subagents.status:
        - running/终态 → 立即返回
        - paused → 循环轮询(间隔 2s), 直到恢复 running 或转终态
        - cancelled → 抛 asyncio.CancelledError(由 run() 统一处置)

        安全边界: ReactLoop 单轮为原子段(模型调用/工具执行不中断),
        暂停仅在轮间生效 —— 避免半途状态与副作用工具重复执行。
        """
        while True:
            status = await self._conn.fetchval(
                "SELECT status FROM subagents WHERE id = $1",
                self._subagent_id,
            )
            if status not in ("paused",):
                if status == "cancelled":
                    raise asyncio.CancelledError()
                return
            await asyncio.sleep(2)

    async def _run_react_loop(self) -> None:
        """构建 ContextManager + ReactLoop 并执行一轮(复用主会话完整机制)。

        M4: 子 session 仅首次创建(重启复用同一 session, 消息累积到新 turn)。
        """
        if self._sub_session_id is None:
            sub_session_id = await self._create_sub_session()
            # 回填 subagents.session_id → 子代理会话 id(ADR §3.1 决策 A 关联)
            await self._conn.execute(
                "UPDATE subagents SET session_id=$1 "
                "WHERE id=$2 AND status='running'",
                sub_session_id, self._subagent_id,
            )
            self._sub_session_id = sub_session_id
        sub_session_id = self._sub_session_id
        system_prompt = await self._system_prompt_factory(
            self._conn, sub_session_id
        )
        cm = ContextManager(
            session_id=sub_session_id,
            system_prompt=system_prompt,
            tools=self._tools,
            memory_manager=None,  # 决策 7: 子代理不注入/提取用户记忆
            cfg=self._cfg,
        )
        try:
            await cm.ensure_initial(self._conn)
        except FrozenHashMismatchError:
            # 工具/提示词演进(与主会话同机制): 自动重建 frozen zone
            await cm.replace_frozen_zone(
                self._conn,
                system_prompt=cm._system_prompt,
                tools=cm._tools,
            )
        await cm.reload_from_db(self._conn)
        # 子 session 继承父会话 model_id(未锁定时走 fallback 链)
        model_id = await self._conn.fetchval(
            "SELECT model_id FROM sessions WHERE id=$1", sub_session_id
        )
        loop = ReactLoop(
            session_id=sub_session_id,
            context_manager=cm,
            adapter=self._adapter_factory(model_id),
            tools=self._tools,
            conn=self._conn,
            cfg=self._cfg,
            provider_limits=resolve_provider_limits(self._cfg, model_id),
            event_sink=self._wrapped_sink,
            permission_manager=None,   # 决策 6: 委派即授权
            compress_adapter=self._compress_adapter,
            hook_runner=None,          # 决策 8: 子代理不跑 hooks
            pause_controller=None,     # 决策 8: 子代理不响应暂停
        )
        await loop.run_turn(self._prompt)

    # ── 事件包装(前缀隔离) ──────────────────────────────────────────────────

    async def _wrapped_sink(self, ev: dict) -> None:
        """ReactLoop 事件包装: 捕获 final/error + tool_call 统计 + phase 推断,
        以 subagent_event 前缀推送主会话 WS(与主会话事件流隔离)。"""
        ev_type = ev.get("event_type")
        payload = ev.get("payload") or {}
        if ev_type == "final":
            self._final_content = payload.get("content") or ""
            self._phase = "idle"
        elif ev_type == "error":
            self._error_msg = payload.get("message") or ""
            self._phase = "idle"
        elif ev_type == "tool_call":
            self._tool_call_count += 1
            self._phase = "tool_exec"
        elif ev_type == "thinking":
            self._phase = "thinking"
        elif ev_type in ("tool_result", "tool_confirmation_result"):
            self._phase = "idle"
        wrapped = {
            "type": "subagent_event",
            "subagent_id": self._subagent_id,
            "session_id": self._parent_session_id,
            "event_type": ev_type,
            "payload": payload,
        }
        await self._push(wrapped)

    async def _push(self, ev: dict) -> None:
        """WS 推送(失败仅告警, 不中断子代理执行 —— 判定依赖 DB 不依赖 WS)。"""
        try:
            await self._event_sink(ev)
        except Exception:  # noqa: BLE001
            logger.exception(
                "subagent event push failed: subagent_id=%s type=%s",
                self._subagent_id, ev.get("type"),
            )

    # ── 心跳(§3.3a: 独立 task, 与执行分离) ─────────────────────────────────

    async def _start_heartbeat(self) -> None:
        self._hb_conn = await db.connect(self._cfg)
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        self._hb_task.add_done_callback(self._on_hb_done)

    async def _heartbeat_loop(self) -> None:
        """周期刷新 last_heartbeat_at(条件更新: 已终态则退出) + 推 WS 心跳事件。"""
        interval = self._sub_cfg["heartbeat_interval_sec"]
        while not self._hb_stopping:
            await asyncio.sleep(interval)
            try:
                updated = await self._hb_conn.execute(
                    "UPDATE subagents SET last_heartbeat_at=now() "
                    "WHERE id=$1 AND status IN ('running','paused')",
                    self._subagent_id,
                )
                if _rowcount(updated) == 0:
                    # 已终态(watchdog kill / runner 完成), 心跳自然退出
                    self._hb_stopping = True
                    break
                # M3(ADR-012 §3.4): 推 WS heartbeat(前端"最后心跳 Ns 前"计时;
                # WS 断线丢事件无碍 —— 前端以 GET /admin/subagents DB 轮询兜底)
                await self._push({
                    "type": "subagent_heartbeat",
                    "subagent_id": self._subagent_id,
                    "session_id": self._parent_session_id,
                    "phase": self._phase,
                })
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # 心跳协程故障(§3.3a 决策 2): 打 ERROR + 埋点标识,
                # 业务 task 继续(不误杀); 下个周期重试。
                logger.error(
                    "subagent.heartbeat_task_failure subagent_id=%s "
                    "heartbeat refresh failed(业务不受影响, 等待下周期重试)",
                    self._subagent_id,
                    exc_info=True,
                )
                await _emit_observability_event(
                    self._hb_conn,
                    parent_session_id=self._parent_session_id,
                    turn=self._parent_turn,
                    event_type="subagent",
                    payload={
                        "kind": "heartbeat_task_failure",
                        "subagent_id": self._subagent_id,
                        "phase": self._phase,
                    },
                )

    def _on_hb_done(self, t: asyncio.Task) -> None:
        """心跳 task 非正常退出(cancel/未捕获异常) → ERROR + 埋点(AC-1)。

        正常收尾(_hb_stopping=True)跳过; 心跳 task 被外部 kill 或内部
        异常退出且业务仍在运行 → 记录, 供监控区分"心跳协程坏" vs "业务卡死"。
        """
        if self._hb_stopping:
            return
        # 注意: Task.exception() 对已取消 task 抛 CancelledError, 必须先判 cancelled
        if t.cancelled():
            logger.error(
                "subagent.heartbeat_task_failure subagent_id=%s "
                "heartbeat task was cancelled externally (业务 task 不误杀, "
                "stale 判定按 DB 心跳执行)",
                self._subagent_id,
            )
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "subagent.heartbeat_task_failure subagent_id=%s "
                "heartbeat task exited unexpectedly: exc=%r "
                "(业务 task 不误杀, stale 判定按 DB 心跳执行)",
                self._subagent_id, exc,
            )

    # ── 终态写入(全部条件更新, 幂等) ────────────────────────────────────────

    async def _finish(
        self, status: str, *, result: str | None = None, error: str | None = None
    ) -> None:
        if self._finished:
            return
        self._finished = True
        await self._conn.execute(
            "UPDATE subagents SET status=$2, result=$3, error=$4, "
            "tool_calls=$5, finished_at=now() "
            "WHERE id=$1 AND status='running'",
            self._subagent_id, status, result, error, self._tool_call_count,
        )

    async def _mark_cancelled(self) -> None:
        # 2026-08-13 修复: cancelled 补写 tool_calls(此前恒 0, 误导排查
        # "tool_calls=0" 被误读为"子代理没干活") + subagent 埋点(kind='cancelled',
        # 让全局智能体/管理端可查取消事件)。埋点失败不阻断取消流程。
        await self._conn.execute(
            "UPDATE subagents SET status='cancelled', finished_at=now(), "
            "tool_calls=$2 WHERE id=$1 AND status='running'",
            self._subagent_id, self._tool_call_count,
        )
        try:
            await _emit_observability_event(
                self._conn,
                parent_session_id=self._parent_session_id,
                turn=self._parent_turn,
                kind="cancelled",
                subagent_id=self._subagent_id,
                detail=(
                    f"父会话取消/工具超时级联取消(已执行 {self._tool_call_count} "
                    "次工具调用)"
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "subagent cancelled 埋点失败(不阻断取消流程): subagent_id=%s",
                self._subagent_id,
            )


# ──────────────────────────────────────────────────────────────────────────────
# watchdog 模块函数(delegate handler 轮询式等待时调用)
# ──────────────────────────────────────────────────────────────────────────────


async def scan_and_mark_stalled(
    conn: asyncpg.Connection, subagent_ids: list[int], timeout_sec: float
) -> list[int]:
    """扫描"心跳超时"的 running 子代理, 原子置 stalled_at(§3.3b)。

    单条条件 UPDATE(`WHERE status='running' AND stalled_at IS NULL` + 心跳
    过期) —— 天然幂等: 返回的行 = 本次首次检出(需推 subagent_stalled);
    0 行 = 已被其他 worker 处理, 跳过(AC-3)。

    stale 判定: now - COALESCE(last_heartbeat_at, started_at, created_at)
    > heartbeat_timeout_sec; grace 宽限窗口从 stalled_at 起算(R5)。
    """
    if not subagent_ids:
        return []
    rows = await conn.fetch(
        """
        UPDATE subagents SET stalled_at=now()
        WHERE id = ANY($1::bigint[]) AND status='running'
          AND stalled_at IS NULL
          AND (now() - COALESCE(last_heartbeat_at, started_at, created_at))
              > make_interval(secs => $2)
        RETURNING id
        """,
        subagent_ids, float(timeout_sec),
    )
    return [int(r["id"]) for r in rows]


async def grace_expired_ids(
    conn: asyncpg.Connection, subagent_ids: list[int], grace_sec: float
) -> list[int]:
    """grace 窗口(自 stalled_at 起算)已耗尽且仍 running → 原子置
    failed(heartbeat_timeout)。返回本次置位成功的 id 列表(首次, 幂等)。"""
    if not subagent_ids:
        return []
    rows = await conn.fetch(
        """
        UPDATE subagents SET status='failed', error='heartbeat_timeout',
               finished_at=now()
        WHERE id = ANY($1::bigint[]) AND status='running'
          AND stalled_at IS NOT NULL
          AND (now() - stalled_at) > make_interval(secs => $2)
        RETURNING id
        """,
        subagent_ids, float(grace_sec),
    )
    return [int(r["id"]) for r in rows]


async def lifetime_exceeded_ids(
    conn: asyncpg.Connection, subagent_ids: list[int], max_lifetime_sec: float
) -> list[int]:
    """硬总时长兜底(§3.3d / AC-2): now - started_at > 上限 → 强制
    failed(max_lifetime_exceeded), 与心跳是否正常无关(防御心跳 bug)。"""
    if not subagent_ids:
        return []
    rows = await conn.fetch(
        """
        UPDATE subagents SET status='failed', error='max_lifetime_exceeded',
               finished_at=now()
        WHERE id = ANY($1::bigint[]) AND status='running'
          AND (now() - started_at) > make_interval(secs => $2)
        RETURNING id
        """,
        subagent_ids, float(max_lifetime_sec),
    )
    return [int(r["id"]) for r in rows]


async def kill_tasks(
    conn: asyncpg.Connection,
    tasks_by_id: dict[int, asyncio.Task],
    subagent_ids: list[int],
    cancel_wait_sec: float,
    *,
    parent_session_id: int | None = None,
    turn: int = 0,
    reason: str = "heartbeat_timeout",
) -> None:
    """对指定子代理执行 cancel + 等待窗口(§3.3c 决策 4 / AC-4)。

    - cancel 后 await wait_for(task, cancel_wait_sec): 正常取消抛
      CancelledError → 吞掉; 超时 → 打 `zombie_task_detected` ERROR
      + react_events 埋点(M4, 任务拒绝终止, 资源泄漏由观测系统告警)。
    - DB 状态由调用方先行置 failed —— 无论内存 task 是否真正退出,
      业务按失败处理(asyncio 同步阻塞协程无法被 cancel 是底层约束)。
    """
    for sid in subagent_ids:
        task = tasks_by_id.get(sid)
        if task is None or task.done():
            continue
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=cancel_wait_sec)
        except asyncio.TimeoutError:
            logger.error(
                "zombie_task_detected: subagent_id=%s 无法终止"
                "(同步阻塞协程/拒绝取消), DB 已置 failed, 资源泄漏由观测系统告警",
                sid,
            )
            if conn is not None and parent_session_id is not None:
                await _emit_observability_event(
                    conn,
                    parent_session_id=parent_session_id,
                    turn=turn,
                    kind="zombie_task_detected",
                    subagent_id=sid,
                    detail=f"cancel 后 {cancel_wait_sec}s 未退出({reason})",
                )
        except asyncio.CancelledError:
            pass  # 正常终止(与调用方自身被取消区分: 此处显式捕获)


async def cleanup_zombies_on_startup(
    conn: asyncpg.Connection, cfg: dict | None
) -> int:
    """进程级崩溃兜底(§3.3e): 后端重启后, running 且心跳过期的残留子代理
    统一置 failed(heartbeat_timeout_after_restart)。幂等(WHERE status='running')。

    Returns:
        受影响行数(被清理的僵尸 running 记录数)。
    """
    timeout_sec = subagent_cfg(cfg)["heartbeat_timeout_sec"]
    result = await conn.execute(
        """
        UPDATE subagents SET status='failed', error='heartbeat_timeout_after_restart',
               finished_at=now()
        WHERE status='running'
          AND (now() - COALESCE(last_heartbeat_at, started_at, created_at))
              > make_interval(secs => $1)
        """,
        float(timeout_sec),
    )
    return _rowcount(result)
