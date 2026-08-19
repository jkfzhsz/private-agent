"""C-1 工作区文件工具族(设计文档 next-phase-plan-2026-08-15 §3.3-C1)。

工具清单:
- ws_read:  读取会话工作区内文件(safe)
- ws_write: 写入会话工作区内文件(elevated, WS 60s 确认 + 会话缓存)
- ws_list:  列出会话工作区目录/文件(safe)
- ws_rm:    删除会话工作区内文件/目录(危险级: elevated 确认 + high 风险
            标签 + trash 回收 —— 不直接删除, 移入 .trash/ 可恢复)

安全约束(路径沙箱):
- 所有路径解析后必须落于 workspace root 内(os.path.commonpath 校验, 防穿越)
- 无 workspace 的会话: 工具不注册(注册层判断, 见 main.py 装配), 零回归
- ws_rm 默认移入回收站 {workspace}/.trash/ 而非直接删除(文件系统安全基线)

权限语义(2026-08-15 蒋先生确认沿用 file_write 语义):
- ws_write = elevated(WS 60s 确认, 会话缓存复用)
- ws_rm = 危险级二次确认: safety_level=elevated + risk_level=high
  (PA 的 dangerous 语义=直接拦截不可执行, 而设计文档要求"删除操作二次确认
  + trash 回收"; 采用 elevated 确认通道 + high 风险标签满足"危险级二次确认",
  确认卡片明示高风险, 且 handler 移入回收站而非物理删除 —— 双重保护)
- ws_read/ws_list = safe(自动执行)

handler 依赖注入: args 中的 workspace(由 ReactLoop 服务端强制注入,
与 file_read/file_write 同机制 —— 模型省略该字段也由服务端补全)。
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = [
    "WS_READ_TOOL",
    "WS_WRITE_TOOL",
    "WS_LIST_TOOL",
    "WS_RM_TOOL",
    "WS_TOOLS",
    "resolve_within_workspace",
    "_ws_read_handler",
    "_ws_write_handler",
    "_ws_list_handler",
    "_ws_rm_handler",
]


def resolve_within_workspace(workspace: str, rel_or_abs: str) -> tuple[str, str | None]:
    """路径沙箱解析: 返回 (绝对路径, error)。

    规则:
    - workspace 为空 → error(工具应在无 workspace 会话不注册, 此处防御)
    - rel_or_abs 为空 → error
    - 解析后必须落于 workspace root 内(commonpath 校验, 防 ../ 穿越)
    - 支持 ~ 展开 / 绝对路径(限 workspace 内) / 相对路径(相对 workspace)

    Returns:
        (resolved_abs_path, None) 或 ("", error_message)。
    """
    if not workspace:
        return "", "workspace not configured for this session"
    if not rel_or_abs:
        return "", "No path provided"
    ws_root = os.path.abspath(os.path.expanduser(os.path.expandvars(workspace)))
    raw = os.path.expanduser(os.path.expandvars(str(rel_or_abs)))
    if not os.path.isabs(raw):
        raw = os.path.join(ws_root, raw)
    resolved = os.path.abspath(raw)
    # commonpath 校验: 解析后必须落在 workspace root 内
    try:
        common = os.path.commonpath([ws_root, resolved])
    except ValueError:  # 不同盘符 → 必然越界
        return "", f"Path traversal detected: '{resolved}' is outside workspace '{ws_root}'"
    if common != ws_root:
        return "", f"Path traversal detected: '{resolved}' is outside workspace '{ws_root}'"
    return resolved, None


async def _ws_read_handler(args: dict) -> ToolResult:
    """读取会话工作区内文件。"""
    workspace = args.get("workspace", "")
    filepath, err = resolve_within_workspace(workspace, args.get("path", ""))
    if err:
        return ToolResult(output="", error=err)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        rel = os.path.relpath(filepath, workspace)
        return ToolResult(output=f"--- {rel} ---\n{content}")
    except FileNotFoundError:
        return ToolResult(output="", error=f"File not found: {filepath}")
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {filepath}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


async def _ws_write_handler(args: dict) -> ToolResult:
    """写入会话工作区内文件(自动创建父目录)。"""
    workspace = args.get("workspace", "")
    filepath, err = resolve_within_workspace(workspace, args.get("path", ""))
    if err:
        return ToolResult(output="", error=err)
    content = args.get("content", "")
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(filepath, workspace)
        return ToolResult(
            output=f"Written {len(content)} bytes to {rel}",
            metadata={"path": rel, "bytes": len(content)},
        )
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {filepath}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


async def _ws_list_handler(args: dict) -> ToolResult:
    """列出会话工作区目录/文件(递归可选)。"""
    workspace = args.get("workspace", "")
    dirpath, err = resolve_within_workspace(workspace, args.get("path", "."))
    if err:
        return ToolResult(output="", error=err)
    recursive = bool(args.get("recursive", False))
    try:
        if not os.path.isdir(dirpath):
            return ToolResult(output="", error=f"Not a directory: {dirpath}")
        lines: list[str] = []
        base = os.path.abspath(workspace)
        if recursive:
            for root, dirs, files in os.walk(dirpath):
                # 跳过回收站
                dirs[:] = [d for d in dirs if d != ".trash"]
                rel_root = os.path.relpath(root, base)
                for name in sorted(files):
                    fp = os.path.join(root, name)
                    size = os.path.getsize(fp)
                    lines.append(f"{os.path.join(rel_root, name)}\t{size}B")
        else:
            for name in sorted(os.listdir(dirpath)):
                fp = os.path.join(dirpath, name)
                if os.path.isdir(fp):
                    lines.append(f"{name}/\t(dir)")
                else:
                    size = os.path.getsize(fp)
                    lines.append(f"{name}\t{size}B")
        return ToolResult(
            output="\n".join(lines) if lines else "(空目录)",
            metadata={"count": len(lines), "recursive": recursive},
        )
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {dirpath}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


async def _ws_rm_handler(args: dict) -> ToolResult:
    """删除会话工作区内文件/目录(移入回收站 .trash/ 而非直接删除)。"""
    workspace = args.get("workspace", "")
    target, err = resolve_within_workspace(workspace, args.get("path", ""))
    if err:
        return ToolResult(output="", error=err)
    try:
        if not os.path.exists(target):
            return ToolResult(output="", error=f"Not found: {target}")
        # 回收站目录: {workspace}/.trash/{yyyy-mm-dd}/
        ws_root = os.path.abspath(os.path.expanduser(os.path.expandvars(workspace)))
        trash_dir = os.path.join(
            ws_root, ".trash", datetime.now().strftime("%Y-%m-%d")
        )
        os.makedirs(trash_dir, exist_ok=True)
        # 同名冲突 → 追加时间戳
        rel = os.path.relpath(target, ws_root)
        dest = os.path.join(trash_dir, os.path.basename(target))
        if os.path.exists(dest):
            ts = datetime.now().strftime("%H%M%S")
            dest = os.path.join(trash_dir, f"{os.path.basename(target)}.{ts}")
        shutil.move(target, dest)
        return ToolResult(
            output=(
                f"Moved '{rel}' to trash: {os.path.relpath(dest, ws_root)}"
                " (回收站, 可在 .trash/ 恢复)"
            ),
            metadata={"trashed_to": os.path.relpath(dest, ws_root)},
        )
    except PermissionError:
        return ToolResult(output="", error=f"Permission denied: {target}")
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


# ── ToolDef 定义 ────────────────────────────────────────────────────────────

WS_READ_TOOL = ToolDef(
    name="ws_read",
    description=(
        "Read a file inside the session workspace. Path is resolved relative to "
        "the workspace root and must stay within it (path traversal blocked)."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, relative to workspace root or absolute within it.",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace root (injected by server, do not modify).",
            },
        },
        "required": ["path"],
    },
    handler=_ws_read_handler,
    safety_level="safe",
)

WS_WRITE_TOOL = ToolDef(
    name="ws_write",
    description=(
        "Write content to a file inside the session workspace (creates parent "
        "directories). Path must stay within the workspace root."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, relative to workspace root or absolute within it.",
            },
            "content": {
                "type": "string",
                "description": "Content to write.",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace root (injected by server, do not modify).",
            },
        },
        "required": ["path", "content"],
    },
    handler=_ws_write_handler,
    # 2026-08-16 权限放宽(蒋先生要求): ws_write 限定工作区内写入 → safe
    # 免确认, 仅审计记录(tool_call 事件含 tool_name+args 已落库)。
    safety_level="safe",
    risk_level="medium",
)

WS_LIST_TOOL = ToolDef(
    name="ws_list",
    description=(
        "List files/directories inside the session workspace. Use recursive=true "
        "to walk subdirectories. Excludes the .trash recycle bin."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list, relative to workspace root (default '.').",
            },
            "recursive": {
                "type": "boolean",
                "description": "Recursively list subdirectories (default false).",
                "default": False,
            },
            "workspace": {
                "type": "string",
                "description": "Workspace root (injected by server, do not modify).",
            },
        },
        "required": [],
    },
    handler=_ws_list_handler,
    safety_level="safe",
)

WS_RM_TOOL = ToolDef(
    name="ws_rm",
    description=(
        "Remove a file or directory inside the session workspace. Items are moved "
        "to the workspace .trash/ recycle bin (recoverable), not permanently deleted."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to remove, relative to workspace root or absolute within it.",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace root (injected by server, do not modify).",
            },
        },
        "required": ["path"],
    },
    handler=_ws_rm_handler,
    safety_level="elevated",
    risk_level="high",
)

WS_TOOLS = [WS_READ_TOOL, WS_WRITE_TOOL, WS_LIST_TOOL, WS_RM_TOOL]
