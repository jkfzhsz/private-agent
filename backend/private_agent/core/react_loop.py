"""蓝图 §2.4/§2.6 - ReAct 循环状态机 + asyncio 流式事件产出。

Source: spec/m1-react-loop AC-1 + Solution `core/react_loop.py`
- ReactLoopState: IDLE/THINKING/ACTING/OBSERVING/ERROR 五态(蓝图 §2.4)
- ReactLoop: 状态机 + asyncio.Queue 产出 react_event + max_iterations=10
- run_turn: 完整 ReAct 循环(IDLE→THINKING→[ACTING→OBSERVING→THINKING...]→final→IDLE)
- 每步 react_events 入库,turn 递增
- spec AC-1: thinking→tool_call→tool_result→final 四类 event 顺序正确
- spec Edge cases: max_iterations 防死循环(默认 10)
- spec Failure modes: 模型全 fail / 未知工具 → 产出 error event,state=ERROR
- M2 P2 fix: tool_def.handler(args) 包 try/except,产出标准化 error event

Event 格式(推送到 event_queue):
    {
        "type": "react_event",
        "event_type": "thinking" | "tool_call" | "tool_result" | "final" | "error",
        "session_id": int,
        "turn": int,
        "payload": {...},
    }
"""
from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from private_agent.core.billing import BillingRecorder
from private_agent.core.checkpoint import CheckpointManager
from private_agent.core.compressor import Compressor
from private_agent.core.injection_guard import InjectionGuard
from private_agent.core.token_estimator import TokenEstimator
from private_agent.models.base import AllProvidersFailedError, ChatResult, ModelAdapter
from private_agent.observability.logging import setup_logger
from private_agent.storage.react_events import insert_react_event
from private_agent.tools.defs import ToolDef, ToolResult

if TYPE_CHECKING:
    import asyncpg

    from private_agent.core.context_manager import ContextManager

__all__ = ["ReactLoopState", "ReactLoop"]


class ReactLoopState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    ERROR = "error"


class ReactLoop:
    """ReAct 循环状态机(蓝图 §2.4/§2.6)。

    管理单轮对话的思考-行动-观察循环,产出结构化事件流。
    """

    def __init__(
        self,
        session_id: int,
        context_manager: ContextManager,
        adapter: ModelAdapter,
        tools: list[ToolDef],
        conn: asyncpg.Connection,
        max_iterations: int = 10,
        event_sink: Callable[[dict], Awaitable[None]] | None = None,
        cfg: dict | None = None,
        provider_limits: dict | None = None,
        permission_manager: Any | None = None,
        compress_adapter: Any | None = None,
    ) -> None:
        self._session_id = session_id
        self._context_manager = context_manager
        self._adapter = adapter
        self._tools = tools
        # adapter.chat 期望 OpenAI tools schema dict(非 ToolDef 对象)
        self._tool_schemas = [t.to_openai_schema() for t in tools]
        self._conn = conn
        # V2 P1: 工具权限确认管理器(蓝图 §5.12), None 时跳过确认(测试/兼容)
        self._permission_manager = permission_manager
        # 对话参数上限: 优先 provider 级(per-model, 设置页按模型配置),
        # 回退全局 models.limits
        limits = provider_limits
        if not limits:
            limits = (cfg or {}).get("models", {}).get("limits", {}) if cfg else {}
        self._max_iterations = int(
            limits.get("max_turns") or max_iterations
        )
        self._max_output_tokens = limits.get("max_output_tokens")
        self._max_input_tokens = limits.get("max_input_tokens")
        self._turn = 0
        self._turn_initialized = False  # run_turn 首次调用时从历史最大 turn 续号
        self._state = ReactLoopState.IDLE
        self._iteration = 0
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_sink = event_sink
        self._logger = setup_logger("private_agent.react_loop")
        self._cfg = cfg or {}
        self._injection_guard = InjectionGuard()
        self._compressor = Compressor()
        self._token_estimator = TokenEstimator()  # V2 修复: _maybe_compress 曾引用未初始化
        self._billing = BillingRecorder()
        # V2 上下文工程 - Agent 状态栏(AI-Agents-in-Depth §2.6):
        # 纯代码维护的动态元信息(工具计数/时间戳/状态), 注入上下文末尾
        from private_agent.core.status_bar import AgentStatusBar

        self._status_bar = AgentStatusBar()
        status_cfg = self._cfg.get("context", {}).get("status_bar", {})
        self._status_bar_enabled = bool(status_cfg.get("enabled", True))
        self._status_bar_per_turn = bool(
            status_cfg.get("inject_per_turn", True)
        )
        # V2 上下文工程 - 上下文压缩(AI-Agents-in-Depth §2.7.4 / 蓝图 §3.9):
        # compress_adapter 按 compress_model 构建(main.py 传入), None 时
        # 压缩降级为纯滑动窗口(不摘要)。熔断器: 连续失败 3 次禁用本会话压缩。
        self._compress_adapter = compress_adapter
        self._compress_failures = 0
        self._compress_disabled = False

    # ──────────────────────────────────────────────────────────────────────────
    # State machine
    # ──────────────────────────────────────────────────────────────────────────

    async def _emit_event(
        self,
        event_type: str,
        *,
        payload: dict | None = None,
        persist: bool = True,
    ) -> None:
        """构造并写入 react_event(可选入库 + 异步推送)。

        Args:
            event_type: 事件类型(thinking/tool_call/tool_result/error/final)。
            payload: 事件负载(可选)。
            persist: 是否持久化到 DB。False 用于高频流式事件
                (如 sandbox_output), 避免事件风暴, 仅 WS 推送 + 队列。
        """
        event: dict[str, Any] = {
            "type": "react_event",
            "event_type": event_type,
            "session_id": self._session_id,
            "turn": self._turn,
            "payload": payload or {},
        }

        # 持久化到 DB(高频流式事件跳过)
        if persist:
            await insert_react_event(
                self._conn,
                session_id=self._session_id,
                turn=self._turn,
                event_type=event_type,
                payload=payload or {},
            )

        # 推送到队列(测试消费)
        await self.event_queue.put(event)

        # 实时推送给 WS(流式关键: 事件边产生边推送, 而非 run_turn 结束后批量)
        if self._event_sink is not None:
            try:
                await self._event_sink(event)
            except Exception:
                self._logger.exception("event_sink push failed (继续, 不中断对话)")

    @property
    def state(self) -> ReactLoopState:
        """返回当前状态。"""
        return self._state

    def _transition(self, new_state: ReactLoopState) -> None:
        """状态转移。"""
        self._state = new_state

    @property
    def max_iterations(self) -> int:
        """返回最大迭代次数。"""
        return self._max_iterations

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def run_turn(self, user_message: str) -> None:
        """执行一轮 ReAct 循环。

        Args:
            user_message: 用户消息文本。
        """
        # 历史会话续聊: 首次调用时从 messages 表最大 turn 续号(新消息 turn 不
        # 与历史冲突, 否则前端分组合并、上下文错乱)
        if not self._turn_initialized:
            try:
                max_turn = await self._conn.fetchval(
                    "SELECT COALESCE(MAX(turn), 0) FROM messages WHERE session_id = $1",
                    self._session_id,
                )
                self._turn = int(max_turn or 0)
            except Exception:
                self._logger.exception("load max turn failed, 从 0 开始")
            self._turn_initialized = True
        self._turn += 1
        self._iteration = 0
        self._transition(ReactLoopState.THINKING)

        # V2 状态栏: 新 turn 重置工具计数(状态栏反映当前轮内真实执行,
        # 跨轮累积会误导模型对"本轮进度"的判断)
        self._status_bar.reset()

        # 追加 user_message 到上下文
        await self._context_manager.append_user_message(
            self._conn, turn=self._turn, content=user_message,
        )

        # thinking event 仅触发一次(首次模型调用后)
        has_emitted_thinking = False

        while self._iteration < self._max_iterations:
            self._iteration += 1
            self._transition(ReactLoopState.ACTING)

            # 构建消息列表
            messages = await self._context_manager.build_messages()

            # V2 状态栏注入: 追加到上下文末尾的 user-role meta 消息
            # (AI-Agents-in-Depth §2.6.3)。仅内存注入不持久化; 追加到末尾
            # 不破坏 KV Cache 前缀(因果注意力只依赖前序 token)。
            if self._status_bar_enabled and self._status_bar_per_turn:
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": self._status_bar.render(
                            state=self._state.value,
                            turn=self._turn,
                            iteration=self._iteration,
                            max_iterations=self._max_iterations,
                        ),
                    },
                ]

            # 推理增量累积器(跨 iteration 的 thinking 事件流, 前端逐段展示)
            reasoning_acc: list[str] = []

            async def _emit_reasoning(text: str) -> None:
                """推理增量回调: 产出 thinking 事件(前端'查看推理过程'逐段展示)。"""
                if not text:
                    return
                reasoning_acc.append(text)
                await self._emit_event(
                    "thinking",
                    payload={"turn": self._turn, "reasoning": text},
                )

            # 调用模型(流式优先: adapter 支持 chat_stream 时使用)
            try:
                if hasattr(self._adapter, "chat_stream"):
                    result = await self._adapter.chat_stream(
                        messages,
                        self._tool_schemas,
                        max_tokens=self._max_output_tokens,
                        on_delta=self._emit_delta,
                        on_reasoning=_emit_reasoning,
                    )
                else:
                    result = await self._adapter.chat(
                        messages,
                        self._tool_schemas,
                        max_tokens=self._max_output_tokens,
                    )
            except AllProvidersFailedError as e:
                await self._emit_event(
                    "error",
                    payload={
                        "message": str(e),
                        "stage": "model_chat",
                    },
                )
                self._transition(ReactLoopState.ERROR)
                await self._save_checkpoint()
                return

            # B4 P0-4: 记录对话计费
            try:
                if hasattr(result, "usage") and result.usage is not None:
                    await self._billing.record_usage(
                        self._conn,
                        session_id=self._session_id,
                        turn=self._turn,
                        model_id=getattr(result, "used_provider", "unknown"),
                        usage=result.usage,
                        cost_type="dialogue",
                    )
            except Exception:
                self._logger.exception("billing record_usage failed")

            # 首次模型调用后产出 thinking event(推理过程)
            # 流式已通过 on_reasoning 逐段推送; 非流式/无 reasoning 时补发完整内容
            # (始终发: 纯 tool_call 无文本时也保留 thinking 事件, 事件序列稳定)
            if not has_emitted_thinking:
                if not reasoning_acc:
                    reason_text = (
                        getattr(result, "reasoning_content", None)
                        or result.content
                        or ""
                    )
                    await self._emit_event(
                        "thinking",
                        payload={
                            "turn": self._turn,
                            "reasoning": reason_text,
                            "content": result.content or "",
                        },
                    )
                has_emitted_thinking = True

            if result.tool_calls:
                # 持久化 assistant 消息(含 tool_calls + reasoning_content)
                # V2 上下文工程: reasoning_content 一并持久化, 续聊 reload 后
                # 原样回传(DeepSeek V4 系强制要求, AI-Agents-in-Depth 2.3.1)
                await self._context_manager.append_assistant_message(
                    self._conn,
                    turn=self._turn,
                    content=result.content,
                    tool_calls=result.tool_calls,
                    reasoning_content=(
                        getattr(result, "reasoning_content", None) or None
                    ),
                )
                # V2 P2: 同轮多 tool_call 并行执行(蓝图 L612-616 + L4948)
                # Phase A(串行): 解析 + emit tool_call + 权限确认 + 构造执行计划
                plans: list[dict] = []
                for tc in result.tool_calls:
                    # OpenAI 格式: tc.function.name / tc.function.arguments
                    func = tc.get("function", tc)
                    tool_name = func["name"]
                    args_raw = func.get("arguments", "{}")
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    tool_call_id = tc.get("id", f"call_{self._iteration}")

                    # V2 状态栏: 记录工具调用(计数供状态栏渲染)
                    self._status_bar.record_tool_call(tool_name)

                    # 产出 tool_call event
                    await self._emit_event(
                        "tool_call",
                        payload={
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "arguments": args,
                        },
                    )

                    # 查找工具定义
                    tool_def = self._find_tool(tool_name)
                    if tool_def is None:
                        # 未知工具: 单工具 error 回传, 不中断整轮(V2 P2 语义)
                        reason = f"unknown tool: {tool_name}"
                        await self._emit_event(
                            "tool_result",
                            payload={
                                "tool_call_id": tool_call_id,
                                "tool_name": tool_name,
                                "output": "",
                                "error": reason,
                            },
                        )
                        await self._context_manager.append_tool_message(
                            self._conn,
                            turn=self._turn,
                            tool_call_id=tool_call_id,
                            content="",
                            name=tool_name,
                            error=reason,
                        )
                        continue

                    # ── V2 P1: 权限确认(蓝图 §5.12) ──
                    # 仅 elevated 工具走确认;拒绝/超时以 error 回传模型, 循环继续
                    if self._permission_manager is not None:
                        level = getattr(tool_def, "safety_level", "none")
                        if level == "elevated":
                            outcome = await self._permission_manager.check_and_confirm(
                                session_id=self._session_id,
                                tool_def=tool_def,
                                args=args,
                                emit_fn=self._emit_confirmation_required,
                            )
                            await self._emit_event(
                                "tool_confirmation_result",
                                payload={
                                    "confirmation_id": "",
                                    "tool_name": tool_name,
                                    "approved": outcome == "approved",
                                },
                            )
                            if outcome in ("denied", "timeout"):
                                reason = (
                                    "Tool confirmation timeout (60s)"
                                    if outcome == "timeout"
                                    else "User denied tool execution"
                                )
                                await self._emit_event(
                                    "tool_result",
                                    payload={
                                        "tool_call_id": tool_call_id,
                                        "tool_name": tool_name,
                                        "output": "",
                                        "error": reason,
                                    },
                                )
                                await self._context_manager.append_tool_message(
                                    self._conn,
                                    turn=self._turn,
                                    tool_call_id=tool_call_id,
                                    content="",
                                    name=tool_name,
                                    error=reason,
                                )
                                continue
                        elif level == "dangerous":
                            reason = f"Dangerous tool blocked: {tool_name}"
                            await self._emit_event(
                                "tool_result",
                                payload={
                                    "tool_call_id": tool_call_id,
                                    "tool_name": tool_name,
                                    "output": "",
                                    "error": reason,
                                },
                            )
                            await self._context_manager.append_tool_message(
                                self._conn,
                                turn=self._turn,
                                tool_call_id=tool_call_id,
                                content="",
                                name=tool_name,
                                error=reason,
                            )
                            continue

                    # ── V2 P1: 沙箱流式输出注入(code_execution) ──
                    # 把 on_output 回调注入 args 副本(不污染解析出的原始 dict,
                    # 否则回调函数进 messages JSONB 序列化失败), handler 透传到
                    # 沙箱 executor, 分片实时推送 sandbox_output 事件(仅 WS, 不入库)
                    if tool_name == "code_execution":
                        async def _on_output(stream: str, chunk: str) -> None:
                            await self._emit_event(
                                "sandbox_output",
                                payload={"stream": stream, "chunk": chunk},
                                persist=False,
                            )

                        args = dict(args)
                        args["_on_output"] = _on_output

                    plans.append(
                        {
                            "idx": len(plans),
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "args": args,
                            "tool_def": tool_def,
                        }
                    )

                # Phase B: 并行执行(信号量限流; code_execution 串行避免同会话
                # 沙箱 workspace 竞态)。单工具异常 → error 回传, 不中断整轮。
                concurrent_limit = int(
                    (self._cfg or {})
                    .get("tools", {})
                    .get("mcp", {})
                    .get("concurrent_limit", 5)
                )
                sem = asyncio.Semaphore(concurrent_limit)

                async def _exec_plan(plan: dict) -> ToolResult:
                    async with sem:
                        try:
                            return await plan["tool_def"].handler(plan["args"])
                        except Exception as e:  # noqa: BLE001
                            self._logger.exception(
                                "Tool handler failed: tool=%s", plan["tool_name"]
                            )
                            return ToolResult(
                                output="",
                                error=(
                                    f"tool handler error: {type(e).__name__}: {e}"
                                ),
                            )

                serial_plans = [
                    p for p in plans if p["tool_name"] == "code_execution"
                ]
                parallel_plans = [
                    p for p in plans if p["tool_name"] != "code_execution"
                ]
                results_by_idx: dict[int, ToolResult] = {}
                for p in serial_plans:
                    results_by_idx[p["idx"]] = await _exec_plan(p)
                if parallel_plans:
                    outcomes = await asyncio.gather(
                        *(_exec_plan(p) for p in parallel_plans)
                    )
                    for p, tr in zip(parallel_plans, outcomes):
                        results_by_idx[p["idx"]] = tr

                # Phase C(按模型原始 tool_calls 顺序): emit tool_result +
                # 注入防护扫描 + 持久化
                for plan in plans:
                    tool_result = results_by_idx[plan["idx"]]
                    tool_name = plan["tool_name"]
                    tool_call_id = plan["tool_call_id"]

                    # V2 状态栏: 记录工具结果(失败计数供状态栏渲染)
                    self._status_bar.record_tool_result(
                        tool_name, error=tool_result.error
                    )

                    # 产出 tool_result event
                    await self._emit_event(
                        "tool_result",
                        payload={
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "output": tool_result.output,
                            "error": tool_result.error,
                        },
                    )

                    # B3 P0-2: 注入防护扫描(告警不阻断)
                    if self._injection_guard.is_enabled(self._cfg):
                        tool_output = tool_result.output or ""
                        source = "sandbox" if tool_name == "code_execution" else "mcp"
                        try:
                            truncated = self._injection_guard.truncate_tool_result(
                                tool_output, source
                            )
                            scan_result = self._injection_guard.scan(
                                truncated, tool_call_id, source
                            )
                            for alert in scan_result.high_alerts:
                                await self._emit_event(
                                    "injection_alert",
                                    payload={
                                        "pattern": alert.pattern,
                                        "call_id": alert.call_id,
                                        "risk": alert.risk,
                                        "source": alert.source,
                                        "snippet": alert.snippet,
                                    },
                                )
                            tool_result.output = truncated
                        except Exception:
                            self._logger.exception("injection_guard scan failed")

                    # 持久化 tool message
                    await self._context_manager.append_tool_message(
                        self._conn,
                        turn=self._turn,
                        tool_call_id=tool_call_id,
                        content=tool_result.output,
                        name=tool_name,
                    )

                    # §4.15 [MVP]: search_knowledge 结果额外注入 Stable Zone
                    # (除本轮 tool message 外, 供后续轮次长期参考; 蓝图要求
                    # "工具返回的 KB 片段由 context_manager 注入 Stable Zone")
                    if (
                        tool_name == "search_knowledge"
                        and not tool_result.error
                        and tool_result.output
                    ):
                        try:
                            await self._context_manager.inject_kb_chunks(
                                self._conn,
                                turn=self._turn,
                                content=tool_result.output,
                            )
                        except Exception:
                            # 注入失败不影响本轮对话
                            self._logger.exception(
                                "inject_kb_chunks failed (turn=%s)", self._turn,
                            )

                # OBSERVING → 继续循环
                self._transition(ReactLoopState.OBSERVING)
            else:
                # 无 tool_calls → final response
                await self._context_manager.append_assistant_message(
                    self._conn, turn=self._turn, content=result.content,
                    reasoning_content=(
                        getattr(result, "reasoning_content", None) or None
                    ),
                )
                await self._emit_event(
                    "final",
                    payload={
                        "turn": self._turn,
                        "content": result.content,
                    },
                )
                self._transition(ReactLoopState.IDLE)
                await self._save_checkpoint()
                await self._maybe_compress()
                return

        # 超出 max_iterations
        await self._emit_event(
            "error",
            payload={
                "message": f"max_iterations ({self._max_iterations}) reached",
                "stage": "iteration_limit",
            },
        )
        self._transition(ReactLoopState.ERROR)
        await self._save_checkpoint()

    async def _emit_delta(self, text: str) -> None:
        """流式增量回调: 产出 delta 事件(前端逐句/逐字渲染)。"""
        if not text:
            return
        await self._emit_event(
            "delta",
            payload={"turn": self._turn, "content": text},
        )

    async def _emit_confirmation_required(self, ev: dict) -> None:
        """权限确认请求回调(PermissionManager emit_fn)。

        将确认请求持久化(审计) + 实时推送 WS, 前端渲染确认卡片。
        """
        await self._emit_event(
            "tool_confirmation_required",
            payload={
                "confirmation_id": ev["confirmation_id"],
                "tool_name": ev["tool_name"],
                "args_summary": ev["args_summary"],
                "message": ev["message"],
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _save_checkpoint(self) -> None:
        """B3 P0-3: 每轮结束写入 checkpoint(蓝图 §2.14)。"""
        try:
            ctx_summary = {
                "frozen_zone_len": len(self._context_manager.frozen_zone.messages),
                "stable_zone_len": len(self._context_manager.stable_zone.messages),
                "active_zone_msg_count": len(self._context_manager.active_zone.messages),
                "active_zone_turn_range": [self._turn, self._turn],
            }
            await CheckpointManager.save_checkpoint(
                self._conn,
                session_id=self._session_id,
                turn=self._turn,
                ctx_summary=ctx_summary,
            )
        except Exception as e:
            self._logger.warning("checkpoint save failed: %s", e)

    async def _maybe_merge_stable_zone(self) -> bool:
        """§3.10.3 [MVP]: 每 N 轮或 KB 片段超阈值时合并 Stable Zone。

        流程(蓝图 §3.10.3):
        1. 触发判断: turn % N == 0 或 kb_count > threshold(且存在 KB 片段)
        2. 调 compress_adapter 合并所有未压缩 stable 消息 → 单一知识摘要
        3. 旧 stable 消息标记 compressed(DB + 内存)
        4. 新 merged 消息 INSERT zone='stable' + 追加到 stable_zone
        5. 存档 version_snapshots(scope='stable_zone', version=f'turn-{turn}')

        无 compress_adapter 时跳过(无法摘要, 降级不报错)。

        Returns:
            True 表示执行了合并。
        """
        cm = self._context_manager
        kb_count = cm.kb_chunk_count()
        if kb_count <= 0:
            return False
        merge_cfg = self._cfg.get("context", {}).get("compression", {})
        merge_interval = int(merge_cfg.get("merge_interval_turns", 5))
        kb_threshold = int(merge_cfg.get("kb_chunks_merge_threshold", 20))
        if not Compressor.should_merge_stable(
            self._turn, kb_count, merge_interval, kb_threshold
        ):
            return False
        stable_msgs = [
            m for m in cm.stable_zone.messages if not m.get("compressed")
        ]
        if not stable_msgs:
            return False
        if self._compress_adapter is None:
            return False
        try:
            prompt = Compressor.build_merge_prompt(stable_msgs)
            result = await self._compress_adapter.chat(
                [{"role": "user", "content": prompt}], tools=[]
            )
            merged_text = (result.content or "").strip()
            if not merged_text:
                return False
            if not merged_text.startswith("[Merged KB Context]"):
                merged_text = f"[Merged KB Context]\n{merged_text}"
            # 1. 存档 version_snapshots(scope='stable_zone', 蓝图 §3.10.3)
            try:
                await self._conn.execute(
                    """
                    INSERT INTO version_snapshots (scope, version, payload)
                    VALUES ('stable_zone', $1, $2::jsonb)
                    ON CONFLICT (scope, version) DO UPDATE
                    SET payload = EXCLUDED.payload, created_at = now()
                    """,
                    f"turn-{self._turn}",
                    json.dumps(
                        {
                            "messages": [
                                {"role": m.get("role"), "content": m.get("content")}
                                for m in stable_msgs
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            except Exception:
                # 存档失败不影响合并主流程
                self._logger.warning("stable_zone snapshot save failed")
            # 2. 旧 stable 消息标记 compressed(DB + 内存)
            msg_ids = [
                m.get("msg_id") for m in stable_msgs if m.get("msg_id")
            ]
            if msg_ids:
                await self._conn.execute(
                    "UPDATE messages SET compressed=TRUE "
                    "WHERE id = ANY($1::bigint[])",
                    msg_ids,
                )
            for m in stable_msgs:
                m["compressed"] = True
            # 3. 新 merged 消息 INSERT + 内存追加
            merged_id = await self._conn.fetchval(
                """
                INSERT INTO messages (session_id, turn, role, content, zone)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                self._session_id,
                self._turn,
                "user",
                merged_text,
                "stable",
            )
            cm.stable_zone.messages.append(
                {
                    "role": "user",
                    "content": merged_text,
                    "turn": self._turn,
                    "msg_id": merged_id,
                    "zone": "stable",
                }
            )
            self._logger.info(
                "stable zone merged: %d msgs → 1 summary (turn=%s)",
                len(stable_msgs), self._turn,
            )
            return True
        except Exception as e:
            self._logger.warning("stable zone merge failed: %s", e)
            return False

    async def _maybe_compress(self) -> None:
        """V2 上下文工程: 每轮结束后检查并执行上下文压缩(蓝图 §3.9)。

        触发条件(AI-Agents-in-Depth §2.7.4): 轮次 > 10 或 token 超
        context_window 的 80%(接近阈值时批量压缩, 不每轮都压, 避免频繁
        破坏 KV Cache)。

        执行: 滑动窗口标记旧轮次 compressed=True; 有 compress_adapter 时
        对压缩掉的消息生成摘要(摘要进 API); 更新内存 + DB; emit compress 事件。

        熔断器(§2.7.4 第 5 层): 连续失败 3 次禁用本会话压缩, 避免在反复
        失败的会话上持续烧钱。
        """
        if self._compress_disabled:
            return
        try:
            # §3.10.3 [MVP]: Stable Zone 合并(先于 active 压缩, 防止
            # Agentic RAG 多轮检索导致 Stable Zone 膨胀)
            await self._maybe_merge_stable_zone()
            cfg = self._cfg.get("context", {}).get("compression", {})
            if not cfg.get("enabled", True):
                return
            context_window = cfg.get("context_window", 8000)
            messages = await self._context_manager.build_messages()
            active_turns = self._turn
            triggered = self._compressor.maybe_compress(
                messages,
                active_turns=active_turns,
                context_window=context_window,
                compress_adapter=self._compress_adapter,
            )
            if not triggered:
                return
            # 触发 → 执行压缩(滑动窗口 + 可选摘要), 基于含内部 metadata 的消息
            meta_msgs = self._context_manager.get_messages_with_meta()
            result = await self._compressor.execute(
                meta_msgs,
                keep_turns=int(cfg.get("keep_turns", 6)),
                compress_adapter=self._compress_adapter,
            )
            if not result["compressed_msgs"]:
                return
            await self._apply_compression(result)
            # 摘要失败(已降级滑动窗口) → 熔断计数; 成功则清零
            if result.get("summary_error"):
                self._compress_failures += 1
                if self._compress_failures >= 3:
                    self._compress_disabled = True
                    self._logger.warning(
                        "compression disabled for session %s after %d failures",
                        self._session_id,
                        self._compress_failures,
                    )
            else:
                self._compress_failures = 0
            trigger = (
                "token_limit"
                if self._token_estimator.estimate_messages(messages)
                > context_window * 0.8
                else "turn_limit"
            )
            await self._compressor._emit_compress_event(
                self._conn,
                session_id=self._session_id,
                turn=self._turn,
                trigger=trigger,
            )
            self._logger.info(
                "context compressed: %d msgs (trigger=%s, summary=%s)",
                len(result["compressed_msgs"]),
                trigger,
                bool(result["summary"]),
            )
        except Exception as e:
            self._compress_failures += 1
            if self._compress_failures >= 3:
                self._compress_disabled = True
                self._logger.warning(
                    "compression disabled for session %s after %d failures",
                    self._session_id,
                    self._compress_failures,
                )
            self._logger.warning("compression failed: %s", e)

    async def _apply_compression(self, result: dict) -> None:
        """把压缩结果落库 + 更新内存 active_zone。

        - 被压缩消息: UPDATE messages SET compressed=true + 内存标记
          (原文保留, 仅不进 API, 未来可恢复)
        - 被压缩消息归档到 messages_archive(§3.10 [MVP] 压缩存档:
          soft delete + 归档, ttl_cleanup 按 90 天清理)
        - 摘要消息(如有): INSERT zone='active' + compressed_from JSONB,
          插入 active_zone 头部(压缩后仍进 API, 信息密度更高)
        """
        cm = self._context_manager
        compressed_msgs = result["compressed_msgs"]
        msg_ids = [
            m.get("msg_id") for m in compressed_msgs if m.get("msg_id")
        ]
        if msg_ids:
            await self._conn.execute(
                "UPDATE messages SET compressed=TRUE WHERE id = ANY($1::bigint[])",
                msg_ids,
            )
            # §3.10 [MVP] 压缩存档: 原文归档到 messages_archive
            await self._archive_compressed(compressed_msgs)
        # 内存: 按 msg_id 标记 active_zone 原消息(_sliding_window 返回的是
        # 浅拷贝, 直接标记副本无效, 必须回写原对象)
        compressed_ids = set(msg_ids)
        for m in cm.active_zone.messages:
            if m.get("msg_id") in compressed_ids:
                m["compressed"] = True
        # 摘要消息落库 + 内存插入 active 头部
        summary = result["summary"]
        if summary is not None:
            compressed_from = [
                m.get("msg_id") for m in compressed_msgs if m.get("msg_id")
            ]
            summary_id = await self._conn.fetchval(
                """
                INSERT INTO messages
                    (session_id, turn, role, content, compressed_from, zone)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                RETURNING id
                """,
                self._session_id,
                self._turn,
                summary.get("role", "assistant"),
                summary.get("content", ""),
                json.dumps(compressed_from, ensure_ascii=False),
                "active",
            )
            summary["msg_id"] = summary_id
            summary["turn"] = self._turn
            cm.active_zone.messages.insert(0, summary)

    async def _archive_compressed(self, compressed_msgs: list[dict]) -> None:
        """§3.10 [MVP] 压缩存档: 被压缩消息原文写入 messages_archive。

        表结构(蓝图 §9.14): original_msg_id/session_id/turn/role/content/
        reasoning_content/tool_calls/zone。ttl_cleanup 按 90 天清理。
        单条 INSERT(压缩消息数有限, 无需批量优化)。
        """
        for m in compressed_msgs:
            msg_id = m.get("msg_id")
            if not msg_id:
                continue
            tool_calls_json = None
            if m.get("tool_calls"):
                tool_calls_json = json.dumps(
                    m["tool_calls"], ensure_ascii=False
                )
            try:
                await self._conn.execute(
                    """
                    INSERT INTO messages_archive
                        (original_msg_id, session_id, turn, role, content,
                         reasoning_content, tool_calls, zone)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                    """,
                    msg_id,
                    self._session_id,
                    m.get("turn") or self._turn,
                    m.get("role", "assistant"),
                    m.get("content") or "",
                    m.get("reasoning_content"),
                    tool_calls_json,
                    m.get("zone", "active"),
                )
            except Exception:
                # 归档失败不影响压缩主流程(压缩已标记 compressed)
                self._logger.warning(
                    "archive compressed msg %s failed", msg_id,
                )

    def _find_tool(self, name: str) -> ToolDef | None:
        """按名查找工具定义。

        Args:
            name: 工具名称。

        Returns:
            匹配的 ToolDef,未找到时返回 None。
        """
        for td in self._tools:
            if td.name == name:
                return td
        return None