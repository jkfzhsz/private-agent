"""V2 P1 - 工具权限确认管理器(蓝图 §5.12 运行时链路)。

三级权限:
- safe / none: 自动执行,不打断 Agent
- elevated: WS 推送确认请求 → 等待用户响应(默认 60s 超时自动拒绝) → 会话级缓存
- dangerous: 直接拦截,不入队

阶段三批次 1(B-2/B-3/B-4, 调研 round2 §4.2.1):
- 规则求值层: 可选 rules(list[PermissionRule]) 优先于 safety_level 决策,
  规则未命中 → 回退 safety_level 默认路径(100% 兼容)。
- 权限模式 mode: default/plan/acceptEdits/cautious/deny_all 五个预置档,
  是 OpenClaw security×ask×askFallback 矩阵的命名封装(避免引入新概念层)。

确认缓存:
- key = get_permission_cache_key("default", tool_name, args) 按 (session_id, cache_key) 隔离
- 同会话内,首次确认后相同工具 + 相同参数组合自动放行(无论通过还是拒绝)
- 不同参数组合需重新确认
- cautious 模式禁用缓存(每次都确认); 权限模式切换时调用 clear_session 清缓存

线程模型:
- check_and_confirm 内部 await 一个 asyncio.Future,由 WS 收到 tool_confirmation
  消息后调用 resolve(confirmation_id, approved) 唤醒。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable

from private_agent.tools.permission import (
    PermissionRule,
    evaluate_rules,
    get_permission_cache_key,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PermissionManager",
    "PERMISSION_MODES",
    "PERMISSION_MODE_DEFAULTS",
]

# 权限模式预置档(OpenClaw 预置档命名封装, 见模块 docstring)
PERMISSION_MODES = ("default", "plan", "acceptEdits", "cautious", "deny_all")
# 各模式语义: 覆盖默认 safety_level 判定规则(由 check_and_confirm 消费)
PERMISSION_MODE_DEFAULTS = {
    # plan: 只读放行, 写工具全部确认(跳过缓存, 每次询问)
    "plan": {"auto": {"none", "safe"}, "ask": {"elevated"}, "blocked": {"dangerous"}},
    # acceptEdits: 文件类 elevated 自动批准, 其余 elevated 走确认
    "acceptEdits": {"auto_extra": {"file_write", "file_read", "read_artifact"}},
    # cautious: 同 default 但确认结果不缓存(每次都询问)
    "cautious": {},
    # deny_all: 全部拦截(OpenClaw deny-all 语义)
    "deny_all": {"blocked": {"none", "safe", "elevated", "dangerous"}},
}


class PermissionManager:
    """蓝图 §5.12 权限确认管理器。

    Args:
        timeout: 确认等待超时秒数(默认 60,测试可传小值)。
        rules: 规则求值层输入(可选, None 时纯 safety_level 语义)。
        mode: 权限模式(default/plan/acceptEdits/cautious/deny_all)。
    """

    def __init__(
        self,
        timeout: float = 60.0,
        rules: list[PermissionRule] | None = None,
        mode: str = "default",
        defer_timeout: float = 600.0,
    ) -> None:
        self._timeout = timeout
        # 阶段三批次4(B-14): defer 后继续等待的时长(默认 10 分钟)
        self._defer_timeout = defer_timeout
        self._rules = list(rules) if rules else []
        if mode not in PERMISSION_MODES:
            raise ValueError(
                f"invalid permission mode: {mode!r} (expected {list(PERMISSION_MODES)})"
            )
        self._mode = mode
        # (session_id, cache_key) -> approved
        self._cache: dict[tuple[int, str], bool] = {}
        # confirmation_id -> asyncio.Future[bool](pending 确认)
        self._pending: dict[str, asyncio.Future] = {}
        # 阶段三批次4(B-14): 被用户"稍后决定"挂起的 confirmation_id 集合
        self._deferred: set[str] = set()

    # ── 配置接口 ────────────────────────────────────────────────────────────

    def set_rules(self, rules: list[PermissionRule] | None) -> None:
        """更新规则集(会话级/Skill 激活时调用)。"""
        self._rules = list(rules) if rules else []

    def set_mode(self, mode: str) -> None:
        """切换权限模式并清空确认缓存(模式变化后旧缓存不再适用)。"""
        if mode not in PERMISSION_MODES:
            raise ValueError(
                f"invalid permission mode: {mode!r} (expected {list(PERMISSION_MODES)})"
            )
        self._mode = mode
        self._cache.clear()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def rules(self) -> list[PermissionRule]:
        return list(self._rules)

    # ── 决策主流程 ──────────────────────────────────────────────────────────

    async def check_and_confirm(
        self,
        session_id: int,
        tool_def,
        args: dict,
        emit_fn: Callable[[dict], Awaitable[None]],
    ) -> str:
        """检查并确认工具执行权限(蓝图 §5.12)。

        决策顺序(阶段三批次 1):
        1. 权限模式 deny_all → "blocked"(全局拦截);
        2. 规则求值层: deny → "blocked"; allow → "auto"; ask → 强制确认;
           (规则命中时安全默认仍生效: ask 走 elevated 通道, 60s 超时拒绝)
        3. 规则未命中 → 回退 mode + safety_level 默认路径。

        Args:
            session_id: 会话 ID。
            tool_def: ToolDef(含 safety_level)。
            args: 工具调用参数。
            emit_fn: 推送确认请求事件的回调(async (event: dict) -> None)。

        Returns:
            outcome 字符串:
            - "auto": 自动放行(safe/规则 allow/acceptEdits 文件类)
            - "blocked": 拦截(dangerous/规则 deny/deny_all)
            - "approved": elevated 用户确认通过
            - "denied": elevated 用户拒绝
            - "timeout": elevated 等待超时(默认 60s)
        """
        level = getattr(tool_def, "safety_level", "none")

        # ── 1. deny_all 模式: 全局拦截 ──
        if self._mode == "deny_all":
            logger.info("deny_all mode blocks tool: %s", tool_def.name)
            return "blocked"

        # ── 2. 规则求值层(deny 优先; 未命中 → None 回退) ──
        if self._rules:
            decision = evaluate_rules(self._rules, tool_def.name, args)
            if decision == "deny":
                logger.info("rule deny blocks tool: %s", tool_def.name)
                return "blocked"
            if decision == "allow":
                return "auto"
            if decision == "ask":
                return await self._do_elevated_confirm(
                    session_id, tool_def, args, emit_fn, force=True
                )

        # ── 3. 回退 mode + safety_level 默认路径 ──
        if level in ("none", "safe"):
            return "auto"
        if level == "dangerous":
            logger.warning("Dangerous tool blocked: %s", tool_def.name)
            return "blocked"
        if level != "elevated":
            return "auto"  # 未知级别按安全处理

        # plan 模式: elevated 工具每次都确认(不缓存)
        if self._mode == "plan":
            return await self._do_elevated_confirm(
                session_id, tool_def, args, emit_fn, force=True
            )

        # acceptEdits 模式: 文件类工具自动批准
        if self._mode == "acceptEdits":
            auto_extra = PERMISSION_MODE_DEFAULTS["acceptEdits"].get(
                "auto_extra", set()
            )
            if tool_def.name in auto_extra:
                return "auto"

        # default/cautious: 标准确认流程(force=False 时默认走缓存)
        return await self._do_elevated_confirm(
            session_id, tool_def, args, emit_fn, force=(self._mode == "cautious")
        )

    async def _do_elevated_confirm(
        self,
        session_id: int,
        tool_def,
        args: dict,
        emit_fn: Callable[[dict], Awaitable[None]],
        *,
        force: bool = False,
    ) -> str:
        """elevated 确认执行(缓存命中 / WS 推送 / 60s 超时拒绝)。

        Args:
            force: True 时跳过会话级缓存(plan/cautious/规则 ask)。
        """
        cache_key = get_permission_cache_key("default", tool_def.name, args)
        key = (session_id, cache_key)
        if not force and key in self._cache:
            return "approved" if self._cache[key] else "denied"

        confirmation_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[confirmation_id] = fut

        # 阶段三批次1(B-8): 风险分级 + 来源解释(可解释决策)
        try:
            from private_agent.tools.defs import assess_risk

            risk_level = assess_risk(tool_def, args)
        except Exception:  # noqa: BLE001 - 评估失败不阻塞确认
            risk_level = getattr(tool_def, "risk_level", "medium")
        reason = self._explain_reason(tool_def, args)

        # 2026-08-15: 确认卡片人性化(蒋先生反馈"英文+术语看不懂, 授权等于盲签")
        # display 生成失败兜底为空, 绝不阻塞确认流程
        try:
            from private_agent.tools.confirmation_display import humanize_confirmation

            display = humanize_confirmation(tool_def.name, args)
        except Exception:  # noqa: BLE001
            display = {"title": tool_def.name, "summary": [], "tool_label": tool_def.name}

        await emit_fn(
            {
                "event_type": "tool_confirmation_required",
                "confirmation_id": confirmation_id,
                "tool_name": tool_def.name,
                "args_summary": self._summarize_args(args),
                "message": f"AI 请求: {display['title']}, 是否允许?",
                "display": display,
                "mode": self._mode,
                "risk_level": risk_level,
                "reason": reason,
            }
        )

        try:
            # shield 保护 fut: wait_for 超时不取消内部 future(否则 defer
            # 后续无法继续等待/resolve —— B-14 关键修复)
            approved = await asyncio.wait_for(
                asyncio.shield(fut), timeout=self._timeout
            )
            outcome = "approved" if approved else "denied"
        except asyncio.TimeoutError:
            # 阶段三批次4(B-14): 60s 超时后若用户已"稍后决定"挂起,
            # 继续等待(defer_timeout), 期间可 resolve; 否则按原 fail-closed 拒绝。
            if confirmation_id in self._deferred:
                try:
                    approved = await asyncio.wait_for(
                        asyncio.shield(fut), timeout=self._defer_timeout
                    )
                    outcome = "approved" if approved else "denied"
                except asyncio.TimeoutError:
                    approved = False
                    outcome = "timeout"
            else:
                approved = False
                outcome = "timeout"
            logger.warning(
                "tool confirmation timeout: session=%s tool=%s deferred=%s",
                session_id, tool_def.name, confirmation_id in self._deferred,
            )
        finally:
            self._pending.pop(confirmation_id, None)
            self._deferred.discard(confirmation_id)

        # 缓存结果(同会话同参数,通过/拒绝都缓存;超时按拒绝缓存)
        if not force:
            self._cache[key] = approved
        return outcome

    def _explain_reason(self, tool_def, args: dict | None = None) -> str:
        """生成确认卡片的来源解释(B-8 + 2026-08-15 通俗化: 少术语)。

        优先级: 命中规则(session/skill/config) > 默认 safety_level。
        """
        args = args or {}
        for rule in self._rules:
            if rule.action in ("ask", "deny") and rule.matches(tool_def.name, args):
                return f"按『{rule.source}』的设定, 这类操作需要你确认"
        level = getattr(tool_def, "safety_level", "none")
        if level == "dangerous":
            return "危险工具默认拦截"
        if level == "elevated":
            if self._mode == "plan":
                return "当前处于『先计划后执行』模式, 所有写操作都需逐次确认"
            if self._mode == "cautious":
                return "当前处于『谨慎』模式, 每次操作都单独确认"
            return "这类操作会改动你的文件或系统设置, 出于安全默认需要你同意"
        return f"安全级别: {level}"

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

    def defer(self, confirmation_id: str) -> bool:
        """阶段三批次4(B-14): 用户"稍后决定"挂起确认。

        标记该确认挂起: 60s 超时后不再立即拒绝, 继续等待 defer_timeout,
        期间用户仍可 resolve; 未挂起(未知/已结束)返回 False。

        Args:
            confirmation_id: 确认 ID。

        Returns:
            True 表示挂起成功;未知 ID 返回 False。
        """
        fut = self._pending.get(confirmation_id)
        if fut is None or fut.done():
            return False
        self._deferred.add(confirmation_id)
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
