"""V2 P1 - 沙箱流式输出(蓝图 §6.10 _stream_output)。

验证:
- SandboxExecutor.execute 带 on_output 回调 → 按 4KB 分片实时收到 stdout/stderr
- SandboxService.execute 透传 on_output
- code_execution_handler 接受 _on_output 回调 → 流式输出可达调用方
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from private_agent.sandbox.executor import SandboxExecutor
from private_agent.sandbox.service import SandboxService
from private_agent.tools.builtins.code_execution import code_execution_handler


def _make_config(tmp_path: Path) -> dict:
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


class TestExecutorStreaming:
    async def test_on_output_receives_stdout_chunks(self, tmp_path: Path) -> None:
        """多行输出 → on_output 收到全部内容(分片合并 = 完整 stdout)。"""
        executor = SandboxExecutor(python_command=sys.executable)
        chunks: list[tuple[str, str]] = []

        async def on_output(stream: str, chunk: str) -> None:
            chunks.append((stream, chunk))

        code = "import sys\nfor i in range(20):\n    print(f'line-{i}')\n"
        result = await executor.execute(
            code=code,
            language="python",
            timeout=90,
            workspace=str(tmp_path),
            on_output=on_output,
        )
        assert result.exit_code == 0
        stdout = "".join(c for s, c in chunks if s == "stdout")
        assert "line-0" in stdout and "line-19" in stdout
        assert result.stdout == stdout  # 分片拼接 = 最终 stdout

    async def test_on_output_receives_stderr(self, tmp_path: Path) -> None:
        """stderr 输出也走 on_output(stream='stderr')。"""
        executor = SandboxExecutor(python_command=sys.executable)
        chunks: list[tuple[str, str]] = []

        async def on_output(stream: str, chunk: str) -> None:
            chunks.append((stream, chunk))

        code = "import sys\nsys.stderr.write('boom')\n"
        result = await executor.execute(
            code=code,
            language="python",
            timeout=90,
            workspace=str(tmp_path),
            on_output=on_output,
        )
        assert result.exit_code == 0
        stderr = "".join(c for s, c in chunks if s == "stderr")
        assert "boom" in stderr

    async def test_without_on_output_still_works(self, tmp_path: Path) -> None:
        """无 on_output(向后兼容) → 原行为不变。"""
        executor = SandboxExecutor(python_command=sys.executable)
        result = await executor.execute(
            code="print('hello')",
            language="python",
            timeout=90,
            workspace=str(tmp_path),
        )
        assert result.exit_code == 0
        assert "hello" in result.stdout


class TestServiceStreaming:
    async def test_service_passes_through_on_output(self, tmp_path: Path) -> None:
        """SandboxService.execute 透传 on_output 到 executor。"""
        config = _make_config(tmp_path)
        svc = SandboxService(config)
        chunks: list[tuple[str, str]] = []

        async def on_output(stream: str, chunk: str) -> None:
            chunks.append((stream, chunk))

        result = await svc.execute(
            code="print('svc-stream')",
            language="python",
            timeout=90,
            session_id="stream-svc",
            on_output=on_output,
        )
        assert result.exit_code == 0
        stdout = "".join(c for s, c in chunks if s == "stdout")
        assert "svc-stream" in stdout
        assert "svc-stream" in result.stdout


class TestHandlerStreaming:
    async def test_handler_with_on_output_callback(self, tmp_path: Path) -> None:
        """code_execution_handler 接受 _on_output → 流式输出可达调用方。"""
        config = _make_config(tmp_path)
        chunks: list[tuple[str, str]] = []

        async def on_output(stream: str, chunk: str) -> None:
            chunks.append((stream, chunk))

        result = await code_execution_handler({
            "code": "for i in range(5):\n    print(f'n-{i}')",
            "timeout": 90,
            "session_id": "handler-stream",
            "_sandbox_config": config,
            "_on_output": on_output,
        })
        assert result.error is None
        stdout = "".join(c for s, c in chunks if s == "stdout")
        assert "n-0" in stdout and "n-4" in stdout
        assert "Exit code: 0" in result.output

    async def test_handler_without_on_output(self, tmp_path: Path) -> None:
        """无 _on_output → 兼容旧行为。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": "print('legacy')",
            "timeout": 90,
            "session_id": "handler-legacy",
            "_sandbox_config": config,
        })
        assert result.error is None
        assert "legacy" in result.output
