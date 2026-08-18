"""蓝图 §6.x / spec m2-sandbox - SandboxResult + CodeWarning 数据类。

SandboxResult: 沙箱执行结果统一结构。
CodeWarning: 代码预扫描告警条目。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CodeWarning:
    """危险代码预扫描告警条目。

    pattern: 匹配到的危险模式正则。
    line: 代码行号(1-based)。
    snippet: 匹配到的代码片段。
    """

    pattern: str
    line: int
    snippet: str


@dataclass
class SandboxResult:
    """沙箱执行结果。

    stdout: 标准输出文本。
    stderr: 标准错误文本。
    exit_code: 进程退出码(0 成功,非零失败)。
    generated_files: 执行生成的文件路径列表。
    warnings: 代码预扫描告警列表。
    duration_ms: 执行耗时(毫秒)。
    sync_dir: 2026-08-16(问题1-C) 产物同步目录(会话工作区内), 空=未同步。
    """

    stdout: str
    stderr: str
    exit_code: int
    generated_files: list[str] = field(default_factory=list)
    warnings: list[CodeWarning] = field(default_factory=list)
    duration_ms: int = 0
    sync_dir: str = ""