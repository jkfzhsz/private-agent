"""read_artifact 内置工具:本地 artifact 读取。

限制在 workspace/.claude/artifacts/ 目录内读取。
"""
from __future__ import annotations

import os

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["read_artifact_handler", "READ_ARTIFACT_TOOL"]


async def read_artifact_handler(args: dict) -> ToolResult:
    """读取本地 artifact 文件。

    Args:
        args: 包含 path(artifact 路径)和 workspace(工作区根目录)的 dict。

    Returns:
        artifact 内容或错误信息。
    """
    filepath = args.get("path", "")
    workspace = args.get("workspace", "")

    if not filepath:
        return ToolResult(output="", error="No path provided")

    resolved = os.path.abspath(filepath)

    # 安全限制:必须在 workspace/.claude/artifacts/ 目录内
    if workspace:
        artifacts_dir = os.path.abspath(os.path.join(workspace, ".claude", "artifacts"))
        if not resolved.startswith(artifacts_dir + os.sep) and resolved != artifacts_dir:
            return ToolResult(
                output="",
                error=f"Access denied: '{resolved}' is outside the artifacts directory '{artifacts_dir}'",
            )

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
        # 标记来源
        rel_path = os.path.relpath(resolved, workspace) if workspace else resolved
        return ToolResult(output=f"--- {rel_path} ---\n{content}")
    except FileNotFoundError:
        return ToolResult(output="", error=f"Artifact not found: {resolved}")
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {resolved}")
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


READ_ARTIFACT_TOOL = ToolDef(
    name="read_artifact",
    description="Read an artifact file from the .claude/artifacts/ directory within the workspace.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the artifact file.",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace root directory for security check.",
            },
        },
        "required": ["path"],
    },
    handler=read_artifact_handler,
)