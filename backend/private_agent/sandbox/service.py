from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

from private_agent.errors import SandboxTimeoutError
from private_agent.sandbox.executor import SandboxExecutor
from private_agent.sandbox.resource_limiter import ResourceLimiter, disable_network
from private_agent.sandbox.result import CodeWarning, SandboxResult
from private_agent.sandbox.security import CodeScanner, EnvSanitizer
from private_agent.sandbox.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# 流式输出回调: (stream_type, chunk) -> Awaitable[None]
OnOutput = Callable[[str, str], Awaitable[None]]


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
        # ${WORKSPACE}/.sandbox 等环境变量展开(config.yaml 大量相对/占位路径,
        # loader 不负责展开, 此处必须 expandvars 否则生成字面量 ${WORKSPACE} 目录)
        workspace_root = Path(
            os.path.expandvars(sandbox_cfg.get("workspace_root", ".sandbox"))
        ).resolve()
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
        memory_limit_mb = limits_cfg.get("memory_limit_mb", 512)
        # 网络隔离开关(config 默认 false → 默认禁网; 显式 true 才放行)
        self._network_enabled = bool(limits_cfg.get("network_enabled", False))
        self._resource_limiter = ResourceLimiter(memory_limit_mb, self._default_timeout)

        lang_cfg = sandbox_cfg.get("languages", {}).get("python", {})
        python_cmd = lang_cfg.get("command", "python")
        js_lang_cfg = sandbox_cfg.get("languages", {}).get("javascript", {})
        node_cmd = js_lang_cfg.get("command", "node")

        # 阶段二批次 3: Windows Job Object(内存/CPU 时间/进程数/UI 约束)
        # + POSIX RLIMIT(preexec_fn)。attach 失败自动降级(executor 内处理)。
        job = None
        if os.name == "nt":
            from private_agent.sandbox.job import SandboxJob

            job = SandboxJob(
                memory_limit_mb=memory_limit_mb,
                cpu_timeout_sec=self._default_timeout,
                active_process_limit=int(limits_cfg.get("active_process_limit", 4)),
            )
        self._executor = SandboxExecutor(
            python_command=python_cmd,
            node_command=node_cmd,
            preexec_fn=self._resource_limiter.get_preexec_fn(),
            job=job,
        )

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
        on_output: OnOutput | None = None,
        allow_network: bool = False,
        workspace_env: str | None = None,
    ) -> SandboxResult:
        """端到端执行代码(AC-8, AC-14)。

        Args:
            code: 要执行的代码。
            language: 语言(python/javascript,B2 P1-7)。
            timeout: 超时秒数(默认用 config 值)。
            session_id: 会话 ID(用于工作目录隔离和事件记录)。
            on_output: 流式输出回调(蓝图 §6.10),透传到 executor。
            allow_network: 0.5.1 技能级联网放行 —— code_execution 工具
                显式声明(LLM 置 network=true)时绕过沙箱代理隔离; 默认
                False 保持禁网(disable_network 注入死代理)。
            workspace_env: 2026-08-15 会话工作区 env 覆盖 —— 非 None 时
                将子进程环境变量 WORKSPACE 覆盖为会话选定工作区(与
                ReactLoop 的 cfg.system.workspace_root 一致), 防止模型在
                沙箱内读取到后端全局目录而把产物写错位置。
                2026-08-16(问题1-C): 同时作为"产物同步目标" —— 沙箱
                outputs/ 生成的产物复制一份到 {workspace_env}/.sandbox-
                artifacts/{session_id}/, 使 file_read/_inject_image_urls
                在会话工作区内闭环读取(图片读取失败根治)。

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
        warnings = self._code_scanner.scan(code, language) if self._code_scanner else []

        # 4. 环境变量脱敏 + 网络隔离(阶段二批次 3: 接线 disable_network;
        #    默认禁网, config limits.network_enabled=true 或 工具显式
        #    allow_network=true 才放行)
        safe_env = self._env_sanitizer.sanitize(dict(os.environ))
        if not self._network_enabled and not allow_network:
            safe_env = disable_network(safe_env)
        # 0.5.1 GBK 治本: 沙箱内所有 Python 子进程(含用户脚本内部 subprocess)
        # 强制 UTF-8 模式 —— Windows 默认 GBK 解码 UTF-8 输出会 UnicodeDecodeError
        safe_env.setdefault("PYTHONUTF8", "1")
        safe_env.setdefault("PYTHONIOENCODING", "utf-8")
        # 2026-08-15: 会话工作区 env 对齐 —— 覆盖 WORKSPACE 为会话选定
        # 工作区(有值才覆盖, 避免污染未设定工作区会话的默认行为)
        if workspace_env:
            safe_env["WORKSPACE"] = workspace_env

        # 5. 执行
        try:
            result = await self._executor.execute(
                code=code,
                language=language,
                timeout=timeout,
                workspace=str(workspace),
                env=safe_env,
                on_output=on_output,
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

        # 6.1 2026-08-16(问题1-C): 产物同步到会话工作区 —— 沙箱 outputs/
        # 生成的产物复制到 {workspace_env}/.sandbox-artifacts/{session_id}/,
        # 使 file_read/_inject_image_urls 在会话工作区内闭环读取
        # (图片读取失败根治: 沙箱路径与会话工作区不一致的历史根因)。
        if workspace_env and generated_files:
            try:
                sync_dir = (
                    Path(workspace_env) / ".sandbox-artifacts" / session_id
                )
                sync_dir.mkdir(parents=True, exist_ok=True)
                for rel in generated_files:
                    src = workspace / rel
                    if not src.is_file():
                        continue
                    dest = sync_dir / Path(rel).name
                    import shutil

                    shutil.copy2(src, dest)
                result.sync_dir = str(sync_dir)
            except Exception:  # noqa: BLE001 - 产物同步失败不影响执行结果
                pass

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
