"""蓝图 §2.4/§2.6 - ReAct 循环状态机 + asyncio 流式事件产出。

Source: spec/m1-react-loop AC-1 + Solution `core/react_loop.py`
- ReactLoopState: IDLE/THINKING/ACTING/OBSERVING/ERROR 五态(蓝图 §2.4)
- ReactLoop: 状态机 + asyncio.Queue 产出 react_event + max_iterations=10
- run_turn: 完整 ReAct 循环(IDLE→THINKING→[ACTING→OBSERVING→THINKING...]→final→IDLE)
- 每步 react_events 入库,turn 递增
- spec AC-1: thinking→tool_call→tool_result→final 四类 event 顺序正确
- spec Edge cases: max_iterations 防死循环(默认 10)
- spec Failure modes: 模型全 fail / 未知工具 → 产出 error event,state=ERROR
"""
from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import TYPE_CHECKING, Any

from private_agent.models.base import AllProvidersFailedError, ChatResult, ModelAdapter
from private_agent.observability.logging import setup_logger
from private_agent.storage.react_events import insert_react_event
from private_agent.tools.defs import ToolDef

if TYPE_CHECKING:
    import asyncpg

    from private_agent.core.context_manager import ContextManager

__all__ = ["ReactLoopState", "ReactLoop"]

_logger = setup_logger(__name__)


class ReactLoopState(Enum):
    """蓝图 §2.4 ReAct 状态机五态。"""

    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    ERROR = "error"


class ReactLoop:
    """蓝图 §2.4/§2.6 ReAct 循环状态机。

    单轮流程:
    1. IDLE → THINKING: 收到 user_message,build_per_turn 构造上下文
    2. THINKING: adapter.chat() 产出 LLM 回复,产出 thinking event(仅首次迭代)
    3. 若有 tool_calls → ACTING: 执行工具,产出 tool_call/tool_result events → OBSERVING
    4. OBSERVING → THINKING: 追加 tool_result,再次调用 adapter(循环)
    5. 无 tool_calls → 产出 final event → IDLE
    6. 达到 max_iterations 或模型全失败 → 产出 error event → ERROR

    每步 react_events 入库,turn 递增。
    """

    def __init__(
        self,
        session_id: int,
        context_manager: "ContextManager",
        adapter: ModelAdapter,
        tools: list[ToolDef],
        conn: "asyncpg.Connection",
        max_iterations: int = 10,
    ) -> None:
        self.session_id = session_id
        self.context_manager = context_manager
        self.adapter = adapter
        self.tools = list(tools)
        self.conn = conn
        self.max_iterations = max_iterations
        self.state = ReactLoopState.IDLE
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._turn = 0

    def _transition(self, new_state: ReactLoopState) -> None:
        """状态转换(记录日志)。"""
        _logger.debug(
            "react_loop state transition",
            extra={
                "session_id": self.session_id,
                "from": self.state.value,
                "to": new_state.value,
            },
        )
        self.state = new_state

    async def _emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """产出 react_event:入 event_queue + 持久化到 react_events 表。

        Args:
            event_type: thinking/tool_call/tool_result/final/error/checkpoint
            payload: 事件负载
        """
        event = {
            "type": "react_event",
            "session_id": self.session_id,
            "turn": self._turn,
            "event_type": event_type,
            "payload": payload,
        }
        await self.event_queue.put(event)
        await insert_react_event(
            self.conn,
            session_id=self.session_id,
            turn=self._turn,
            event_type=event_type,
            payload=payload,
        )

    def _find_tool(self, name: str) -> ToolDef | None:
        """按名称查找 ToolDef。"""
        for t in self.tools:
            if t.name == name:
                return t
        return None

    async def run_turn(self, user_content: str) -> None:
        """执行一轮 ReAct 循环(蓝图 §2.4)。

        流程:
        1. IDLE → THINKING
        2. build_per_turn 构造上下文
        3. 循环调用 adapter.chat:
           - 首次产出 thinking event
           - 有 tool_calls → ACTING → 执行工具 → OBSERVING → THINKING(循环)
           - 无 tool_calls → 产出 final event → IDLE
        4. 达到 max_iterations 或模型失败 → error event → ERROR

        Args:
            user_content: 用户输入文本。
        """
        self._turn += 1
        self._transition(ReactLoopState.THINKING)

        # 构造上下文(frozen + stable + active + 本次 user)
        messages = await self.context_manager.build_per_turn(
            self.conn,
            turn=self._turn,
            user_content=user_content,
        )
        tools_schema = [t.to_openai_schema() for t in self.tools] if self.tools else None

        iteration = 0
        while True:
            iteration += 1
            if iteration > self.max_iterations:
                await self._emit_event(
                    "error",
                    payload={
                        "message": f"max_iterations({self.max_iterations}) exceeded",
                        "iteration": iteration,
                    },
                )
                self._transition(ReactLoopState.ERROR)
                return

            # 调用模型
            try:
                result: ChatResult = await self.adapter.chat(messages, tools_schema)
            except AllProvidersFailedError as e:
                await self._emit_event(
                    "error",
                    payload={"message": str(e), "stage": "model_chat"},
                )
                self._transition(ReactLoopState.ERROR)
                return

            # 首次迭代产出 thinking event(蓝图 §2.4 THINKING 态)
            if iteration == 1:
                await self._emit_event(
                    "thinking",
                    payload={
                        "content": result.content,
                        "tool_calls": result.tool_calls,
                        "used_provider": result.used_provider,
                        "failed_providers": result.failed_providers,
                    },
                )

            if not result.tool_calls:
                # 无工具调用:产出 final → IDLE
                await self.context_manager.append_assistant_message(
                    self.conn,
                    turn=self._turn,
                    content=result.content,
                )
                await self._emit_event(
                    "final",
                    payload={
                        "content": result.content,
                        "used_provider": result.used_provider,
                    },
                )
                self._transition(ReactLoopState.IDLE)
                return

            # 有 tool_calls:THINKING → ACTING
            self._transition(ReactLoopState.ACTING)

            # 持久化 assistant message(含 tool_calls)
            await self.context_manager.append_assistant_message(
                self.conn,
                turn=self._turn,
                content=result.content,
                tool_calls=result.tool_calls,
            )

            # 遍历执行 tool_calls
            for tc in result.tool_calls:
                tool_name = tc["function"]["name"]
                tool_call_id = tc["id"]
                args_str = tc["function"].get("arguments", "{}")
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}

                # 产出 tool_call event
                await self._emit_event(
                    "tool_call",
                    payload={
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
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
                    return

                # 执行工具
                tool_result = await tool_def.handler(args)

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

                # 持久化 tool message
                await self.context_manager.append_tool_message(
                    self.conn,
                    turn=self._turn,
                    tool_call_id=tool_call_id,
                    content=tool_result.output,
                    name=tool_name,
                )

            # ACTING → OBSERVING → THINKING(循环)
            self._transition(ReactLoopState.OBSERVING)
            self._transition(ReactLoopState.THINKING)

            # 更新 messages 用于下一次 adapter 调用
            messages = self.context_manager.get_messages()
