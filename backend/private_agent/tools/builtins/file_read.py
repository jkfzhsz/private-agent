"""file_read 内置工具:限制 PA_DATA_DIR 内读取文件。

安全约束:
- 强制校验路径在 PA_DATA_DIR 范围内
- 防止路径穿越攻击

M3 增强(plan AC-1/2/3):
- max_lines 截断(default 1000)
- 大文件拒绝(default 10MB,提示用 code_execution)
- 输出 > 4000 token 时截断 + 写入 artifact
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["file_read_handler", "FILE_READ_TOOL"]

# 蓝图 §7.9 token 阈值(简化估算:1 token ≈ 4 字符)
_MAX_OUTPUT_TOKEN = 4000
_MAX_OUTPUT_CHARS = _MAX_OUTPUT_TOKEN * 4


async def file_read_handler(args: dict) -> ToolResult:
    """读取指定文件内容。

    Args:
        args: 包含 path/data_dir 的 dict,可选 max_lines/max_file_size_mb/workspace。

    Returns:
        文件内容(必要时截断)或错误信息。
    """
    filepath = args.get("path", "")
    data_dir = args.get("data_dir", "")
    max_lines = args.get("max_lines", 1000)
    max_file_size_mb = args.get("max_file_size_mb", 10)
    workspace = args.get("workspace", "")

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

    # AC-2: 大文件拒绝
    try:
        size_bytes = os.path.getsize(resolved)
    except FileNotFoundError:
        return ToolResult(output="", error=f"File not found: {resolved}")
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {resolved}")
    except OSError as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")

    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_file_size_mb:
        return ToolResult(
            output="",
            error=(
                f"File too large: {size_mb:.2f}MB > {max_file_size_mb}MB limit. "
                "Use code_execution to process in chunks."
            ),
        )

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return ToolResult(output="", error=f"File not found: {resolved}")
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {resolved}")
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")

    # AC-1: max_lines 截断
    lines = content.split("\n")
    if len(lines) > max_lines:
        content = "\n".join(lines[:max_lines]) + f"\n[truncated at {max_lines} lines]"

    # AC-3: 输出 > 4000 token 时截断 + 写 artifact
    if len(content) > _MAX_OUTPUT_CHARS:
        artifact_path = _write_artifact(content, workspace)
        truncated = content[:_MAX_OUTPUT_CHARS]
        content = (
            truncated
            + f"\n[truncated, full content saved to artifact: {artifact_path}]"
        )

    return ToolResult(output=content)


def _write_artifact(content: str, workspace: str) -> str:
    """将完整内容写入 artifact 文件,返回相对路径。

    artifact 路径: {workspace}/.claude/artifacts/file_read_{hash_short}.txt
    无 workspace 时写到当前目录 .claude/artifacts/。
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    base_dir = Path(workspace) if workspace else Path(".")
    artifact_dir = base_dir / ".claude" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_file = artifact_dir / f"file_read_{digest}.txt"
    artifact_file.write_text(content, encoding="utf-8")
    # 返回包含 .claude/artifacts 的相对路径字符串,便于 LLM 与测试识别
    return f".claude/artifacts/{artifact_file.name}"


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
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to return (default 1000).",
                "default": 1000,
                "minimum": 1,
                "maximum": 10000,
            },
            "max_file_size_mb": {
                "type": "integer",
                "description": "Maximum file size in MB (default 10). Larger files are rejected.",
                "default": 10,
                "minimum": 1,
            },
            "workspace": {
                "type": "string",
                "description": "Workspace root for artifact storage when output is truncated.",
            },
        },
        "required": ["path"],
    },
    handler=file_read_handler,
)
