"""Test sandbox/service.py - SandboxService 编排(AC-8, AC-9, AC-10, AC-14)。

SandboxService 工作流: workspace→scan→sanitize→execute→scan_files→log_event→artifact。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from private_agent.sandbox.result import CodeWarning, SandboxResult
from private_agent.sandbox.service import SandboxService


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


@pytest.mark.asyncio
async def test_execute_end_to_end(tmp_path: Path) -> None:
    """AC-8: 端到端执行简单 Python 代码,返回 SandboxResult 且字段完整。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code='print("hello from service")',
        language="python",
        session_id="e2e-test",
    )
    assert isinstance(result, SandboxResult)
    assert result.exit_code == 0
    assert "hello from service" in result.stdout
    assert result.stderr == ""
    assert result.duration_ms > 0
    assert isinstance(result.warnings, list)
    assert isinstance(result.generated_files, list)


@pytest.mark.asyncio
async def test_execute_with_timeout_override(tmp_path: Path) -> None:
    """AC-8: timeout 参数可覆盖配置默认值。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code="import time; time.sleep(0.1)",
        language="python",
        timeout=30,
        session_id="timeout-override",
    )
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_stdout_artifact(tmp_path: Path) -> None:
    """AC-9: stdout 超阈值时截断,artifact 文件存在。

    阈值 2000 token ≈ 8000 字符(len//4)。
    """
    config = _make_config(tmp_path)
    config["sandbox"]["output"]["stdout_artifact_threshold"] = 10  # 极小阈值
    svc = SandboxService(config)
    # 打印约 200 字符,远超 10 token
    result = await svc.execute(
        code="print('x' * 200)",
        language="python",
        session_id="artifact-test",
    )
    assert "[stdout truncated" in result.stdout
    # artifact 文件应存在于工作目录
    workspace = tmp_path / ".sandbox" / "artifact-test"
    artifacts = list((workspace / "artifacts").glob("stdout_*.txt"))
    assert len(artifacts) >= 1


@pytest.mark.asyncio
async def test_stdout_artifact_disabled(tmp_path: Path) -> None:
    """AC-9: 阈值 <=0 时不截断。"""
    config = _make_config(tmp_path)
    config["sandbox"]["output"]["stdout_artifact_threshold"] = 0
    svc = SandboxService(config)
    result = await svc.execute(
        code="print('hello')",
        language="python",
        session_id="artifact-disabled",
    )
    assert "hello" in result.stdout
    assert "[stdout truncated" not in result.stdout


@pytest.mark.asyncio
async def test_event_logging(tmp_path: Path) -> None:
    """AC-10: 提供 conn 时,执行事件写入 react_events 表。"""
    mock_conn = AsyncMock()
    config = _make_config(tmp_path)
    svc = SandboxService(config, conn=mock_conn)
    await svc.execute(
        code='print("event test")',
        language="python",
        session_id="42",
    )
    # 验证 conn.execute 被调用(INSERT INTO react_events)
    assert mock_conn.execute.called
    call_args = mock_conn.execute.call_args
    assert call_args is not None
    sql = call_args[0][0]
    assert "INSERT INTO react_events" in sql
    assert "sandbox_execution" in sql


@pytest.mark.asyncio
async def test_event_logging_no_conn(tmp_path: Path) -> None:
    """AC-10: conn=None 时不抛异常(不记录事件)。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config, conn=None)
    result = await svc.execute(
        code='print("no conn")',
        language="python",
        session_id="no-conn",
    )
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_crash_recovery(tmp_path: Path) -> None:
    """AC-14: sys.exit(1) 返回 SandboxResult(exit_code=1),不抛异常。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code="import sys; sys.exit(1)",
        language="python",
        session_id="crash-test",
    )
    assert isinstance(result, SandboxResult)
    assert result.exit_code == 1
    # exit_code=1 时不抛异常


@pytest.mark.asyncio
async def test_crash_recovery_syntax_error(tmp_path: Path) -> None:
    """AC-14: 语法错误返回 SandboxResult(exit_code!=0),不抛异常。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code="print(hello",
        language="python",
        session_id="syntax-crash",
    )
    assert isinstance(result, SandboxResult)
    assert result.exit_code != 0
    assert "Error" in result.stderr or "SyntaxError" in result.stderr


@pytest.mark.asyncio
async def test_disk_limit_exceeded(tmp_path: Path) -> None:
    """磁盘超限时直接返回错误结果,不执行代码。"""
    config = _make_config(tmp_path)
    config["sandbox"]["limits"]["disk_limit_mb"] = 0  # 0MB 限制
    svc = SandboxService(config)
    result = await svc.execute(
        code='print("should not run")',
        language="python",
        session_id="disk-limit",
    )
    assert result.exit_code == -1
    assert "Disk limit exceeded" in result.stderr


@pytest.mark.asyncio
async def test_warnings_included_in_result(tmp_path: Path) -> None:
    """代码预扫描告警包含在结果中(code_scan_enabled=True)。"""
    config = _make_config(tmp_path)
    # 使用默认 dangerous_patterns
    config["sandbox"]["security"]["code_scan_enabled"] = True
    svc = SandboxService(config)
    result = await svc.execute(
        code="import os; os.system('ls')",
        language="python",
        session_id="warnings-test",
    )
    assert len(result.warnings) >= 1
    assert any("os.system" in w.snippet for w in result.warnings)


@pytest.mark.asyncio
async def test_generated_files_scanned(tmp_path: Path) -> None:
    """执行后 outputs/ 目录下的文件被扫描到 generated_files。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code="open('outputs/hello.txt', 'w').write('world')",
        language="python",
        session_id="gen-files",
    )
    assert result.exit_code == 0
    assert any("hello.txt" in f for f in result.generated_files)