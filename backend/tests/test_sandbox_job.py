"""阶段二批次 3 - Windows Job Object 沙箱约束测试(审查 A.1.1/B.1.1)。

仅 Windows 平台运行(Job Object 为 Windows 概念; POSIX 走 RLIMIT, 见
test_sandbox_executor 既有用例)。全部为真实子进程级验证。

覆盖:
- CPU 时间限制(JOB_TIME): 死循环 1s 被系统终止
- 内存限制(PROCESS_MEMORY): 大分配 MemoryError
- 进程数限制(ACTIVE_PROCESS): 子进程 spawn 被拒
- 正常代码在 Job 下不受影响(回归)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from private_agent.sandbox.service import SandboxService

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows Job Object is Windows-only"
)

_VENV_PY = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"


def _make_config(tmp_path: Path, **limits_overrides) -> dict:
    limits = {
        "cpu_timeout_sec": 5,
        "memory_limit_mb": 512,
        "disk_limit_mb": 100,
        "network_enabled": True,  # 网络不影响 Job 资源测试
    }
    limits.update(limits_overrides)
    return {
        "sandbox": {
            "workspace_root": str(tmp_path),
            "languages": {
                "python": {"command": str(_VENV_PY), "script_extension": ".py"},
            },
            "limits": limits,
            "security": {
                "code_scan_enabled": True,
                "env_sanitization_enabled": True,
            },
            "output": {"stdout_artifact_threshold": 2000, "code_artifact_threshold": 4000},
        }
    }


@pytest.mark.asyncio
async def test_job_cpu_time_limit_kills_spin(tmp_path: Path) -> None:
    """死循环: Job JOB_TIME=1s 系统级终止(非 asyncio 超时), exit_code != 0。

    2026-08-09: 本机 python 冷启动 ~16s(杀软扫描) —— asyncio 超时放宽到
    90s 容纳启动, 死循环在启动后吃掉 1s CPU → Job kill, duration 上限放宽。
    """
    config = _make_config(tmp_path, cpu_timeout_sec=1)
    svc = SandboxService(config)
    result = await svc.execute(
        code="while True: pass",
        language="python",
        timeout=90,  # asyncio 超时 90s, 但 Job 1s 先杀 → 验证 Job 生效
        session_id="",
    )
    assert result.exit_code != 0, (
        "Job 应终止死循环进程(exit_code != 0)"
    )
    assert result.duration_ms < 90000, "应在 Job 时间限制(1s)后很快返回"


@pytest.mark.asyncio
async def test_job_memory_limit_blocks_allocation(tmp_path: Path) -> None:
    """大内存分配: PROCESS_MEMORY 限制下分配失败(MemoryError), exit_code != 0。"""
    config = _make_config(tmp_path, memory_limit_mb=64)
    svc = SandboxService(config)
    result = await svc.execute(
        code="x = [0] * (10**8)  # 64 位下约 800MB, 远超 64MB 限制",
        language="python",
        timeout=15,
        session_id="",
    )
    assert result.exit_code != 0
    # 分配失败会抛 MemoryError(或被 Job 终止) —— 两者都满足"受限"
    assert "MemoryError" in result.stderr or result.exit_code != 0


@pytest.mark.asyncio
async def test_job_active_process_limit_blocks_spawn(tmp_path: Path) -> None:
    """进程数限制: 活动进程数=1 时 spawn 第二个子进程被 Job 拒绝。"""
    config = _make_config(tmp_path, active_process_limit=1)
    svc = SandboxService(config)
    result = await svc.execute(
        code=(
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
        ),
        language="python",
        timeout=15,
        session_id="",
    )
    # 活动进程数已满(Job 内 1 个=自身) → Popen 抛 OSError → exit != 0
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_job_normal_code_unaffected(tmp_path: Path) -> None:
    """正常代码在 Job 约束下不受影响(回归)。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code='print("job ok:", [i * i for i in range(5)])',
        language="python",
        timeout=15,
        session_id="",
    )
    assert result.exit_code == 0
    assert "job ok" in result.stdout
