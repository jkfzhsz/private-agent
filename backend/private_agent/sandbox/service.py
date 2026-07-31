from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from private_agent.errors import SandboxTimeoutError
from private_agent.sandbox.executor import SandboxExecutor
from private_agent.sandbox.result import CodeWarning, SandboxResult
from private_agent.sandbox.security import CodeScanner, EnvSanitizer
from private_agent.sandbox.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class SandboxService:
    """沙箱端到端编排服务(蓝图 §6.9 / spec m2-sandbox AC-8, AC-9, AC-10, AC-14)。

    串联 workspace→scan→sanitize→execute→scan_files→log_event→artifact。
    """

    def __init__(
        self,
        config: dict,
        conn=None,
    ) -> None:
        self._config = config
        self._conn = conn
        sandbox_cfg = config.get("sandbox", {})
        workspace_root = Path(sandbox_cfg.get("workspace_root", ".sandbox")).resolve()
        self._workspace_mgr = WorkspaceManager(workspace_root)

        security_cfg = sandbox_cfg.get("security", {})
        self._code_scanner = CodeScanner(
            security_cfg.get("dangerous_patterns")
        )
        self._env_sanitizer = EnvSanitizer(
            security_cfg.get("sensitive_env_patterns")
        )

        limits_cfg = sandbox_cfg.get("limits", {})
        self._default_timeout = limits_cfg.get("cpu_timeout_sec", 300)
        self._disk_limit_mb = limits_cfg.get("disk_limit_mb", 100)

        lang_cfg = sandbox_cfg.get("languages", {}).get("python", {})
        python_cmd = lang_cfg.get("command", "python")
        self._executor = SandboxExecutor(python_command=python_cmd)

        output_cfg = sandbox_cfg.get("output", {})
        self._stdout_artifact_threshold = output_cfg.get(
            "stdout_artifact_threshold", 2000
        )
        self._code_artifact_threshold = output_cfg.get(
            "code_artifact_threshold", 4000
        )

    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: int | None = None,
        session_id: str = "",
    ) -> SandboxResult:
        """端到端执行代码(AC-8, AC-14)。

        Args:
            code: 要执行的代码。
            language: 语言(当前仅支持 "python")。
            timeout: 超时秒数(默认用 config 值)。
            session_id: 会话 ID(用于工作目录隔离和事件记录)。

        Returns:
            SandboxResult 包含执行结果和元数据。
        """
        start = time.monotonic()
        timeout = timeout or self._default_timeout

        # 1. 工作目录
        workspace = self._workspace_mgr.get_or_create(session_id)

        # 2. 磁盘检查(前置拦截)
        if not WorkspaceManager.check_disk_usage(workspace, self._disk_limit_mb):
            return SandboxResult(
                stdout="",
                stderr=f"Disk limit exceeded ({self._disk_limit_mb}MB)",
                exit_code=-1,
                duration_ms=0,
            )

        # 3. 代码预扫描(告警不阻断)
        warnings = self._code_scanner.scan(code) if self._code_scanner else []

        # 4. 环境变量脱敏
        safe_env = self._env_sanitizer.sanitize(dict(os.environ))

        # 5. 执行
        try:
            result = await self._executor.execute(
                code=code,
                language=language,
                timeout=timeout,
                workspace=str(workspace),
                env=safe_env,
            )
        except SandboxTimeoutError as e:
            result = SandboxResult(
                stdout="",
                stderr=str(e),
                exit_code=-1,
                generated_files=self._scan_generated_files(workspace),
                warnings=warnings,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # 6. 扫描生成的文件
        generated_files = self._scan_generated_files(workspace)
        result.generated_files = generated_files
        result.warnings = warnings

        # 7. stdout 截断(AC-9: 用字符数/4 粗估)
        if self._stdout_artifact_threshold > 0:
            estimated_tokens = len(result.stdout) // 4
            if estimated_tokens > self._stdout_artifact_threshold:
                artifact_path = self._save_artifact(
                    result.stdout, workspace, "stdout", session_id
                )
                result.stdout = (
                    f"[stdout truncated: ~{estimated_tokens} tokens, "
                    f"saved to {artifact_path}]"
                )

        # 8. 事件记录(AC-10)
        await self._log_execution(
            session_id=session_id,
            language=language,
            code=code[: self._code_artifact_threshold],
            exit_code=result.exit_code,
            generated_files=result.generated_files,
            duration_ms=int((time.monotonic() - start) * 1000),
            warnings=result.warnings,
        )

        return result

    def _scan_generated_files(self, workspace: Path) -> list[str]:
        """扫描 outputs/ 目录下的生成文件。"""
        outputs_dir = workspace / "outputs"
        if not outputs_dir.is_dir():
            return []
        files: list[str] = []
        for entry in outputs_dir.iterdir():
            if entry.is_file():
                files.append(str(entry.relative_to(workspace)))
        return sorted(files)

    def _save_artifact(
        self, content: str, workspace: Path, prefix: str, session_id: str
    ) -> str:
        """将大内容存入 artifact 文件。"""
        artifacts_dir = workspace / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifacts_dir / f"{prefix}_{int(time.time())}.txt"
        artifact_path.write_text(content, encoding="utf-8")
        return str(artifact_path)

    async def _log_execution(
        self,
        session_id: str,
        language: str,
        code: str,
        exit_code: int,
        generated_files: list[str],
        duration_ms: int,
        warnings: list[CodeWarning] | None = None,
    ) -> None:
        """将沙箱执行事件写入 react_events 表(AC-10)。"""
        if not self._conn or not session_id:
            return
        try:
            payload = {
                "language": language,
                "code": code,
                "exit_code": exit_code,
                "generated_files": generated_files,
                "duration_ms": duration_ms,
                "warnings": [w.__dict__ for w in (warnings or [])],
            }
            await self._conn.execute(
                "INSERT INTO react_events (session_id, turn, event_type, payload) "
                "VALUES ($1, 0, 'sandbox_execution', $2)",
                int(session_id),
                json.dumps(payload),
            )
        except Exception as exc:
            logger.warning("Failed to log sandbox execution event: %s", exc)
