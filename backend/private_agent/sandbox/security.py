from __future__ import annotations

import os
import re
from pathlib import Path

from private_agent.sandbox.result import CodeWarning

# 阻断提示(面向模型, 必须给出可操作的替代方案, 而不只是报错)。
# 2026-09-03 方案 B: 配套 `CodeScanner.find_blocking_violation()` 使用。
TRAVERSAL_BLOCKED_MESSAGE = (
    "【执行前拦截】检测到以超大目录为起点的全树递归遍历, 已拒绝执行"
    "(第 {line} 行: {snippet})。\n"
    "原因: 这类遍历单次可达数十万条目(实测 C:\\Users\\<name> 548,728 项 / "
    "暖缓存 12.5s、冷缓存 ≈56s); 4 基目录 x 4 模式即 137.55s, 叠加冷缓存惩罚"
    "必然越过本工具 300s 超时 —— 结果是干等 5 分钟零输出。\n"
    "替代方案:\n"
    "1. 查已安装包的版本/元数据 → importlib.metadata.version(\"pkg\")"
    "(O(1), 实测 0.001~0.01s)\n"
    "2. 定位文件 → 先用已知精确路径 + os.path.exists 校验\n"
    "3. 确需遍历 → 起点收窄到明确的小目录(预计条目 < 1 万), 或用 os.scandir "
    "限定深度, 命中即 break"
)


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
        r"\beval\s*\(",
        r"\bexec\s*\(",
    ]

    # B2 P1-7: JavaScript 危险模式(node 沙箱)
    JS_DANGEROUS_PATTERNS: list[str] = [
        r"child_process\.(exec|execSync|spawn|spawnSync|fork)\s*\(",
        r"require\s*\(\s*['\"]child_process['\"]\s*\)",
        r"eval\s*\(",
        r"new\s+Function\s*\(",
        r"fs\.(unlinkSync|rmSync|writeFileSync|appendFileSync)\s*\(",
        r"process\.(env|exit)\b",
        r"globalThis\.fetch\s*\(",
    ]

    # 2026-09-03(修复方案 B, 源自 session-76 超时事故): 阻断级规则 ——
    # 「以超大目录为起点的全树递归遍历」。与 DEFAULT_DANGEROUS_PATTERNS
    # (只告警、不阻断)不同, 这一类必须**执行前**拦截: 单次遍历
    # C:\Users\zongxin 实测 548,728 条目 / 暖缓存 12.5s、冷缓存 ≈56s;
    # 事故代码 4 基目录 x 4 模式 = 137.55s, 叠加冷缓存惩罚即越过 300s
    # 墙钟超时, 用户干等 5.5 分钟零输出。干等的代价 >> 误拦一次。
    RECURSIVE_MARKERS: list[str] = [
        r"recursive\s*=\s*True",   # glob.glob(..., recursive=True)
        r"os\.walk\s*\(",
        r"\.rglob\s*\(",
    ]
    # 超大目录起点(盘符根 / POSIX 根 / 用户主目录 / HOME 系环境变量)
    HUGE_ROOT_MARKERS: list[str] = [
        r"expanduser\(\s*['\"]~['\"]\s*\)",
        r"Path\.home\s*\(",
        r"environ(?:\.get)?\s*[\[(]\s*['\"](?:HOME|USERPROFILE)['\"]",
        r"['\"][A-Za-z]:[\\/]+['\"]",
        r"['\"]/['\"]",
    ]

    def __init__(self, patterns: list[str] | None = None) -> None:
        self._patterns = patterns or list(self.DEFAULT_DANGEROUS_PATTERNS)

    def scan(self, code: str, language: str = "python") -> list[CodeWarning]:
        """预扫描代码,返回告警列表(不阻断)。

        Args:
            code: 用户提交的代码文本。
            language: 语言标识(python/javascript),决定使用的危险模式集。

        Returns:
            已匹配到的告警列表(空列表表示无告警)。
        """
        patterns = self._patterns
        if self._patterns == list(self.DEFAULT_DANGEROUS_PATTERNS) and language == "javascript":
            # 自定义 patterns 优先;默认配置时按语言切换(B2 P1-7)
            patterns = list(self.JS_DANGEROUS_PATTERNS)
        warnings: list[CodeWarning] = []
        for pattern in patterns:
            for match in re.finditer(pattern, code):
                line = code[: match.start()].count("\n") + 1
                warnings.append(CodeWarning(
                    pattern=pattern,
                    line=line,
                    snippet=match.group(),
                ))
        return warnings

    def find_blocking_violation(
        self, code: str, language: str = "python"
    ) -> CodeWarning | None:
        """阻断级预检: 全树递归遍历以超大目录为起点。

        与 `scan()` 的区别: `scan()` 只告警(记录到 react_events.warnings、
        事后拼进 output), 而本方法的结果由 SandboxService 用于**执行前拒绝**。

        Args:
            code: 用户提交的代码文本。
            language: 语言标识; 仅 python 生效(JS 遍历语义不同, 避免误伤)。

        Returns:
            命中的违规项(含行号与命中片段); 无风险则返回 None。
        """
        if language != "python":
            return None
        # 两个条件同时成立才拦: 有递归遍历写法 + 起点是超大目录。
        # 只命中其一属合法用法(如窄目录 glob / 读主目录下的单个配置文件)。
        if not any(re.search(p, code) for p in self.RECURSIVE_MARKERS):
            return None
        for pattern in self.HUGE_ROOT_MARKERS:
            match = re.search(pattern, code)
            if match:
                line = code[: match.start()].count("\n") + 1
                return CodeWarning(
                    pattern=pattern,
                    line=line,
                    snippet=match.group(),
                )
        return None


class EnvSanitizer:
    """环境变量脱敏器(蓝图 §6.8 / spec m2-sandbox AC-6)。

    两层过滤:
    1. 敏感模式(保密): KEY/SECRET/TOKEN/PASSWORD 等子串匹配;
    2. 注入类变量精确阻断(2026-09-07 S7, 自检 P2-#9): PYTHONPATH 等
       不含敏感词、但可劫持沙箱进程解释器的变量 —— PYTHONPATH 经
       sitecustomize 注入任意代码、NODE_OPTIONS 注入 --require、
       PYTHONSTARTUP 执行启动脚本、LD_PRELOAD 劫持动态链接。
       此前全部透传进沙箱(环境隔离缺口), 一律精确名阻断。
    """

    DEFAULT_SENSITIVE_PATTERNS: list[str] = [
        "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
        "CREDENTIAL", "AUTH", "API_KEY", "PRIVATE_KEY",
        "DATABASE_URL", "DB_PASSWORD", "CONNECTION_STRING",
    ]

    # 解释器注入类变量(精确名, 大小写不敏感) —— 与敏感模式正交,
    # 不受 sensitive_patterns 自定义影响, 恒阻断。
    BLOCKED_INJECTION_VARS: frozenset[str] = frozenset({
        "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONINSPECT",
        "PYTHONBREAKPOINT", "NODE_OPTIONS", "LD_PRELOAD",
        "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    })

    def __init__(self, sensitive_patterns: list[str] | None = None) -> None:
        self._patterns = sensitive_patterns or list(self.DEFAULT_SENSITIVE_PATTERNS)

    def sanitize(self, env: dict[str, str]) -> dict[str, str]:
        """过滤敏感/注入类环境变量,防止 Agent 代码读取凭证或被注入。

        Args:
            env: 原始环境变量 dict。

        Returns:
            脱敏后的环境变量 dict(保留 PATH/HOME/USER/LANG)。
        """
        sanitized: dict[str, str] = {}
        for key, value in env.items():
            if self._is_sensitive(key) or self._is_injection_var(key):
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

    def _is_injection_var(self, key: str) -> bool:
        return key.upper() in self.BLOCKED_INJECTION_VARS


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
