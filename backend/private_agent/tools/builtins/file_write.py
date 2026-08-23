"""file_write 内置工具:限制 PA_DATA_DIR 内写入文件。

安全约束:
- 强制校验路径在 PA_DATA_DIR 范围内
- 自动创建父目录
"""
from __future__ import annotations

import os

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["file_write_handler", "FILE_WRITE_TOOL"]


async def file_write_handler(args: dict) -> ToolResult:
    """写入内容到指定文件。

    Args:
        args: 包含 path(文件路径)、content(写入内容)和 data_dir(安全目录)的 dict。

    Returns:
        成功或错误信息。
    """
    filepath = args.get("path", "")
    content = args.get("content", "")
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
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(output=f"Written {len(content)} bytes to {resolved}")
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {resolved}")
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


FILE_WRITE_TOOL = ToolDef(
    name="file_write",
    description="Write content to a file on the local filesystem. Path must be within the allowed data directory.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file.",
            },
            "data_dir": {
                "type": "string",
                "description": "Allowed data directory root for security check.",
            },
        },
        "required": ["path", "content"],
    },
    handler=file_write_handler,
)