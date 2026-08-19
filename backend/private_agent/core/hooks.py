"""阶段三批次 2(B-1, 调研 round2 §4.2.2) - Hooks 生命周期系统。

参考 Claude Code 29 事件 + Pi/OpenClaw Hooks 语义, 收敛为 PA 六事件:
- user_prompt_submit: 用户消息提交 → 可拒/改
- pre_tool_use: 工具执行前 → permissionDecision(allow/deny/ask/defer)
- post_tool_use: 工具执行后 → additionalContext 注入 / 强制校验
- stop: 收尾前 → 可阻止过早收尾
- pre_compact: 压缩前 → 关键信息 flush(OpenClaw compaction flush 借鉴)
- permission_request: 权限确认请求 → 外部策略接管(如企业合规 hook)

三类 hook 实现:
- command: 子进程调用(输入 JSON 走 stdin, 解析 stdout JSON; 复用 sandbox executor 子进程模式)
- http: 回调 URL(强制过 security/ssrf.py 校验, 复用阶段二 SSRF 防护)
- mcp_tool: MCP 工具调用(经注入的 mcp_call 回调, 复用 MCP client)

安全语义:
- 默认空列表 → 行为不变(零回归)
- hook 失败(超时/异常)默认放行(hook 是增强不是门禁)
- permissionDecision=deny 的结果是终局(阻断)
- 执行日志审计进 results(调用方落 react_events)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "HOOK_EVENTS",
    "HOOK_TYPES",
    "HookConfig",
    "HookDecision",
    "HookRunner",
]

# 支持的事件与类型
HOOK_EVENTS = (
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "stop",
    "pre_compact",
    "permission_request",
)
HOOK_TYPES = ("command", "http", "mcp_tool")

# 默认超时(秒)与失败语义
DEFAULT_HOOK_TIMEOUT = 5.0
# Claude Code 语义: 退出码 2 = 阻断并回喂 stderr; 0 = 解析 stdout JSON; 其他 = 非阻塞警告
EXIT_BLOCK = 2

# 可回写的决策字段(Claude Code hooks 输出协议子集)
DECISION_FIELDS = ("permissionDecision", "updatedInput", "additionalContext", "stop")


@dataclass
class HookConfig:
    """Hook 配置(来自 config.yaml hooks[] 或 admin CRUD)。

    Args:
        name: hook 名(唯一, 审计用)。
        event: 订阅事件(见 HOOK_EVENTS)。
        type: 实现类型(command/http/mcp_tool)。
        command: type=command 时子进程命令(列表或字符串, 字符串按 shell 分词)。
        url: type=http 时回调 URL(过 SSRF 校验)。
        mcp_server: type=mcp_tool 时 MCP server 名。
        mcp_tool: type=mcp_tool 时 MCP 工具名。
        timeout: 超时秒数(默认 5)。
        enabled: 是否启用(默认 true)。
    """

    name: str
    event: str
    type: str = "command"
    command: str | None = None
    url: str | None = None
    mcp_server: str | None = None
    mcp_tool: str | None = None
    timeout: float = DEFAULT_HOOK_TIMEOUT
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.event not in HOOK_EVENTS:
            raise ValueError(
                f"invalid hook event: {self.event!r} (expected {list(HOOK_EVENTS)})"
            )
        if self.type not in HOOK_TYPES:
            raise ValueError(
                f"invalid hook type: {self.type!r} (expected {list(HOOK_TYPES)})"
            )
        if self.type == "command" and not self.command:
            raise ValueError("command hook requires 'command'")
        if self.type == "http" and not self.url:
            raise ValueError("http hook requires 'url'")
        if self.type == "mcp_tool" and (not self.mcp_server or not self.mcp_tool):
            raise ValueError("mcp_tool hook requires 'mcp_server' and 'mcp_tool'")


@dataclass
class HookDecision:
    """一次事件 dispatch 的聚合决策(供 ReactLoop 消费)。"""

    permission_decision: str | None = None  # allow | deny | ask | defer
    updated_input: dict | None = None  # 替换工具/用户输入参数
    additional_context: str | None = None  # 注入上下文
    stop: bool = False  # stop 事件返回 stop=true 时阻止收尾
    results: list[dict] = field(default_factory=list)  # 每个 hook 执行结果(审计)

    def merge(self, other: "HookDecision") -> None:
        """合并子决策(deny 优先, 后 hook 的 updatedInput/additionalContext 覆盖)。"""
        if other.permission_decision:
            # deny 优先于一切; ask 高于 allow
            if other.permission_decision == "deny":
                self.permission_decision = "deny"
            elif other.permission_decision == "ask" and self.permission_decision != "deny":
                self.permission_decision = "ask"
            elif self.permission_decision is None:
                self.permission_decision = other.permission_decision
        if other.updated_input is not None:
            self.updated_input = other.updated_input
        if other.additional_context is not None:
            self.additional_context = other.additional_context
        self.stop = self.stop or other.stop
        self.results.extend(other.results)


class HookRunner:
    """Hook 调度执行器(事件 → 按序执行启用 hooks → 聚合决策)。

    Args:
        hooks: HookConfig 列表(默认空 = 零回归)。
        http_post: 可选 http POST 回调(默认内部 SafeHttpxClient, 测试可注入)。
        mcp_call: 可选 MCP 工具调用回调(async (server, tool, args) -> dict),
                  type=mcp_tool 时必需(由 main.py 注入)。
        loop: 可选事件循环(单测隔离用)。
    """

    def __init__(
        self,
        hooks: list[HookConfig] | None = None,
        http_post: Callable[..., Awaitable[dict]] | None = None,
        mcp_call: Callable[..., Awaitable[dict]] | None = None,
    ) -> None:
        self._hooks = list(hooks) if hooks else []
        self._http_post = http_post
        self._mcp_call = mcp_call

    @property
    def hooks(self) -> list[HookConfig]:
        return list(self._hooks)

    def set_hooks(self, hooks: list[HookConfig] | None) -> None:
        self._hooks = list(hooks) if hooks else []

    async def dispatch(self, event: str, payload: dict) -> HookDecision:
        """派发事件到所有启用的该事件 hook, 聚合决策。

        Args:
            event: 事件名(见 HOOK_EVENTS)。
            payload: 事件负载(含 session_id/call_id 等, 传给 hook 输入)。

        Returns:
            HookDecision(无匹配 hook 时为空决策 = 行为不变)。
        """
        decision = HookDecision()
        for hook in self._hooks:
            if not hook.enabled or hook.event != event:
                continue
            result = await self._run_one(hook, payload)
            decision.results.append(result)
            if result.get("permissionDecision") or result.get("updatedInput") or result.get("additionalContext") or result.get("stop"):
                decision.merge(self._from_result(result))
        return decision

    async def _run_one(self, hook: HookConfig, payload: dict) -> dict:
        """执行单个 hook, 返回结果 dict(失败放行 + 超时兜底)。

        Returns:
            dict: 解析自 hook 输出的 JSON(含 permissionDecision 等), 失败时含 error 字段。
        """
        input_json = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            if hook.type == "command":
                return await self._run_command(hook, input_json)
            if hook.type == "http":
                return await self._run_http(hook, input_json)
            if hook.type == "mcp_tool":
                return await self._run_mcp(hook, payload)
        except asyncio.TimeoutError:
            logger.warning("hook %s timed out (%.1fs), pass-through", hook.name, hook.timeout)
            return {"name": hook.name, "error": "timeout", "passed": True}
        except Exception as e:  # noqa: BLE001 - hook 失败放行
            logger.warning("hook %s failed (%s), pass-through", hook.name, e)
            return {"name": hook.name, "error": f"{type(e).__name__}: {e}", "passed": True}
        return {"name": hook.name, "error": "unknown", "passed": True}

    async def _run_command(self, hook: HookConfig, input_json: str) -> dict:
        """command hook: 子进程执行, 输入走 stdin, 解析 stdout JSON。

        退出码语义(Claude Code): 0=解析 stdout; 2=阻断(stderr 回喂);
        其他=非阻塞警告。Windows 无 preexec_fn, 用 asyncio 超时 + kill。
        """
        proc = await asyncio.create_subprocess_exec(
            *hook.command.split(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_json.encode("utf-8")),
                timeout=hook.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise
        exit_code = proc.returncode
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        result: dict = {"name": hook.name, "exit_code": exit_code}
        if exit_code == 0 and out_text:
            try:
                parsed = json.loads(out_text)
                if isinstance(parsed, dict):
                    result.update(parsed)
            except json.JSONDecodeError:
                result["error"] = f"non-json stdout: {out_text[:200]}"
        elif exit_code == EXIT_BLOCK:
            result["error"] = f"blocked by hook: {err_text[:200]}"
            result["permissionDecision"] = "deny"
        elif exit_code != 0:
            result["error"] = f"exit {exit_code}: {err_text[:200]}"
        return result

    async def _run_http(self, hook: HookConfig, input_json: str) -> dict:
        """http hook: POST JSON 到回调 URL(过 SSRF 校验)。"""
        if self._http_post is None:
            from private_agent.security.ssrf import safe_httpx_client

            client = safe_httpx_client()
            try:
                resp = await client.post(
                    hook.url,
                    content=input_json,
                    headers={"Content-Type": "application/json"},
                    timeout=hook.timeout,
                )
                text = resp.text
                result: dict = {"name": hook.name, "status": resp.status_code}
                if resp.status_code < 300:
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            result.update(parsed)
                    except json.JSONDecodeError:
                        result["error"] = "non-json response"
                else:
                    result["error"] = f"http {resp.status_code}: {text[:200]}"
                return result
            finally:
                await client.aclose()
        return await self._http_post(hook.url, input_json, hook.timeout)

    async def _run_mcp(self, hook: HookConfig, payload: dict) -> dict:
        """mcp_tool hook: 调用 MCP 工具(经注入回调)。"""
        if self._mcp_call is None:
            return {"name": hook.name, "error": "mcp_call not injected", "passed": True}
        out = await self._mcp_call(hook.mcp_server, hook.mcp_tool, payload)
        if isinstance(out, dict):
            return {"name": hook.name, **out}
        return {"name": hook.name, "result": out}

    @staticmethod
    def _from_result(result: dict) -> HookDecision:
        """把单 hook 结果 dict 转成 HookDecision(仅取可回写字段)。"""
        d = HookDecision()
        pd = result.get("permissionDecision")
        if pd in ("allow", "deny", "ask", "defer"):
            d.permission_decision = pd
        ui = result.get("updatedInput")
        if isinstance(ui, dict):
            d.updated_input = ui
        ac = result.get("additionalContext")
        if isinstance(ac, str) and ac:
            d.additional_context = ac
        if result.get("stop") is True:
            d.stop = True
        return d

    @staticmethod
    def config_from_dict(d: dict) -> HookConfig:
        """从配置 dict 构造 HookConfig(admin CRUD / yaml 解析用)。"""
        return HookConfig(
            name=str(d.get("name", "")),
            event=str(d.get("event", "")),
            type=str(d.get("type", "command")),
            command=d.get("command"),
            url=d.get("url"),
            mcp_server=d.get("mcp_server"),
            mcp_tool=d.get("mcp_tool"),
            timeout=float(d.get("timeout", DEFAULT_HOOK_TIMEOUT)),
            enabled=bool(d.get("enabled", True)),
        )

    @staticmethod
    def configs_from_list(items: list[dict] | None) -> list[HookConfig]:
        """从 yaml/runtime 列表解析 hooks 配置(空/None → 空列表)。"""
        if not items:
            return []
        return [HookRunner.config_from_dict(i) for i in items]
