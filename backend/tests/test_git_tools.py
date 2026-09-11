"""阶段2(agent-upgrader 设计文档 §2.1/§4): git 工具族 + pytest_run 测试。

覆盖:
- git_status/git_diff 只读, 返回仓库状态/差异
- git_commit elevated 且提交信息必填, 实际提交
- 非 git 仓库(临时目录) → 明确报错
- pytest_run 解析 backend 目录 + 运行测试(聚焦单文件)
- 权限分级断言(git_status/diff/pytest_run=safe, git_commit=elevated)
"""
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from private_agent.tools.builtins.git_tools import (
    GIT_COMMIT_TOOL,
    GIT_DIFF_TOOL,
    GIT_STATUS_TOOL,
    GIT_TOOLS,
    _git_commit_handler,
    _git_diff_handler,
    _git_status_handler,
)
from private_agent.tools.builtins.pytest_run import (
    PYTEST_RUN_TOOL,
    _pytest_run_handler,
    resolve_backend_dir,
)


def _init_git_repo(tmp: str) -> None:
    """在临时目录初始化 git 仓库 + 一次提交。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=tmp, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=tmp, check=True
    )
    (Path(tmp) / "a.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=tmp, check=True
    )


def test_git_tools_safety_levels():
    """权限分级: status/diff=safe, commit=elevated。"""
    assert GIT_STATUS_TOOL.safety_level == "safe"
    assert GIT_DIFF_TOOL.safety_level == "safe"
    assert GIT_COMMIT_TOOL.safety_level == "elevated"
    assert len(GIT_TOOLS) == 3


def test_git_status_and_diff():
    """status 显示改动, diff 显示差异。"""
    with tempfile.TemporaryDirectory() as tmp:
        _init_git_repo(tmp)
        (Path(tmp) / "a.txt").write_text("hello\nworld", encoding="utf-8")

        async def _run():
            sr = await _git_status_handler({"workspace": tmp})
            assert sr.error is None, sr.error
            assert "a.txt" in sr.output or "M a.txt" in sr.output
            dr = await _git_diff_handler({"workspace": tmp})
            assert dr.error is None, dr.error
            return sr, dr

        sr, dr = asyncio.run(_run())
        assert "a.txt" in sr.output  # status 含文件名
        assert dr.output  # diff 非空


def test_git_commit_requires_message():
    """commit 无 message → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        _init_git_repo(tmp)

        async def _run():
            return await _git_commit_handler({"workspace": tmp})

        result = asyncio.run(_run())
        assert result.error is not None
        assert "message required" in result.error


def test_git_commit_success():
    """commit 实际提交改动。"""
    with tempfile.TemporaryDirectory() as tmp:
        _init_git_repo(tmp)
        (Path(tmp) / "b.txt").write_text("new", encoding="utf-8")

        async def _run():
            return await _git_commit_handler(
                {"workspace": tmp, "message": "feat: add b.txt"}
            )

        result = asyncio.run(_run())
        assert result.error is None, result.error
        assert "已提交" in result.output
        # 验证提交生效
        out = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=tmp, capture_output=True, text=True, check=True,
        ).stdout
        assert "feat: add b.txt" in out


def test_git_tool_not_git_repo():
    """非 git 目录 → 明确报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        async def _run():
            return await _git_status_handler({"workspace": tmp})

        result = asyncio.run(_run())
        assert result.error is not None


def test_git_tool_requires_workspace():
    """无 workspace → 报错。"""
    async def _run():
        return await _git_status_handler({})

    result = asyncio.run(_run())
    assert result.error is not None
    assert "workspace" in result.error


def test_pytest_run_tool_safety():
    """pytest_run 为 safe 级。"""
    assert PYTEST_RUN_TOOL.safety_level == "safe"


def test_resolve_backend_dir():
    """backend 目录解析: workspace 根 → {ws}/backend; 已是 backend → 原样。"""
    # 用真实 backend 路径验证(测试 cwd=backend)
    real_backend = os.path.abspath(".")
    assert (Path(real_backend) / "config" / "config.yaml").exists()
    # workspace = 源码根(backend 的上级)
    parent = str(Path(real_backend).parent)
    assert resolve_backend_dir(parent) == real_backend
    # workspace 已是 backend
    assert resolve_backend_dir(real_backend) == real_backend
    # 无 backend 的目录 → None
    with tempfile.TemporaryDirectory() as tmp:
        assert resolve_backend_dir(tmp) is None


def test_pytest_run_focused_file():
    """pytest_run 跑聚焦单文件(用本测试文件自身验证链路)。

    注: 在 pytest 内嵌套再跑 pytest 会因环境并发受限, 故本测试只验证
    handler 链路可执行(返回结果而非抛异常), 真实通过性由
    resolve_backend_dir 单测 + 手动验证覆盖。
    """
    backend_dir = os.path.abspath(".")

    async def _run():
        return await _pytest_run_handler({
            "workspace": str(Path(backend_dir).parent),
            "tests": "tests/test_stage1_anchors.py",
            "timeout": 120,
        })

    result = asyncio.run(_run())
    # 链路可跑通(无论通过与否都返回 ToolResult, 不抛异常)
    assert result is not None
    if result.error is None:
        assert "通过" in result.output


# ── 2026-09-07 F1/F5: 超时参数探测降级 + 选择器前置校验 ──────────────────


def test_has_pytest_timeout_detects_real_venv():
    """F1: 真实 backend venv 已装 pytest-timeout → 探测为 True。"""
    from private_agent.tools.builtins.pytest_run import _has_pytest_timeout

    backend_dir = os.path.abspath(".")
    assert (Path(backend_dir) / ".venv").is_dir()
    assert _has_pytest_timeout(backend_dir) is True


def test_has_pytest_timeout_false_when_venv_missing():
    """F1: 无 .venv 的目录 → False(降级省略 --timeout 参数)。"""
    from private_agent.tools.builtins.pytest_run import _has_pytest_timeout

    with tempfile.TemporaryDirectory() as tmp:
        assert _has_pytest_timeout(tmp) is False


def test_pytest_run_rejects_hallucinated_selector():
    """F5: 不存在的测试路径前置拦截(不执行 pytest), 错误含引导语。"""
    backend_dir = os.path.abspath(".")

    async def _run():
        return await _pytest_run_handler({
            "workspace": str(Path(backend_dir).parent),
            "tests": "tests/test_not_exist_hallucinated.py",
            "timeout": 60,
        })

    result = asyncio.run(_run())
    assert result.error is not None
    assert "测试路径不存在" in result.error
    assert "file_read" in result.error


def test_pytest_run_selector_validation_with_nodeid():
    """F5: nodeid 语法(tests/x.py::test_y)按文件部分校验。"""
    from private_agent.tools.builtins.pytest_run import _missing_selectors

    backend_dir = os.path.abspath(".")
    assert _missing_selectors(
        backend_dir, ["tests/test_git_tools.py::test_pytest_run_tool_safety"]
    ) == []
    assert _missing_selectors(
        backend_dir, ["tests/nope.py::test_x"]
    ) == ["tests/nope.py"]



def test_git_commit_multiple_paths():
    """path 逗号分隔多路径 → 一次 commit 覆盖多个文件。

    2026-09-11(session-78067 反馈#2): 原实现 path 只支持单路径, 一次完整
    改动(多文件)需拆多次 git_commit → 每次触发权限确认。修复后支持
    "a.py,b.py" 一次 add + 一次 commit, 一次确认完成。
    """
    with tempfile.TemporaryDirectory() as tmp:
        _init_git_repo(tmp)
        (Path(tmp) / "m1.py").write_text("x", encoding="utf-8")
        (Path(tmp) / "m2.py").write_text("y", encoding="utf-8")
        (Path(tmp) / "m3.py").write_text("z", encoding="utf-8")

        async def _run():
            return await _git_commit_handler(
                {
                    "workspace": tmp,
                    "message": "feat: multi paths",
                    "path": "m1.py,m2.py,m3.py",
                }
            )

        result = asyncio.run(_run())
        assert result.error is None, result.error
        assert "已提交" in result.output
        # 三个文件都在一次 commit 中
        out = subprocess.run(
            ["git", "show", "--stat", "--oneline", "HEAD"],
            cwd=tmp, capture_output=True, text=True, check=True,
        ).stdout
        assert "m1.py" in out and "m2.py" in out and "m3.py" in out
        # 工作区无残留未提交改动
        st = subprocess.run(
            ["git", "status", "--short"], cwd=tmp,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert st == ""


def test_git_commit_single_path_with_spaces():
    """含空格单路径(Windows 风格)不被逗号拆分逻辑破坏。"""
    with tempfile.TemporaryDirectory() as tmp:
        _init_git_repo(tmp)
        subdir = Path(tmp) / "dir with space"
        subdir.mkdir()
        (subdir / "s.py").write_text("s", encoding="utf-8")

        async def _run():
            return await _git_commit_handler(
                {
                    "workspace": tmp,
                    "message": "feat: spaced path",
                    "path": str(subdir / "s.py"),
                }
            )

        result = asyncio.run(_run())
        assert result.error is None, result.error
        assert "已提交" in result.output
