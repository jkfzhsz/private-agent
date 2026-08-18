"""code_execution 内置工具:沙箱隔离执行 Python 代码(蓝图 §5.x / spec m2-sandbox AC-11)。

通过 SandboxService 编排子进程隔离执行,支持超时控制、安全扫描、事件记录。
"""
from __future__ import annotations

import json

from private_agent.sandbox.service import SandboxService
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["code_execution_handler", "CODE_EXECUTION_TOOL", "set_sandbox_config"]

# 模块级配置(应用启动时由 main.py 设置)
_sandbox_config: dict | None = None


def set_sandbox_config(config: dict) -> None:
    """设置沙箱全局配置(应用启动时调用)。"""
    global _sandbox_config
    _sandbox_config = config


async def code_execution_handler(args: dict) -> ToolResult:
    """执行 Python 代码(沙箱隔离)。

    Args:
        args: 包含 code(代码), timeout(超时秒数,可选,默认 300),
              session_id(会话 ID,可选), network(联网执行,可选,默认 False),
              _on_output(流式输出回调,可选),
              _sandbox_config(测试用配置注入)的 dict。

    Returns:
        ToolResult 包含执行 stdout/stderr/exit_code。
    """
    code: str = args.get("code", "")
    timeout: int | None = args.get("timeout")
    session_id: str = args.get("session_id", "")
    # 0.5.1: 显式联网放行(绕过沙箱代理隔离)。
    # 安全边界: code_execution 为 elevated 工具, 联网执行同样经过权限确认,
    # 用户在确认弹窗可见"联网"语义。仅 LLM 显式声明 network=true 才放行。
    allow_network: bool = bool(args.get("network", False))
    # 2026-08-15: 会话工作区 env 对齐(ReactLoop 注入的内部参数, 不进模型
    # schema) —— 沙箱子进程 WORKSPACE 环境变量覆盖为会话选定工作区,
    # 避免模型在代码里读到全局 backend 路径把产物写错目录。
    workspace_env: str | None = args.get("_workspace_env")
    # 流式输出回调(由 react_loop 注入): (stream_type, chunk) -> Awaitable[None]
    on_output = args.get("_on_output")

    if not code:
        return ToolResult(output="", error="No code provided")

    config = _sandbox_config
    if config is None:
        # 回退:从 args 读取 sandbox_config(用于测试直接调用)
        config = args.get("_sandbox_config")
    if config is None:
        return ToolResult(output="", error="Sandbox not configured")

    try:
        svc = SandboxService(config)
        result = await svc.execute(
            code=code, language="python", timeout=timeout,
            session_id=session_id, on_output=on_output,
            allow_network=allow_network,
            workspace_env=workspace_env,
        )
        output = (
            f"Exit code: {result.exit_code}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        ).strip()
        if result.generated_files:
            output += f"\nGenerated files: {json.dumps(result.generated_files)}"
            # 2026-08-16(问题1-C): 产物已同步到会话工作区, 提示模型可
            # file_read 读取(sync_dir 在会话工作区内闭环)
            if result.sync_dir:
                output += (
                    f"\n产物已同步到工作区: {result.sync_dir}"
                    " (可用 file_read 读取)"
                )
        if result.warnings:
            warnings_str = "; ".join(
                f"line {w.line}: {w.snippet}" for w in result.warnings
            )
            output += f"\nWarnings: {warnings_str}"
        return ToolResult(output=output)
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


CODE_EXECUTION_TOOL = ToolDef(
    name="code_execution",
    description=(
        "Execute Python code in an isolated sandbox subprocess. Supports timeout "
        "control, security scanning, and result capture. Use for running "
        "user-provided Python scripts safely. "
        "Windows 提示: 调用外部命令(docker/ps 等)时输出可能是 GBK 中文, "
        "subprocess 请用 encoding='gbk' 或 errors='replace', 避免解码崩溃。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds (default: 300).",
            },
            "session_id": {
                "type": "string",
                "description": "Session ID for workspace isolation.",
            },
            "network": {
                "type": "boolean",
                "description": (
                    "Set true ONLY when the code must access the network "
                    "(e.g. calling external APIs). Bypasses the sandbox "
                    "proxy isolation; execution still requires user "
                    "confirmation. Default false."
                ),
            },
        },
        "required": ["code"],
    },
    handler=code_execution_handler,
    # 2026-08-16(蒋先生要求"工作区内读写免确认, 仅记录"): code_execution 在
    # 沙箱内隔离执行(代码预扫描 + 网络隔离 + 资源限制 + Windows Job Object
    # 多重防护, 不触碰用户文件系统) → 降为 safe 自动执行, 仅审计记录
    # (tool_call 事件含 code + 参数已落库)。原 elevated(每次 60s 确认)在
    # 长任务中频繁打断, 且用户已确认沙箱为可信执行边界。
    safety_level="safe",
)