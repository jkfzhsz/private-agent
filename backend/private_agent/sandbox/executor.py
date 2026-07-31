from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from private_agent.errors import SandboxTimeoutError
from private_agent.sandbox.result import SandboxResult


class SandboxExecutor:
    """子进程隔离的代码执行器(蓝图 §6.3 / spec m2-sandbox AC-1, AC-2, AC-12, AC-15)。

    基于 asyncio.create_subprocess_exec,写临时脚本→启动子进程→超时控制→收集输出。
    MVP 仅子进程模式,V2 可升级 Docker 后端。
    """

    def __init__(self, python_command: str = "python") -> None:
        self._python_cmd = python_command

    async def execute(
        self,
        code: str,
        language: str,
        timeout: int,
        workspace: str,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """在子进程中执行代码(AC-1, AC-2)。

        Args:
            code: 要执行的 Python 代码。
            language: 语言标识(当前仅支持 "python")。
            timeout: 超时秒数。
            workspace: 工作目录路径。
            env: 环境变量 dict(已脱敏)。

        Returns:
            SandboxResult 包含 stdout/stderr/exit_code。
        """
        start = time.monotonic()
        script_path = await self._write_script(code, language, workspace)
        cmd = self._build_command(language, script_path)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
            elapsed = int((time.monotonic() - start) * 1000)
            raise SandboxTimeoutError(
                f"Execution exceeded {timeout}s"
            ) from None

        elapsed = int((time.monotonic() - start) * 1000)
        return SandboxResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            exit_code=process.returncode or 0,
            duration_ms=elapsed,
        )

    async def _write_script(self, code: str, language: str, workspace: str) -> str:
        """将代码写入临时脚本文件。"""
        ext = ".py" if language == "python" else ".txt"
        scripts_dir = Path(workspace) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / f"script_{int(time.time())}{ext}"
        script_path.write_text(code, encoding="utf-8")
        return str(script_path)

    def _build_command(self, language: str, script_path: str) -> list[str]:
        """构建子进程执行命令。"""
        if language == "python":
            return [self._python_cmd, script_path]
        msg = f"Unsupported language: {language}"
        raise ValueError(msg)
