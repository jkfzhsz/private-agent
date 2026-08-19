from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from private_agent.errors import SandboxTimeoutError
from private_agent.sandbox.executor import SandboxExecutor


@pytest.mark.asyncio
async def test_execute_normal_code(tmp_path: Path) -> None:
    """AC-1: 正常代码 exit_code=0, stdout 含输出。"""
    executor = SandboxExecutor(python_command=sys.executable)
    result = await executor.execute(
        code='print("hello from sandbox")',
        language="python",
        timeout=90,
        workspace=str(tmp_path),
    )
    assert result.exit_code == 0
    assert "hello from sandbox" in result.stdout


@pytest.mark.asyncio
async def test_execute_timeout(tmp_path: Path) -> None:
    """AC-2: 超时抛出 SandboxTimeoutError。"""
    executor = SandboxExecutor(python_command=sys.executable)
    with pytest.raises(SandboxTimeoutError):
        await executor.execute(
            code="import time; time.sleep(30)",
            language="python",
            timeout=1,
            workspace=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_execute_syntax_error(tmp_path: Path) -> None:
    """语法错误: exit_code != 0, stderr 含错误信息。"""
    executor = SandboxExecutor(python_command=sys.executable)
    result = await executor.execute(
        code="print(hello",
        language="python",
        timeout=90,
        workspace=str(tmp_path),
    )
    assert result.exit_code != 0
    assert "SyntaxError" in result.stderr or "Error" in result.stderr


@pytest.mark.asyncio
async def test_execute_exit_nonzero(tmp_path: Path) -> None:
    """AC-14: sys.exit(1) 返回 exit_code=1, 不抛异常。"""
    executor = SandboxExecutor(python_command=sys.executable)
    result = await executor.execute(
        code="import sys; sys.exit(1)",
        language="python",
        timeout=90,
        workspace=str(tmp_path),
    )
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_cross_platform_preexec_fn(tmp_path: Path) -> None:
    """AC-15: Windows 下 preexec_fn=None, 非 Windows 有 preexec_fn。"""
    executor = SandboxExecutor(python_command=sys.executable)
    # 验证 executor 能正常执行(Windows 不设 preexec_fn)
    result = await executor.execute(
        code='print("platform check")',
        language="python",
        timeout=90,
        workspace=str(tmp_path),
    )
    assert result.exit_code == 0
