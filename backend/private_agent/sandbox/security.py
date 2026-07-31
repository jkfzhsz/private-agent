from __future__ import annotations

import os
import re
from pathlib import Path

from private_agent.sandbox.result import CodeWarning


class CodeScanner:
    """危险代码预扫描器(蓝图 §6.8 / spec m2-sandbox AC-5)。

    告警不阻断,仅记录到 react_events.warnings。
    """

    DEFAULT_DANGEROUS_PATTERNS: list[str] = [
        r"os\.system\s*\(",
        r"subprocess\.(call|run|Popen|check_output)\s*\(",
        r"shutil\.rmtree\s*\(",
        r"os\.remove\s*\(.*/\*",
        r"os\.unlink\s*\(",
        r"socket\.socket\s*\(",
        r"os\.kill\s*\(",
        r"os\.fork\s*\(",
        r"eval\s*\(",
        r"exec\s*\(",
    ]

    def __init__(self, patterns: list[str] | None = None) -> None:
        self._patterns = patterns or list(self.DEFAULT_DANGEROUS_PATTERNS)

    def scan(self, code: str) -> list[CodeWarning]:
        """预扫描代码,返回告警列表(不阻断)。

        Args:
            code: 用户提交的代码文本。

        Returns:
            已匹配到的告警列表(空列表表示无告警)。
        """
        warnings: list[CodeWarning] = []
        for pattern in self._patterns:
            for match in re.finditer(pattern, code):
                line = code[: match.start()].count("\n") + 1
                warnings.append(CodeWarning(
                    pattern=pattern,
                    line=line,
                    snippet=match.group(),
                ))
        return warnings


class EnvSanitizer:
    """环境变量脱敏器(蓝图 §6.8 / spec m2-sandbox AC-6)。

    过滤 KEY/SECRET/TOKEN/PASSWORD 等敏感模式。
    """

    DEFAULT_SENSITIVE_PATTERNS: list[str] = [
        "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
        "CREDENTIAL", "AUTH", "API_KEY", "PRIVATE_KEY",
        "DATABASE_URL", "DB_PASSWORD", "CONNECTION_STRING",
    ]

    def __init__(self, sensitive_patterns: list[str] | None = None) -> None:
        self._patterns = sensitive_patterns or list(self.DEFAULT_SENSITIVE_PATTERNS)

    def sanitize(self, env: dict[str, str]) -> dict[str, str]:
        """过滤敏感环境变量,防止 Agent 代码读取本地凭证。

        Args:
            env: 原始环境变量 dict。

        Returns:
            脱敏后的环境变量 dict(保留 PATH/HOME/USER/LANG)。
        """
        sanitized: dict[str, str] = {}
        for key, value in env.items():
            if self._is_sensitive(key):
                continue
            sanitized[key] = value
        # 保留必要的基础变量
        sanitized.setdefault("PATH", env.get("PATH", ""))
        sanitized.setdefault("HOME", env.get("HOME", ""))
        sanitized.setdefault("USER", env.get("USER", ""))
        sanitized.setdefault("LANG", env.get("LANG", "en_US.UTF-8"))
        return sanitized

    def _is_sensitive(self, key: str) -> bool:
        key_upper = key.upper()
        return any(p in key_upper for p in self._patterns)


class PathFilter:
    """路径白名单过滤器(蓝图 §6.8 / spec m2-sandbox AC-7)。

    校验代码访问的文件路径是否在 readonly/writable 白名单内。
    """

    def __init__(
        self, readonly: list[str | Path], writable: list[str | Path]
    ) -> None:
        self._readonly = [Path(p).resolve() for p in readonly]
        self._writable = [Path(p).resolve() for p in writable]

    def validate_file_access(self, path: str, write: bool = False) -> bool:
        """校验路径是否在白名单内(AC-7)。

        Args:
            path: 请求的文件路径。
            write: True=需要写权限,False=读权限即可。

        Returns:
            True 表示路径在白名单内,False 表示拒绝访问。
        """
        target = Path(path).resolve()
        if write:
            return any(self._is_subpath(target, p) for p in self._writable)
        return (
            any(self._is_subpath(target, p) for p in self._readonly)
            or any(self._is_subpath(target, p) for p in self._writable)
        )

    @staticmethod
    def _is_subpath(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
