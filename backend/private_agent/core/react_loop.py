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
import os
import platform
import time
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
        hook_runner: Any | None = None,
    ) -> None:
        self._session_id = session_id
        self._context_manager = context_manager
        self._adapter = adapter
        self._tools = tools
        # adapter.chat 期望 OpenAI tools schema dict(非 ToolDef 对象)
        # 流畅度优化(方向一): 全量 schema 作兜底; 每轮由 ToolSelector 挑选
        # top-N 注入(_round_tool_schemas), 执行侧 _find_tool 仍遍历全池
        self._tool_schemas = [t.to_openai_schema() for t in tools]
        self._round_tool_schemas: list[dict] | None = None
        # 工具选择器: 每轮 turn 开始时对 user_message 求值一次(迭代间固定)
        from private_agent.tools.selector import ToolSelector

        self._tool_selector = ToolSelector(cfg or {})
        # 保险箱: 工具配对 400 回退计数(单轮内最多 2 次)
        self._pairing_rollbacks = 0
        self._conn = conn
        # V2 P1: 工具权限确认管理器(蓝图 §5.12), None 时跳过确认(测试/兼容)
        self._permission_manager = permission_manager
        # 阶段三批次2(B-1): Hooks 生命周期调度器, None 时跳过(默认零回归)
        self._hook_runner = hook_runner
        # 对话参数上限: 优先 provider 级(per-model, 设置页按模型配置),
        # 回退全局 models.limits
        limits = provider_limits
        if not limits:
            limits = (cfg or {}).get("models", {}).get("limits", {}) if cfg else {}
        self._max_iterations = int(
            limits.get("max_turns") or max_iterations
        )
        self._max_output_tokens = limits.get("max_output_tokens")
        # 方向二: provider 级 context_window(模型能力) → 压缩触发线
        # min(模型能力, 配置) × 0.8; 未配置时回退 context.compression
        self._context_window = limits.get("context_window")
        self._turn = 0
        self._turn_initialized = False  # run_turn 首次调用时从历史最大 turn 续号
        self._state = ReactLoopState.IDLE
        self._iteration = 0
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_sink = event_sink
        self._logger = setup_logger("private_agent.react_loop")
        self._cfg = cfg or {}
        # 项目优化(opencode 借鉴): 运行时环境注入(工作目录/平台),
        # 状态栏渲染时传给模型, 帮助理解项目上下文(工作目录在哪/Git 相关操作等)
        workspace_root = str(
            self._cfg.get("system", {}).get("workspace_root", "")
        )
        self._workspace_label = os.path.expandvars(workspace_root) if workspace_root else ""
        self._platform_label = platform.system()
        self._injection_guard = InjectionGuard()
        self._compressor = Compressor()
        self._token_estimator = TokenEstimator()  # V2 修复: _maybe_compress 曾引用未初始化
        self._billing = BillingRecorder()
        # 项目优化(opencode 借鉴): Doom Loop 检测 - 跟踪工具调用序列,
        # 识别"同一工具/同参数反复调用"的死循环, 提示模型收敛, 超限强制终止
        # (区别于 max_iterations 的硬上限: 循环可能在 2-3 次迭代就反复)
        self._tool_call_trace: list[str] = []   # 本轮迭代内的 "name:argshash"
        self._loop_warnings = 0                  # 已注入的循环提示次数
        self._loop_enabled = bool(
            self._cfg.get("context", {}).get("loop", {}).get("enabled", True)
        )
        self._loop_max_warnings = int(
            self._cfg.get("context", {}).get("loop", {}).get(
                "max_warnings", 2
            )
        )
        self._loop_same_args_threshold = int(
            self._cfg.get("context", {}).get("loop", {}).get(
                "same_args_threshold", 3
            )
        )
        self._loop_same_tool_threshold = int(
            self._cfg.get("context", {}).get("loop", {}).get(
                "same_tool_threshold", 5
            )
        )
        # V2 上下文工程 - Agent 状态栏(AI-Agents-in-Depth §2.6):
        # 纯代码维护的动态元信息(工具计数/时间戳/状态), 注入上下文末尾
        from private_agent.core.status_bar import AgentStatusBar

        self._status_bar = AgentStatusBar()
        status_cfg = self._cfg.get("context", {}).get("status_bar", {})
        self._status_bar_enabled = bool(status_cfg.get("enabled", True))
        self._status_bar_per_turn = bool(
            status_cfg.get("inject_per_turn", True)
        )
        # 方向三: 状态栏注入频率(迭代粒度)。默认 1 = 每迭代注入(原行为);
        # 配 3 = 每 3 次迭代注入 1 次(减少冗余 token, 模型感知略降)
        self._status_bar_inject_every = max(
            1, int(status_cfg.get("inject_every_iterations", 1))
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

        # 流畅度优化(方向一): 每轮对 user_message 求值一次工具注入子集,
        # 迭代间固定(避免每次迭代重排 schema 破坏 KV Cache 前缀)
        try:
            self._round_tool_schemas = [
                t.to_openai_schema()
                for t in self._tool_selector.select(self._tools, user_message)
            ]
        except Exception:
            self._logger.exception("tool select failed, 回退全量")
            self._round_tool_schemas = self._tool_schemas

        # V2 状态栏: 新 turn 重置工具计数(状态栏反映当前轮内真实执行,
        # 跨轮累积会误导模型对"本轮进度"的判断)
        self._status_bar.reset()
        # 项目优化(opencode 借鉴): 循环检测 trace 按"整个对话轮"累积
        # (跨迭代; 跨轮模型重来, 不跨轮累积)
        self._tool_call_trace = []
        self._loop_warnings = 0

        # 阶段三批次2(B-1): user_prompt_submit hook(可拒/改用户输入)
        if self._hook_runner is not None:
            try:
                hook_decision = await self._hook_runner.dispatch(
                    "user_prompt_submit",
                    {"session_id": self._session_id, "turn": self._turn,
                     "user_message": user_message},
                )
                if hook_decision.updated_input and isinstance(
                    hook_decision.updated_input, dict
                ):
                    new_msg = hook_decision.updated_input.get("user_message")
                    if isinstance(new_msg, str) and new_msg.strip():
                        user_message = new_msg
                if hook_decision.additional_context:
                    await self._context_manager.append_system_message(
                        self._conn,
                        turn=self._turn,
                        content=(
                            f"[Hook Context]\n{hook_decision.additional_context}"
                        ),
                        zone="active",
                    )
            except Exception:
                self._logger.exception("user_prompt_submit hook failed (pass-through)")

        # 追加 user_message 到上下文
        await self._context_manager.append_user_message(
            self._conn, turn=self._turn, content=user_message,
        )

        # thinking event 仅触发一次(首次模型调用后)
        has_emitted_thinking = False

        while self._iteration < self._max_iterations:
            self._iteration += 1
            self._transition(ReactLoopState.ACTING)
            self._logger.info(
                "run_turn[%s] iter=%d/%d: 开始(模型调用)",
                self._session_id, self._iteration, self._max_iterations,
            )

            # 构建消息列表
            messages = await self._context_manager.build_messages()

            # 协议兜底: 修复 tool_calls 配对完整性(压缩/恢复/并行执行边界
            # 可能让 assistant.tool_calls 缺少对应 tool 消息 → 上游 400
            # "tool_calls must be followed by tool messages")。扫描为
            # 只读修复: 仅当缺配对时补占位 tool 消息, 不重复追加。
            messages = self._repair_tool_pairing(messages)

            # V2 状态栏注入: 追加到上下文末尾的 user-role meta 消息
            # (AI-Agents-in-Depth §2.6.3)。仅内存注入不持久化; 追加到末尾
            # 不破坏 KV Cache 前缀(因果注意力只依赖前序 token)。
            # 方向三: inject_every_iterations 控制注入频率(默认每迭代)。
            if (
                self._status_bar_enabled
                and self._status_bar_per_turn
                and self._iteration % self._status_bar_inject_every == 0
            ):
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": self._status_bar.render(
                            state=self._state.value,
                            turn=self._turn,
                            iteration=self._iteration,
                            max_iterations=self._max_iterations,
                            workspace=self._workspace_label,
                            platform=self._platform_label,
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
            # 工具 schema: 本轮求值的注入子集(方向一动态 top-N), 兜底全量
            round_schemas = self._round_tool_schemas or self._tool_schemas
            try:
                if hasattr(self._adapter, "chat_stream"):
                    result = await self._adapter.chat_stream(
                        messages,
                        round_schemas,
                        max_tokens=self._max_output_tokens,
                        on_delta=self._emit_delta,
                        on_reasoning=_emit_reasoning,
                    )
                else:
                    result = await self._adapter.chat(
                        messages,
                        round_schemas,
                        max_tokens=self._max_output_tokens,
                    )
            except AllProvidersFailedError as e:
                # 保险箱: 工具调用配对类 400(压缩/恢复/转换边界未覆盖场景) →
                # 自动回退最近一轮工具调用, 让模型换策略重试, 不中断对话。
                err_text = str(e)
                if (
                    "tool_calls" in err_text
                    and "must be followed" in err_text
                    and self._pairing_rollbacks < 2
                ):
                    self._pairing_rollbacks += 1
                    self._logger.warning(
                        "tool pairing 400 detected, rolling back last tool round "
                        "(session=%s, attempt=%d)",
                        self._session_id, self._pairing_rollbacks,
                    )
                    await self._emit_event(
                        "error",
                        payload={
                            "message": (
                                "工具调用数据异常, 已自动回退本轮工具调用, "
                                "正在重新生成…"
                            ),
                            "stage": "model_chat_retry",
                        },
                    )
                    rolled_back = await self._rollback_last_tool_round()
                    if rolled_back:
                        continue  # 重试(下一迭代)
                await self._emit_event(
                    "error",
                    payload={
                        "message": str(e),
                        "stage": "model_chat",
                    },
                )
                self._transition(ReactLoopState.ERROR)
                await self._save_checkpoint()
                # C-3(A.3.5): 所有退出路径统一尝试压缩, 防止上下文无限增长
                await self._maybe_compress()
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
                # C-2(架构修订 A.2.9): assistant 消息不立即落库, 先收集 payload,
                # 与全部 tool 消息在 Phase C 用同一事务提交 —— 保证同轮
                # assistant(tool_calls) + tool 消息要么全写要么全不写,
                # 杜绝"半残状态"导致下次模型调用 400 配对错误。
                # (reasoning_content 一并持久化, 续聊 reload 后原样回传)
                assistant_payload = {
                    "content": result.content,
                    "tool_calls": result.tool_calls,
                    "reasoning_content": (
                        getattr(result, "reasoning_content", None) or None
                    ),
                }
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

                    # 项目优化(opencode 借鉴): Doom Loop 检测
                    # 同一工具/同参数反复调用 → 注入提示消息引导收敛;
                    # 已提示满 max_warnings 仍循环 → 本轮直接终止(不执行工具,
                    # 避免继续烧 token), 给模型一次看到提示收敛的机会在前一次
                    # cfg context.loop.enabled=false 可整体关闭(兼容旧测试)
                    loop_type = (
                        self._detect_tool_loop(tool_name, args)
                        if self._loop_enabled
                        else None
                    )
                    if loop_type is not None:
                        if self._loop_warnings < self._loop_max_warnings:
                            self._loop_warnings += 1
                            note = self._loop_note_message(loop_type, tool_name)
                            # 仅内存注入(不持久化, 同状态栏机制), 模型下一轮可见
                            self._context_manager.active_zone.messages.append(
                                {"role": "user", "content": note}
                            )
                            await self._emit_event(
                                "tool_loop_detected",
                                payload={
                                    "turn": self._turn,
                                    "iteration": self._iteration,
                                    "loop_type": loop_type,
                                    "tool_name": tool_name,
                                },
                            )
                            self._logger.warning(
                                "tool loop detected (type=%s, tool=%s, warning=%d)",
                                loop_type, tool_name, self._loop_warnings,
                            )
                        else:
                            # 已提示满上限仍循环 → 强制终止本轮
                            await self._emit_event(
                                "tool_loop_detected",
                                payload={
                                    "turn": self._turn,
                                    "iteration": self._iteration,
                                    "loop_type": loop_type,
                                    "tool_name": tool_name,
                                    "force_stop": True,
                                },
                            )
                            await self._emit_event(
                                "final",
                                payload={
                                    "turn": self._turn,
                                    "content": (
                                        "检测到工具调用死循环, 已终止本轮执行。"
                                        "建议换一种思路重试, 或拆分问题后再问。"
                                    ),
                                },
                            )
                            self._transition(ReactLoopState.IDLE)
                            # C-3(A.3.5): 所有退出路径统一尝试压缩
                            await self._maybe_compress()
                            return

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
                    # 阶段三批次2(B-1): pre_tool_use hook 先行决策
                    # (deny 阻断 / allow 跳过确认 / ask 强制确认 / updatedInput 改参)
                    hook_decision = None
                    if self._hook_runner is not None:
                        try:
                            hook_decision = await self._hook_runner.dispatch(
                                "pre_tool_use",
                                {"session_id": self._session_id, "turn": self._turn,
                                 "tool_name": tool_name, "tool_call_id": tool_call_id,
                                 "args": args},
                            )
                            if hook_decision.updated_input and isinstance(
                                hook_decision.updated_input, dict
                            ):
                                args = hook_decision.updated_input
                            if hook_decision.additional_context:
                                await self._context_manager.append_system_message(
                                    self._conn,
                                    turn=self._turn,
                                    content=(
                                        f"[Hook Context]\n"
                                        f"{hook_decision.additional_context}"
                                    ),
                                    zone="active",
                                )
                            if hook_decision.permission_decision == "deny":
                                reason = (
                                    f"Tool blocked by hook policy: {tool_name}"
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
                        except Exception:
                            self._logger.exception(
                                "pre_tool_use hook failed (pass-through)"
                            )
                            hook_decision = None

                    # 仅 elevated 工具走确认;拒绝/超时以 error 回传模型, 循环继续
                    hook_allow = (
                        hook_decision is not None
                        and hook_decision.permission_decision == "allow"
                    )
                    if self._permission_manager is not None and not hook_allow:
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
                        # V1.2-6.3: 记录工具执行耗时(前端 tool_result 展示)
                        _t0 = time.monotonic()
                        try:
                            # T-1(架构修订 A.1.4): 路径校验由服务端强制 ——
                            # 覆盖 LLM 提供的 data_dir/workspace 为会话工作区,
                            # 防止模型省略该字段跳过 file_read/write 路径校验。
                            args = dict(plan["args"])
                            ws_root = os.path.expandvars(
                                str(
                                    (self._cfg or {})
                                    .get("system", {})
                                    .get("workspace_root", "")
                                )
                            )
                            if ws_root:
                                args["data_dir"] = ws_root
                                args["workspace"] = ws_root
                            # T-2(架构修订 A.2.5): 工具执行超时按类别分级
                            # (config tools.timeout.categories), 不再读死键
                            # tool_timeout_sec(恒 120s)。
                            t_cfg = (self._cfg or {}).get("tools", {}).get(
                                "timeout", {}
                            )
                            default_t = float(t_cfg.get("default_sec", 30))
                            cats = t_cfg.get("categories", {}) or {}
                            timeout_sec = float(
                                cats.get(plan["tool_name"], default_t)
                            )
                            result = await asyncio.wait_for(
                                plan["tool_def"].handler(args),
                                timeout=timeout_sec,
                            )
                        except asyncio.TimeoutError:
                            self._logger.warning(
                                "tool timeout after %ss: tool=%s",
                                timeout_sec, plan["tool_name"],
                            )
                            result = ToolResult(
                                output="",
                                error=(
                                    f"tool timeout after {timeout_sec}s: "
                                    f"{plan['tool_name']}"
                                ),
                            )
                        except Exception as e:  # noqa: BLE001
                            self._logger.exception(
                                "Tool handler failed: tool=%s", plan["tool_name"]
                            )
                            result = ToolResult(
                                output="",
                                error=(
                                    f"tool handler error: {type(e).__name__}: {e}"
                                ),
                            )
                        result.metadata["duration_ms"] = int(
                            (time.monotonic() - _t0) * 1000
                        )
                        return result

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
                # 注入防护扫描 + 持久化。
                # C-2(A.2.9): assistant + 全部 tool 消息在同一事务提交。
                # 工具执行(Phase B)在事务外; 仅落库在一个事务内 —— 任一
                # INSERT 失败整体回滚, DB 不留"assistant(tool_calls) 无配对
                # tool 消息"的半残状态(400 根治)。emit 事件在事务内无碍(非 DB)。
                async with self._conn.transaction():
                    # 先写 assistant(含 tool_calls + reasoning_content)
                    await self._context_manager.append_assistant_message(
                        self._conn,
                        turn=self._turn,
                        content=assistant_payload["content"],
                        tool_calls=assistant_payload["tool_calls"],
                        reasoning_content=assistant_payload["reasoning_content"],
                    )
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
                                # V1.2-6.3: 工具耗时(前端展示)
                                "duration_ms": (
                                    tool_result.metadata or {}
                                ).get("duration_ms"),
                            },
                        )

                        # B3 P0-2 + 阶段三批次1(B-12): 注入防护扫描 + 净化回灌
                        # (告警不阻断: 高危阻断原始内容回灌, 注入占位; 低危包裹不可信标记)
                        if self._injection_guard.is_enabled(self._cfg):
                            tool_output = tool_result.output or ""
                            source = "sandbox" if tool_name == "code_execution" else "mcp"
                            try:
                                truncated = self._injection_guard.truncate_tool_result(
                                    tool_output, source
                                )
                                sanitized, scan_result = (
                                    self._injection_guard.sanitize_external(
                                        truncated, tool_call_id, source
                                    )
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
                                tool_result.output = sanitized
                            except Exception:
                                self._logger.exception("injection_guard scan failed")

                        # 阶段三批次2(B-1): post_tool_use hook(additionalContext 注入)
                        if self._hook_runner is not None:
                            try:
                                post_decision = await self._hook_runner.dispatch(
                                    "post_tool_use",
                                    {"session_id": self._session_id,
                                     "turn": self._turn,
                                     "tool_name": tool_name,
                                     "tool_call_id": tool_call_id,
                                     "tool_result": tool_result.output,
                                     "tool_error": tool_result.error},
                                )
                                if post_decision.additional_context:
                                    await self._context_manager.append_system_message(
                                        self._conn,
                                        turn=self._turn,
                                        content=(
                                            f"[Hook Context]\n"
                                            f"{post_decision.additional_context}"
                                        ),
                                        zone="active",
                                    )
                            except Exception:
                                self._logger.exception(
                                    "post_tool_use hook failed (pass-through)"
                                )

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
        # C-3(A.3.5): 迭代上限退出同样触发压缩
        await self._maybe_compress()

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
            # 阶段三批次2(B-1): pre_compact hook(压缩前关键信息 flush)
            if self._hook_runner is not None:
                try:
                    compact_decision = await self._hook_runner.dispatch(
                        "pre_compact",
                        {"session_id": self._session_id, "turn": self._turn},
                    )
                    if compact_decision.additional_context:
                        await self._context_manager.append_system_message(
                            self._conn,
                            turn=self._turn,
                            content=(
                                f"[Hook Context]\n"
                                f"{compact_decision.additional_context}"
                            ),
                            zone="active",
                        )
                except Exception:
                    self._logger.exception("pre_compact hook failed (pass-through)")
            # §3.10.3 [MVP]: Stable Zone 合并(先于 active 压缩, 防止
            # Agentic RAG 多轮检索导致 Stable Zone 膨胀)
            await self._maybe_merge_stable_zone()
            cfg = self._cfg.get("context", {}).get("compression", {})
            if not cfg.get("enabled", True):
                return
            cfg_window = cfg.get("context_window", 8000)
            # 方向二: 触发线 = min(provider 模型能力, 配置值) × 0.8;
            # provider 未配置 context_window 时直接用配置值
            context_window = (
                min(self._context_window, cfg_window)
                if self._context_window is not None
                else cfg_window
            )
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
            # 触发 → 执行压缩(滑动窗口 + 可选摘要)。
            # C-1(架构修订 P1-7): 压缩只作用于 active zone —— Frozen/Stable
            # (system prompt/记忆/KB) 永不参与压缩, 防止被误标 compressed
            # 过滤出 API 上下文。_apply_compression 按 msg_id 回写也只命中
            # active zone 消息。
            meta_msgs = list(self._context_manager.active_zone.messages)
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

    def _detect_tool_loop(self, tool_name: str, args: dict) -> str | None:
        """Doom Loop 检测(opencode 借鉴): 识别工具调用死循环模式。

        记录调用到本轮 trace 后, 检查两种循环模式:
        - same_args: 同一工具 + 同一归一化参数连续出现 ≥ threshold 次
          (模型反复用完全相同参数重试, 典型 stuck loop)
        - same_tool: 最近 N 次调用中同一工具占比过高且无其他工具穿插
          (单工具轰炸, 如反复 web_search 同一查询)

        Args:
            tool_name: 工具名。
            args: 工具参数 dict。

        Returns:
            循环类型("same_args"/"same_tool")或 None(无循环)。
        """
        try:
            args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]
        except Exception:
            args_key = ""
        trace_key = f"{tool_name}:{args_key}"
        self._tool_call_trace.append(trace_key)

        # 模式 1: 同参数重复
        recent = self._tool_call_trace[-self._loop_same_args_threshold:]
        if (
            len(recent) >= self._loop_same_args_threshold
            and len(set(recent)) == 1
        ):
            return "same_args"
        # 模式 2: 同工具高频(最近 8 次中 ≥ threshold 次且无其他工具)
        window = self._tool_call_trace[-8:]
        if len(window) >= self._loop_same_tool_threshold:
            tool_names = [k.split(":")[0] for k in window]
            if tool_names.count(tool_name) >= self._loop_same_tool_threshold:
                return "same_tool"
        return None

    def _loop_note_message(self, loop_type: str, tool_name: str) -> str:
        """构造循环提示消息(注入模型上下文, 引导收敛而非硬终止)。"""
        if loop_type == "same_args":
            return (
                "[System Note] 检测到你在反复使用完全相同的参数调用工具 "
                f"{tool_name}——这看起来是一个死循环。请停止重试, 检查上一次"
                "工具返回结果后改变策略(换参数/换工具), 或直接基于已有信息"
                "给出最终回答。"
            )
        return (
            "[System Note] 检测到你连续多次调用工具 "
            f"{tool_name} 且没有进展——这看起来是一个死循环。请停止该模式, "
            "要么换一种方法继续, 要么直接给出最终回答。"
        )

    async def _rollback_last_tool_round(self) -> bool:
        """回退最近一轮工具调用(保险箱): 删除最后一个 assistant(tool_calls)
        及其后续 tool 消息, 让模型下一迭代不带这轮数据重新决策。

        内存与 DB 同步清理(否则下一迭代 build_messages 从 DB 恢复又回来)。

        Returns:
            True 表示回退成功(有可回退的轮次)。
        """
        try:
            az = self._context_manager.active_zone.messages
            # 从后往前找最后一个 assistant 且带 tool_calls
            last_idx = -1
            for i in range(len(az) - 1, -1, -1):
                m = az[i]
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    last_idx = i
                    break
            if last_idx < 0:
                return False
            # 收集该 assistant 及之后的消息(tool_calls id 用于匹配 tool 消息)
            removed = az[last_idx:]
            ids_to_del: list[int] = []
            for m in removed:
                mid = m.get("msg_id")
                if mid is not None:
                    ids_to_del.append(int(mid))
            # 清理内存
            del az[last_idx:]
            # 清理 DB(该轮 assistant + tool 消息删除)
            if ids_to_del and self._conn is not None:
                await self._conn.execute(
                    "DELETE FROM messages WHERE id = ANY($1::int[])",
                    ids_to_del,
                )
            self._logger.info(
                "rolled back tool round: removed %d messages (session=%s)",
                len(removed), self._session_id,
            )
            return True
        except Exception:  # noqa: BLE001
            self._logger.exception("rollback tool round failed")
            return False

    def _repair_tool_pairing(self, messages: list[dict]) -> list[dict]:
        """修复 tool_calls 配对完整性(只读, 返回新列表)。

        场景: 上下文压缩/DB 恢复/并行执行边界可能导致某条 assistant
        消息带 tool_calls 但缺少对应 role=tool 的响应消息 → 模型 API
        400 "assistant message with tool_calls must be followed by tool
        messages"。这里扫描所有 assistant.tool_calls, 对缺失的
        tool_call_id 补一条占位 tool 消息(不重复追加已存在的)。

        Args:
            messages: build_messages 输出(可能是内部引用, 不改原列表)。

        Returns:
            修复后的消息列表(新列表, 原列表不变)。
        """
        pending_ids: list[str] = []
        fixed: list[dict] = []
        for msg in messages:
            fixed.append(msg)
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    cid = tc.get("id") if isinstance(tc, dict) else None
                    if cid:
                        pending_ids.append(cid)
            elif role == "tool" and msg.get("tool_call_id"):
                if msg["tool_call_id"] in pending_ids:
                    pending_ids.remove(msg["tool_call_id"])
        # 残留未配对的 tool_call_id → 补占位 tool 消息
        for cid in pending_ids:
            fixed.append(
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "content": "(工具结果缺失, 已忽略该调用)",
                }
            )
        return fixed

    def _find_tool(self, name: str) -> ToolDef | None:
        """按名查找工具定义。

        Args:
            name: 工具名称。

        Returns:
            匹配的 ToolDef,未找到时返回 None。
        """
        # 流畅度优化(方向一): 记录实际调用 → 下次评分加权。
        # 遍历全池(安全网): 即使未被本轮注入, 模型明确请求时仍可执行
        for td in self._tools:
            if td.name == name:
                self._tool_selector.record_usage(name)
                return td
        return None