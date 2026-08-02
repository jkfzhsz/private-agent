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
from private_agent.models.base import AllProvidersFailedError, ChatResult, ModelAdapter
from private_agent.observability.logging import setup_logger
from private_agent.storage.react_events import insert_react_event
from private_agent.tools.defs import ToolDef

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
    ) -> None:
        self._session_id = session_id
        self._context_manager = context_manager
        self._adapter = adapter
        self._tools = tools
        # adapter.chat 期望 OpenAI tools schema dict(非 ToolDef 对象)
        self._tool_schemas = [t.to_openai_schema() for t in tools]
        self._conn = conn
        # 最大迭代轮次: 优先配置 models.limits.max_turns(设置页可调)
        limits = (cfg or {}).get("models", {}).get("limits", {}) if cfg else {}
        self._max_iterations = int(
            limits.get("max_turns") or max_iterations
        )
        self._max_output_tokens = limits.get("max_output_tokens")
        self._turn = 0
        self._state = ReactLoopState.IDLE
        self._iteration = 0
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_sink = event_sink
        self._logger = setup_logger("private_agent.react_loop")
        self._cfg = cfg or {}
        self._injection_guard = InjectionGuard()
        self._compressor = Compressor()
        self._billing = BillingRecorder()

    # ──────────────────────────────────────────────────────────────────────────
    # State machine
    # ──────────────────────────────────────────────────────────────────────────

    async def _emit_event(self, event_type: str, *, payload: dict | None = None) -> None:
        """构造并写入 react_event(同步入库 + 异步推送)。

        Args:
            event_type: 事件类型(thinking/tool_call/tool_result/error/final)。
            payload: 事件负载(可选)。
        """
        event: dict[str, Any] = {
            "type": "react_event",
            "event_type": event_type,
            "session_id": self._session_id,
            "turn": self._turn,
            "payload": payload or {},
        }

        # 持久化到 DB
        await insert_react_event(
            self._conn,
            session_id=self._session_id,
            turn=self._turn,
            event_type=event_type,
            payload=payload or {},
        )

        # 推送到队列(WS 消费)
        await self.event_queue.put(event)

        # event_sink 回调(非 None 时调用,用于 ReplayExecutor 静默收集或真实会话 WS 推送)
        if self._event_sink is not None:
            await self._event_sink(event)

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
        self._turn += 1
        self._iteration = 0
        self._transition(ReactLoopState.THINKING)

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

            # 调用模型(流式优先: adapter 支持 chat_stream 时使用)
            try:
                if hasattr(self._adapter, "chat_stream"):
                    result = await self._adapter.chat_stream(
                        messages,
                        self._tool_schemas,
                        max_tokens=self._max_output_tokens,
                        on_delta=self._emit_delta,
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

            # 首次模型调用后产出 thinking event
            if not has_emitted_thinking:
                await self._emit_event(
                    "thinking",
                    payload={
                        "content": result.content,
                        "turn": self._turn,
                    },
                )
                has_emitted_thinking = True

            if result.tool_calls:
                # 持久化 assistant 消息(含 tool_calls)
                await self._context_manager.append_assistant_message(
                    self._conn,
                    turn=self._turn,
                    content=result.content,
                    tool_calls=result.tool_calls,
                )
                for tc in result.tool_calls:
                    # OpenAI 格式: tc.function.name / tc.function.arguments
                    func = tc.get("function", tc)
                    tool_name = func["name"]
                    args_raw = func.get("arguments", "{}")
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    tool_call_id = tc.get("id", f"call_{self._iteration}")

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
                        await self._emit_event(
                            "error",
                            payload={
                                "message": f"unknown tool: {tool_name}",
                                "tool_call_id": tool_call_id,
                                "stage": "tool_lookup",
                            },
                        )
                        self._transition(ReactLoopState.ERROR)
                        await self._save_checkpoint()
                        return

                    # 执行工具(P2 fix: 包 try/except 防 handler 异常崩溃)
                    try:
                        tool_result = await tool_def.handler(args)
                    except Exception as e:
                        self._logger.exception(
                            "Tool handler failed: tool=%s args=%s", tool_name, args
                        )
                        await self._emit_event(
                            "error",
                            payload={
                                "message": f"tool handler error: {type(e).__name__}: {e}",
                                "tool_call_id": tool_call_id,
                                "tool_name": tool_name,
                                "stage": "tool_execution",
                            },
                        )
                        self._transition(ReactLoopState.ERROR)
                        await self._save_checkpoint()
                        return

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

                # OBSERVING → 继续循环
                self._transition(ReactLoopState.OBSERVING)
            else:
                # 无 tool_calls → final response
                await self._context_manager.append_assistant_message(
                    self._conn, turn=self._turn, content=result.content,
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

    async def _maybe_compress(self) -> None:
        """B4 P0-1: 每轮结束后检查并触发上下文压缩(蓝图 §3.9)。"""
        try:
            cfg = self._cfg.get("context", {}).get("compression", {})
            if not cfg.get("enabled", True):
                return
            context_window = cfg.get("context_window", 8000)
            active_zone_token_limit = cfg.get("active_zone_token_limit", 4000)
            messages = await self._context_manager.build_messages()
            active_turns = self._turn
            triggered = self._compressor.maybe_compress(
                messages,
                active_turns=active_turns,
                context_window=context_window,
                compress_adapter=None,
            )
            if triggered:
                await self._compressor._emit_compress_event(
                    self._conn,
                    session_id=self._session_id,
                    turn=self._turn,
                    trigger="token_limit" if self._token_estimator.estimate_messages(messages) > context_window * 0.8 else "turn_limit",
                )
        except Exception as e:
            self._logger.warning("compression failed: %s", e)

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