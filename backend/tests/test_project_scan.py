"""阶段3(agent-upgrader 设计文档 §2.2 能力域②): project_scan 外部项目评估。

覆盖:
- 技术栈识别(清单文件/入口特征)
- 目录树 + 噪音目录排除
- 源码规模统计(文件/行数, 按扩展名, 超限截断)
- 依赖清单摘要
- 错误路径(path 缺失/目录不存在)
- 权限分级(safe)
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

from private_agent.tools.builtins.project_scan import (
    PROJECT_SCAN_TOOL,
    _project_scan_handler,
)


def _make_project(tmp: str, with_manifest: bool = True) -> None:
    """造一个模拟外部项目(结构/源码/依赖)。"""
    root = Path(tmp)
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text(
        "def main():\n    print('hello')\n", encoding="utf-8"
    )
    (root / "src" / "utils.py").write_text(
        "import os\nx = 1\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Demo Project\n", encoding="utf-8")
    if with_manifest:
        (root / "requirements.txt").write_text(
            "fastapi==0.111\nuvicorn==0.30\n", encoding="utf-8"
        )
    # 噪音目录(应被排除)
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("junk", encoding="utf-8")
    (root / ".git").mkdir()


async def _run(path: str):
    return await _project_scan_handler({"path": path})


def test_project_scan_tech_stack_detection():
    """技术栈识别: requirements.txt + main.py 入口。"""
    with tempfile.TemporaryDirectory() as tmp:
        _make_project(tmp)
        result = asyncio.run(_run(tmp))
        assert result.error is None, result.error
        out = result.output or ""
        assert "Python" in out  # requirements.txt → Python


def test_project_scan_stats_and_noise_exclusion():
    """规模统计: .py 文件数/行数正确; node_modules/.git 不进入目录树。"""
    with tempfile.TemporaryDirectory() as tmp:
        _make_project(tmp)
        result = asyncio.run(_run(tmp))
        out = result.output or ""
        # .py 统计: 2 个文件, 4 行(main.py 2 + utils.py 2)
        assert '"files": 2' in out
        assert '"lines": 4' in out
        # 噪音目录排除
        assert "node_modules" not in out
        assert ".git" not in out


def test_project_scan_no_manifest():
    """无清单文件: 技术栈未识别到常见清单。"""
    with tempfile.TemporaryDirectory() as tmp:
        _make_project(tmp, with_manifest=False)
        result = asyncio.run(_run(tmp))
        assert result.error is None, result.error
        assert "未识别到常见清单" in (result.output or "")


def test_project_scan_missing_path():
    """path 缺失 → 报错。"""
    async def _run_missing():
        return await _project_scan_handler({})

    result = asyncio.run(_run_missing())
    assert result.error is not None
    assert "path required" in result.error


def test_project_scan_dir_not_exists():
    """目录不存在 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(_run(str(Path(tmp) / "nope")))
        assert result.error is not None
        assert "目录不存在" in result.error


def test_project_scan_safety_level():
    """权限分级: safe。"""
    assert PROJECT_SCAN_TOOL.safety_level == "safe"


def test_project_scan_empty_dir():
    """空目录 → 报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(_run(tmp))
        assert result.error is not None
        assert "目录为空" in result.error


def test_project_scan_relative_path_with_workspace():
    """相对路径 + workspace 解析。"""
    with tempfile.TemporaryDirectory() as tmp:
        _make_project(tmp)
        sub = Path(tmp) / "sub"
        sub.mkdir()
        (sub / "index.js").write_text("console.log(1)", encoding="utf-8")
        result = asyncio.run(_project_scan_handler({
            "path": "sub", "workspace": tmp,
        }))
        assert result.error is None, result.error
        assert "JavaScript" in (result.output or "") or "index.js" in (result.output or "")
