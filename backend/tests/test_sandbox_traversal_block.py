"""修复方案 B(2026-09-03): 全树递归遍历超大目录必须在**执行前**阻断。

事故背景(session-76): 模型为查 mempalace 版本生成 "4 基目录 x 4 模式" 的
glob(..., recursive=True), 其中基目录含 os.path.expanduser("~") =
C:\\Users\\zongxin (548,728 条目) —— 实测暖缓存 137.55s, 叠加冷缓存惩罚后
越过 300s 墙钟超时, 用户干等 5.5 分钟零输出。

方案 A(description 硬约束)是引导性的, 模型可能无视; 本方案提供强制拦截:
命中即在执行前返回, 把「干等 300s 后超时」变成「秒级失败 + 可操作改写指引」。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from private_agent.sandbox.result import SandboxResult
from private_agent.sandbox.security import CodeScanner
from private_agent.sandbox.service import SandboxService

# session-76 事故代码(精简版, 保留触发结构: 大目录起点 + recursive glob)
INCIDENT_CODE = '''
import os, glob
for base in [r"D:\\mempalace", os.path.expanduser("~")]:
    for pat in ["**/mempalace*/METADATA", "**/mempalace*/PKG-INFO"]:
        for f in glob.glob(os.path.join(base, pat), recursive=True):
            print(f)
'''


def _make_config(tmp_path: Path) -> dict:
    """构造最小 sandbox 配置 dict。"""
    return {
        "sandbox": {
            "workspace_root": str(tmp_path),
            "retention_days": 7,
            "languages": {
                "python": {"command": sys.executable, "script_extension": ".py"},
            },
            "limits": {
                "cpu_timeout_sec": 90,
                "memory_limit_mb": 512,
                "disk_limit_mb": 100,
            },
            "security": {
                "code_scan_enabled": True,
                "env_sanitization_enabled": True,
            },
            "output": {
                "stdout_artifact_threshold": 2000,
                "code_artifact_threshold": 4000,
            },
        }
    }


class TestFindBlockingViolation:
    """CodeScanner 阻断级扫描: 命中 = 全树遍历超大目录。"""

    def test_incident_code_blocked(self) -> None:
        """session-76 事故代码必须被拦下(回归根用例)。"""
        v = CodeScanner().find_blocking_violation(INCIDENT_CODE)
        assert v is not None
        assert "~" in v.snippet

    def test_path_home_with_os_walk_blocked(self) -> None:
        code = 'import os\nfor r, d, f in os.walk(os.path.join(Path.home(), "Downloads")):\n    print(r)\n'
        assert CodeScanner().find_blocking_violation(code) is not None

    def test_drive_root_recursive_glob_blocked(self) -> None:
        code = 'import glob\nprint(glob.glob("C:\\\\" + "**/*.txt", recursive=True))\n'
        assert CodeScanner().find_blocking_violation(code) is not None

    def test_posix_root_walk_blocked(self) -> None:
        code = 'import os\nfor r, d, f in os.walk("/"):\n    print(r)\n'
        assert CodeScanner().find_blocking_violation(code) is not None

    def test_userprofile_env_recursive_glob_blocked(self) -> None:
        code = (
            'import os, glob\n'
            'root = os.environ["USERPROFILE"]\n'
            'print(glob.glob(os.path.join(root, "**/*.log"), recursive=True))\n'
        )
        assert CodeScanner().find_blocking_violation(code) is not None

    def test_narrow_recursive_glob_allowed(self) -> None:
        """窄范围递归 glob 是合法用法, 不得误伤。"""
        code = 'import glob\nprint(glob.glob("src/**/*.py", recursive=True))\n'
        assert CodeScanner().find_blocking_violation(code) is None

    def test_walk_without_huge_root_allowed(self) -> None:
        code = 'import os\nfor r, d, f in os.walk("outputs"):\n    print(r)\n'
        assert CodeScanner().find_blocking_violation(code) is None

    def test_expanduser_without_recursion_allowed(self) -> None:
        """只取主目录路径、不做全树遍历 → 放行(读配置文件等常见场景)。"""
        code = 'import os\np = os.path.join(os.path.expanduser("~"), ".gitconfig")\nprint(open(p).read())\n'
        assert CodeScanner().find_blocking_violation(code) is None

    def test_rglob_huge_root_blocked(self) -> None:
        """Path.home().rglob(...) 同时命中 rglob 与 Path.home() → 必须阻断。"""
        code = 'from pathlib import Path\nprint(list(Path.home().rglob("*.txt")))\n'
        assert CodeScanner().find_blocking_violation(code) is not None

    def test_javascript_not_blocked(self) -> None:
        """JS 不走 Python 遍历语义, 不套用该规则(避免误伤)。"""
        code = 'const path = require("path"); const home = process.env.HOME;'
        assert CodeScanner().find_blocking_violation(code, language="javascript") is None


class TestServiceBlocksBeforeExecution:
    """service 层: 命中阻断规则时不得进入 executor。"""

    @pytest.mark.asyncio
    async def test_blocked_code_never_reaches_executor(self, tmp_path: Path) -> None:
        """强证据: executor.execute 必须一次都没被调用。"""
        svc = SandboxService(_make_config(tmp_path))
        svc._executor.execute = AsyncMock(return_value=SandboxResult(
            stdout="SHOULD NOT RUN", stderr="", exit_code=0, duration_ms=0,
        ))
        result = await svc.execute(
            code=INCIDENT_CODE, language="python", session_id="block-test",
        )
        svc._executor.execute.assert_not_called()
        assert result.exit_code == -1
        assert result.stdout == ""
        # 必须给出可操作的替代方案, 而不只是报错
        assert "importlib.metadata" in result.stderr
        assert result.warnings, "阻断项应作为 warning 记录, 便于事后审计"

    @pytest.mark.asyncio
    async def test_blocked_is_fast(self, tmp_path: Path) -> None:
        """阻断必须是秒级, 不能让用户再等一个 300s 周期。"""
        import time

        svc = SandboxService(_make_config(tmp_path))
        t0 = time.monotonic()
        result = await svc.execute(
            code=INCIDENT_CODE, language="python", session_id="block-fast",
        )
        elapsed = time.monotonic() - t0
        assert result.exit_code == -1
        assert elapsed < 5.0, f"阻断耗时 {elapsed:.2f}s, 未达到秒级失败"

    @pytest.mark.asyncio
    async def test_clean_code_still_executes(self, tmp_path: Path) -> None:
        """回归: 正常代码不受影响。"""
        svc = SandboxService(_make_config(tmp_path))
        result = await svc.execute(
            code='print("hello")', language="python", session_id="clean-test",
        )
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.warnings == []
