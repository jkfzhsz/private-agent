"""file_read 内置工具:限制 PA_DATA_DIR 内读取文件。

安全约束:
- 强制校验路径在 PA_DATA_DIR 范围内
- 防止路径穿越攻击
"""
from __future__ import annotations

import os

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["file_read_handler", "FILE_READ_TOOL"]


async def file_read_handler(args: dict) -> ToolResult:
    """读取指定文件内容。

    Args:
        args: 包含 path(文件路径)和 data_dir(安全目录)的 dict。

    Returns:
        文件内容或错误信息。
    """
    filepath = args.get("path", "")
    data_dir = args.get("data_dir", "")

    if not filepath:
        return ToolResult(output="", error="No path provided")

    resolved = os.path.abspath(filepath)
    if data_dir:
        safe_dir = os.path.abspath(data_dir)
        if not resolved.startswith(safe_dir + os.sep) and resolved != safe_dir:
            return ToolResult(
                output="",
                error=f"Path traversal detected: '{resolved}' is outside data directory '{safe_dir}'",
            )

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
        return ToolResult(output=content)
    except FileNotFoundError:
        return ToolResult(output="", error=f"File not found: {resolved}")
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {resolved}")
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


FILE_READ_TOOL = ToolDef(
    name="file_read",
    description="Read the content of a file on the local filesystem. Path must be within the allowed data directory.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "data_dir": {
                "type": "string",
                "description": "Allowed data directory root for security check.",
            },
        },
        "required": ["path"],
    },
    handler=file_read_handler,
)