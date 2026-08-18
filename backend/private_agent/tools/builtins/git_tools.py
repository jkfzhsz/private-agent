"""阶段2(agent-upgrader 设计文档 §2.1): git 工具族 —— 无涯版本管理。

工具清单:
- git_status: 查看工作区状态(status --short) —— safe, 只读
- git_diff:   查看改动差异(diff HEAD, 可限定路径) —— safe, 只读
- git_commit: 提交改动(git add + commit) —— elevated, WS 确认(影响 git 历史)

设计要点:
- cwd = 会话工作区(PA 源码根, 由 ReactLoop 服务端强制注入 workspace 参数)
- 输出截断(避免大 diff/status 刷爆上下文): 截断 + 提示用 git_diff 限定路径
- 提交信息必填; 禁止 --force/--amend/--push(本地提交, 不触碰远端)
- git 命令走 subprocess, 超时兜底(大仓库 status 可能慢)
"""
from __future__ import annotations

import asyncio
import logging
import os

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "GIT_STATUS_TOOL",
    "GIT_DIFF_TOOL",
    "GIT_COMMIT_TOOL",
    "GIT_TOOLS",
    "_git_status_handler",
    "_git_diff_handler",
    "_git_commit_handler",
]

# 输出截断上限(≈ 4k token)
_MAX_OUTPUT_CHARS = 4000 * 4


def _run_git(
    args_list: list[str],
    cwd: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """同步执行 git 命令(隔离子进程)。"""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args_list],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"git 命令超时({timeout}s)"
    except FileNotFoundError:
        return -1, "", "git 未安装或不在 PATH"
    except Exception as e:  # noqa: BLE001
        return -1, "", f"git 执行异常: {type(e).__name__}: {e}"


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return (
        text[:_MAX_OUTPUT_CHARS]
        + f"\n…(输出已截断 {len(text) - _MAX_OUTPUT_CHARS} 字符, "
          "可用 git_diff 限定路径查看明细)"
    )


def _workspace(args: dict) -> str | None:
    ws = args.get("workspace") or args.get("data_dir")
    if not ws:
        return None
    return os.path.expandvars(str(ws))


async def _git_status_handler(args: dict) -> ToolResult:
    """查看工作区 git 状态(status --short)。"""
    ws = _workspace(args)
    if not ws:
        return ToolResult(output="", error="workspace required(git 工具需会话工作区)")
    rc, out, err = await asyncio.to_thread(
        _run_git, ["status", "--short", "--branch"], ws,
    )
    if rc != 0:
        return ToolResult(output="", error=f"git status 失败: {err.strip() or out.strip()}")
    return ToolResult(output=_truncate(out.strip() or "(工作区无改动)"))


async def _git_diff_handler(args: dict) -> ToolResult:
    """查看改动差异(diff HEAD, 可限定路径)。"""
    ws = _workspace(args)
    if not ws:
        return ToolResult(output="", error="workspace required(git 工具需会话工作区)")
    path = str(args.get("path") or "").strip()
    cmd = ["diff", "HEAD", "--stat"]
    if path:
        cmd = ["diff", "HEAD", "--", path]
    rc, out, err = await asyncio.to_thread(_run_git, cmd, ws)
    if rc != 0:
        return ToolResult(output="", error=f"git diff 失败: {err.strip() or out.strip()}")
    return ToolResult(output=_truncate(out.strip() or "(无改动)"))


async def _git_commit_handler(args: dict) -> ToolResult:
    """提交改动(git add 指定路径 + commit)。elevated 需用户确认。

    Args:
        message: 提交信息(必填)。
        path: 待提交路径(默认 "." 全部改动)。
    """
    ws = _workspace(args)
    if not ws:
        return ToolResult(output="", error="workspace required(git 工具需会话工作区)")
    message = str(args.get("message") or "").strip()
    if not message:
        return ToolResult(output="", error="message required(提交信息必填)")
    path = str(args.get("path") or ".").strip()
    # 先 add
    rc, out, err = await asyncio.to_thread(_run_git, ["add", "--", path], ws)
    if rc != 0:
        return ToolResult(output="", error=f"git add 失败: {err.strip() or out.strip()}")
    # commit(禁止 force/amend/push)
    rc, out, err = await asyncio.to_thread(
        _run_git, ["commit", "-m", message], ws,
    )
    if rc != 0:
        return ToolResult(output="", error=f"git commit 失败: {err.strip() or out.strip()}")
    return ToolResult(output=f"已提交:\n{out.strip()}")


GIT_STATUS_TOOL = ToolDef(
    name="git_status",
    description=(
        "查看 PA 源码仓库工作区状态(git status --short --branch)。"
        "用于了解当前未提交改动/分支。只读, 自动执行。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "会话工作区(PA 源码根), 服务端自动注入",
            },
        },
    },
    handler=_git_status_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)

GIT_DIFF_TOOL = ToolDef(
    name="git_diff",
    description=(
        "查看 PA 源码仓库改动差异(git diff HEAD)。可用 path 限定单文件/目录, "
        "避免大 diff 刷爆上下文。只读, 自动执行。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "限定路径(文件或目录, 可选)",
            },
            "workspace": {
                "type": "string",
                "description": "会话工作区(PA 源码根), 服务端自动注入",
            },
        },
    },
    handler=_git_diff_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)

GIT_COMMIT_TOOL = ToolDef(
    name="git_commit",
    description=(
        "提交 PA 源码改动(git add + commit, 本地提交不推送)。"
        "会触发权限确认。提交信息必填; 禁止 force/amend/push。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "提交信息(必填, 如 'feat(xxx): 改动描述')",
            },
            "path": {
                "type": "string",
                "description": "待提交路径(默认 . 全部改动)",
            },
            "workspace": {
                "type": "string",
                "description": "会话工作区(PA 源码根), 服务端自动注入",
            },
        },
        "required": ["message"],
    },
    handler=_git_commit_handler,
    is_kernel=False,
    safety_level="elevated",  # 影响 git 历史, WS 60s 确认
    risk_level="medium",
)

GIT_TOOLS: list[ToolDef] = [GIT_STATUS_TOOL, GIT_DIFF_TOOL, GIT_COMMIT_TOOL]
