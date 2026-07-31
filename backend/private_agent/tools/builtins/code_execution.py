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
              session_id(会话 ID,可选)的 dict。

    Returns:
        ToolResult 包含执行 stdout/stderr/exit_code。
    """
    code: str = args.get("code", "")
    timeout: int | None = args.get("timeout")
    session_id: str = args.get("session_id", "")

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
            code=code, language="python", timeout=timeout, session_id=session_id,
        )
        output = (
            f"Exit code: {result.exit_code}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        ).strip()
        if result.generated_files:
            output += f"\nGenerated files: {json.dumps(result.generated_files)}"
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
    description="Execute Python code in an isolated sandbox subprocess. "
    "Supports timeout control, security scanning, and result capture. "
    "Use for running user-provided Python scripts safely.",
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
        },
        "required": ["code"],
    },
    handler=code_execution_handler,
)