"""V2 P1 - 工具权限确认管理器(蓝图 §5.12 运行时链路)。

三级权限:
- safe / none: 自动执行,不打断 Agent
- elevated: WS 推送确认请求 → 等待用户响应(默认 60s 超时自动拒绝) → 会话级缓存
- dangerous: 直接拦截,不入队

确认缓存:
- key = get_permission_cache_key("default", tool_name, args) 按 (session_id, cache_key) 隔离
- 同会话内,首次确认后相同工具 + 相同参数组合自动放行(无论通过还是拒绝)
- 不同参数组合需重新确认

线程模型:
- check_and_confirm 内部 await 一个 asyncio.Future,由 WS 收到 tool_confirmation
  消息后调用 resolve(confirmation_id, approved) 唤醒。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable

from private_agent.tools.permission import get_permission_cache_key

logger = logging.getLogger(__name__)

__all__ = ["PermissionManager"]


class PermissionManager:
    """蓝图 §5.12 权限确认管理器。

    Args:
        timeout: 确认等待超时秒数(默认 60,测试可传小值)。
    """

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout
        # (session_id, cache_key) -> approved
        self._cache: dict[tuple[int, str], bool] = {}
        # confirmation_id -> asyncio.Future[bool](pending 确认)
        self._pending: dict[str, asyncio.Future] = {}

    async def check_and_confirm(
        self,
        session_id: int,
        tool_def,
        args: dict,
        emit_fn: Callable[[dict], Awaitable[None]],
    ) -> str:
        """检查并确认工具执行权限(蓝图 §5.12)。

        Args:
            session_id: 会话 ID。
            tool_def: ToolDef(含 safety_level)。
            args: 工具调用参数。
            emit_fn: 推送确认请求事件的回调(async (event: dict) -> None)。

        Returns:
            outcome 字符串:
            - "auto": safe/none 自动放行
            - "blocked": dangerous 拦截
            - "approved": elevated 用户确认通过
            - "denied": elevated 用户拒绝
            - "timeout": elevated 等待超时(默认 60s)
        """
        level = getattr(tool_def, "safety_level", "none")
        if level in ("none", "safe"):
            return "auto"
        if level == "dangerous":
            logger.warning("Dangerous tool blocked: %s", tool_def.name)
            return "blocked"
        if level != "elevated":
            return "auto"  # 未知级别按安全处理

        cache_key = get_permission_cache_key("default", tool_def.name, args)
        key = (session_id, cache_key)
        if key in self._cache:
            return "approved" if self._cache[key] else "denied"

        confirmation_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[confirmation_id] = fut

        await emit_fn(
            {
                "event_type": "tool_confirmation_required",
                "confirmation_id": confirmation_id,
                "tool_name": tool_def.name,
                "args_summary": self._summarize_args(args),
                "message": f"Allow tool '{tool_def.name}' to execute?",
            }
        )

        try:
            approved = await asyncio.wait_for(fut, timeout=self._timeout)
            outcome = "approved" if approved else "denied"
        except asyncio.TimeoutError:
            approved = False
            outcome = "timeout"
            logger.warning(
                "tool confirmation timeout: session=%s tool=%s",
                session_id, tool_def.name,
            )
        finally:
            self._pending.pop(confirmation_id, None)

        # 缓存结果(同会话同参数,通过/拒绝都缓存;超时按拒绝缓存)
        self._cache[key] = approved
        return outcome

    def resolve(self, confirmation_id: str, approved: bool) -> bool:
        """WS 收到用户确认后唤醒等待(蓝图 §5.12 用户点击后执行)。

        Args:
            confirmation_id: 确认 ID。
            approved: 用户是否同意。

        Returns:
            True 表示成功唤醒了一个等待者;未知/重复 ID 返回 False。
        """
        fut = self._pending.get(confirmation_id)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True

    def clear_session(self, session_id: int) -> None:
        """会话结束清空该会话缓存(蓝图 §5.12: 会话结束清空缓存)。"""
        keys = [k for k in self._cache if k[0] == session_id]
        for k in keys:
            del self._cache[k]

    @staticmethod
    def _summarize_args(args: dict, limit: int = 200) -> dict:
        """截断参数摘要(大 code 块只保留开头,避免确认卡片过大)。"""
        summary: dict = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > limit:
                summary[k] = v[:limit] + f"...({len(v)} chars)"
            else:
                summary[k] = v
        return summary
