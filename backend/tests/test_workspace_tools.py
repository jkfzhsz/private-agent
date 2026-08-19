"""C-1 工作区文件工具族测试(设计文档 next-phase-plan-2026-08-15 §3.3-C1)。

覆盖:
- 路径沙箱: 相对/绝对路径解析 + commonpath 防穿越(../ 拒绝, 盘符拒绝)
- ws_read/ws_write/ws_list/ws_rm 基本功能
- 权限分级: read/list=safe, write=elevated, rm=high 风险
- trash 回收: ws_rm 移入 .trash/ 而非直接删除
- 无 workspace 防御: resolve 返回 error
"""
import asyncio
import os

import pytest

from private_agent.tools.builtins.workspace_tools import (
    WS_LIST_TOOL,
    WS_READ_TOOL,
    WS_RM_TOOL,
    WS_TOOLS,
    WS_WRITE_TOOL,
    resolve_within_workspace,
    _ws_list_handler,
    _ws_read_handler,
    _ws_rm_handler,
    _ws_write_handler,
)


@pytest.fixture
def ws(tmp_path):
    """临时工作区。"""
    return str(tmp_path)


# ── 路径沙箱 ────────────────────────────────────────────────────────────────


def test_resolve_relative_path(ws):
    """相对路径解析到 workspace 内。"""
    abs_path, err = resolve_within_workspace(ws, "docs/report.md")
    assert err is None
    assert abs_path.startswith(ws)
    assert abs_path.endswith("docs" + os.sep + "report.md")


def test_resolve_absolute_inside(ws):
    """绝对路径(workspace 内)放行。"""
    inner = os.path.join(ws, "a.txt")
    abs_path, err = resolve_within_workspace(ws, inner)
    assert err is None
    assert abs_path == os.path.abspath(inner)


def test_resolve_traversal_rejected(ws):
    """../ 穿越拒绝。"""
    _, err = resolve_within_workspace(ws, "../secret.txt")
    assert err is not None
    assert "Path traversal" in err


def test_resolve_absolute_outside_rejected(ws):
    """workspace 外的绝对路径拒绝。"""
    _, err = resolve_within_workspace(ws, os.path.join(os.path.dirname(ws), "x.txt"))
    assert err is not None
    assert "outside" in err or "Path traversal" in err


def test_resolve_empty_workspace_rejected():
    """无 workspace → error(工具应未注册, 此处防御)。"""
    _, err = resolve_within_workspace("", "a.txt")
    assert err is not None
    assert "workspace not configured" in err


def test_resolve_empty_path_rejected(ws):
    """空路径 → error。"""
    _, err = resolve_within_workspace(ws, "")
    assert err is not None


# ── 权限分级 ────────────────────────────────────────────────────────────────


def test_tool_safety_levels():
    """read/write/list=safe(工作区内免确认, 仅审计), rm 保持确认。

    2026-08-16 权限放宽(蒋先生要求): 工作区内读写无需用户确认, 只记录;
    删除类(ws_rm)保持确认(elevated 确认通道 + high 风险标签)。
    """
    assert WS_READ_TOOL.safety_level == "safe"
    assert WS_LIST_TOOL.safety_level == "safe"
    assert WS_WRITE_TOOL.safety_level == "safe"
    # rm: 危险级二次确认(elevated 确认 + high 风险标签; PA dangerous=直接拦截
    # 不可执行, 故采用 elevated 确认通道 + trash 回收双重保护)
    assert WS_RM_TOOL.safety_level == "elevated"
    assert WS_RM_TOOL.risk_level == "high"
    assert WS_WRITE_TOOL.risk_level == "medium"


def test_ws_tools_family_complete():
    """四件套齐全。"""
    names = {t.name for t in WS_TOOLS}
    assert names == {"ws_read", "ws_write", "ws_list", "ws_rm"}


# ── 基本功能 ────────────────────────────────────────────────────────────────


def test_ws_write_and_read_roundtrip(ws):
    """写入 → 读取 往返。"""
    async def _run():
        r = await _ws_write_handler(
            {"path": "notes/todo.md", "content": "1. 完成 harness", "workspace": ws}
        )
        assert r.error is None
        assert "notes" + os.sep + "todo.md" in r.output
        r2 = await _ws_read_handler({"path": "notes/todo.md", "workspace": ws})
        assert r2.error is None
        assert "1. 完成 harness" in r2.output
        return r, r2

    asyncio.run(_run())


def test_ws_write_creates_parent_dirs(ws):
    """自动创建父目录。"""
    async def _run():
        r = await _ws_write_handler(
            {"path": "a/b/c/file.txt", "content": "x", "workspace": ws}
        )
        assert r.error is None
        assert os.path.exists(os.path.join(ws, "a", "b", "c", "file.txt"))

    asyncio.run(_run())


def test_ws_write_traversal_blocked(ws):
    """写入穿越拒绝。"""
    async def _run():
        r = await _ws_write_handler(
            {"path": "../evil.txt", "content": "x", "workspace": ws}
        )
        assert r.error is not None
        assert not os.path.exists(os.path.join(os.path.dirname(ws), "evil.txt"))

    asyncio.run(_run())


def test_ws_list_files_and_dirs(ws):
    """列表: 文件 + 目录 + 递归。"""
    async def _run():
        os.makedirs(os.path.join(ws, "sub"), exist_ok=True)
        with open(os.path.join(ws, "a.txt"), "w", encoding="utf-8") as f:
            f.write("hello")
        with open(os.path.join(ws, "sub", "b.txt"), "w", encoding="utf-8") as f:
            f.write("world")
        r = await _ws_list_handler({"path": ".", "workspace": ws})
        assert r.error is None
        assert "a.txt" in r.output
        assert "sub/" in r.output
        r2 = await _ws_list_handler(
            {"path": ".", "recursive": True, "workspace": ws}
        )
        assert r2.error is None
        assert "sub" + os.sep + "b.txt" in r2.output
        return r, r2

    asyncio.run(_run())


def test_ws_rm_moves_to_trash(ws):
    """删除移入 .trash/ 回收站(可恢复), 不物理删除。"""
    async def _run():
        target = os.path.join(ws, "delme.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("bye")
        r = await _ws_rm_handler({"path": "delme.txt", "workspace": ws})
        assert r.error is None
        assert ".trash" in r.output
        assert not os.path.exists(target)  # 原位置已移除
        # 回收站中存在
        trash = os.path.join(ws, ".trash")
        assert os.path.isdir(trash)
        assert len(os.listdir(trash)) == 1
        return r

    asyncio.run(_run())


def test_ws_rm_missing_file(ws):
    """删除不存在的文件 → 明确错误。"""
    async def _run():
        r = await _ws_rm_handler({"path": "nope.txt", "workspace": ws})
        assert r.error is not None
        assert "Not found" in r.error

    asyncio.run(_run())


def test_ws_rm_traversal_blocked(ws):
    """删除穿越拒绝。"""
    async def _run():
        r = await _ws_rm_handler({"path": "../outside.txt", "workspace": ws})
        assert r.error is not None
        assert "Path traversal" in r.error

    asyncio.run(_run())


def test_ws_read_missing_file(ws):
    """读取不存在的文件 → 明确错误。"""
    async def _run():
        r = await _ws_read_handler({"path": "missing.txt", "workspace": ws})
        assert r.error is not None
        assert "File not found" in r.error

    asyncio.run(_run())
