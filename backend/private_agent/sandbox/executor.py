from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable

from private_agent.errors import SandboxTimeoutError
from private_agent.sandbox.result import SandboxResult

# 流式输出回调: (stream_type: "stdout"|"stderr", chunk: str) -> Awaitable[None]
OnOutput = Callable[[str, str], Awaitable[None]]


class SandboxExecutor:
    """子进程隔离的代码执行器(蓝图 §6.3 / spec m2-sandbox AC-1, AC-2, AC-12, AC-15)。

    基于 asyncio.create_subprocess_exec,写临时脚本→启动子进程→超时控制→收集输出。
    MVP 仅子进程模式,V2 可升级 Docker 后端。
    支持语言:python / javascript(B2 P1-7)。
    """

    def __init__(
        self,
        python_command: str = "python",
        node_command: str = "node",
        preexec_fn: Callable[[], None] | None = None,
        job: object | None = None,
    ) -> None:
        """初始化执行器。

        Args:
            python_command: Python 解释器路径。
            node_command: Node 解释器路径。
            preexec_fn: POSIX 子进程预执行回调(ResourceLimiter.get_preexec_fn,
                设置 RLIMIT_AS/RLIMIT_CPU); Windows 无 preexec_fn, 传 None。
            job: Windows Job Object 沙箱(SandboxJob, 阶段二批次 3);
                子进程 spawn 后尽快 attach_pid, 结束前保持句柄存活。
        """
        self._python_cmd = python_command
        self._node_cmd = node_command
        self._preexec_fn = preexec_fn if os.name != "nt" else None
        self._job = job

    async def execute(
        self,
        code: str,
        language: str,
        timeout: int,
        workspace: str,
        env: dict[str, str] | None = None,
        on_output: OnOutput | None = None,
    ) -> SandboxResult:
        """在子进程中执行代码(AC-1, AC-2)。

        Args:
            code: 要执行的 Python 代码。
            language: 语言标识(当前仅支持 "python")。
            timeout: 超时秒数。
            workspace: 工作目录路径。
            env: 环境变量 dict(已脱敏)。
            on_output: 流式输出回调(蓝图 §6.10 _stream_output),stdout/stderr
                按 4KB 分片实时回调;None 时保持一次性收集(向后兼容)。

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
            preexec_fn=self._preexec_fn,
        )
        # 阶段二批次 3: Windows Job Object 约束(尽早挂入; 失败降级不阻断)
        job_attached = False
        if self._job is not None:
            job_attached = self._job.attach_pid(process.pid)  # type: ignore[attr-defined]
        try:
            if on_output is not None:
                stdout_data, stderr_data = await asyncio.wait_for(
                    self._stream_output(process, on_output),
                    timeout=timeout,
                )
                await process.wait()
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
                stdout_data = stdout_bytes.decode("utf-8", errors="replace")
                stderr_data = stderr_bytes.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
            elapsed = int((time.monotonic() - start) * 1000)
            raise SandboxTimeoutError(
                f"Execution exceeded {timeout}s"
            ) from None
        finally:
            # Job 句柄在子进程结束后释放(KILL_ON_JOB_CLOSE 语义)
            if self._job is not None:
                self._job.close()  # type: ignore[attr-defined]

        elapsed = int((time.monotonic() - start) * 1000)
        return SandboxResult(
            stdout=stdout_data,
            stderr=stderr_data,
            exit_code=process.returncode or 0,
            duration_ms=elapsed,
        )

    async def _stream_output(
        self,
        process: asyncio.subprocess.Process,
        on_output: OnOutput,
    ) -> tuple[str, str]:
        """流式读取 stdout/stderr(蓝图 §6.10),4KB 分片实时回调。

        Args:
            process: 运行中的子进程。
            on_output: 流式回调(stream_type, chunk)。

        Returns:
            (stdout, stderr) 完整拼接文本。
        """
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def read_stream(
            stream: asyncio.StreamReader,
            chunks: list[str],
            stream_type: str,
        ) -> None:
            while True:
                chunk = await stream.read(4096)  # 4KB 分片
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                chunks.append(text)
                try:
                    await on_output(stream_type, text)
                except Exception:
                    # 回调异常不影响执行结果收集
                    pass

        await asyncio.gather(
            read_stream(process.stdout, stdout_chunks, "stdout"),
            read_stream(process.stderr, stderr_chunks, "stderr"),
        )
        return "".join(stdout_chunks), "".join(stderr_chunks)

    async def _write_script(self, code: str, language: str, workspace: str) -> str:
        """将代码写入临时脚本文件。"""
        if language == "python":
            ext = ".py"
        elif language == "javascript":
            ext = ".js"
        else:
            ext = ".txt"
        scripts_dir = Path(workspace) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / f"script_{int(time.time())}{ext}"
        script_path.write_text(code, encoding="utf-8")
        return str(script_path)

    def _build_command(self, language: str, script_path: str) -> list[str]:
        """构建子进程执行命令。"""
        if language == "python":
            return [self._python_cmd, script_path]
        if language == "javascript":
            return [self._find_node_cmd(), script_path]
        msg = f"Unsupported language: {language}"
        raise ValueError(msg)

    def _find_node_cmd(self) -> str:
        """定位 node 可执行文件(B2 P1-7)。

        Raises:
            ValueError: node 不在 PATH 中。
        """
        cmd = shutil.which(self._node_cmd)
        if cmd is None:
            msg = f"node command '{self._node_cmd}' not found in PATH"
            raise ValueError(msg)
        return cmd
