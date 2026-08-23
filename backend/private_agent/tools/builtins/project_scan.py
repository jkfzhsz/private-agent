"""阶段3(agent-upgrader 设计文档 §2.2 能力域②): project_scan —— 外部项目评估。

无涯(monitor)能力域②核心工具: 扫描第三方项目, 判定接入方式。

功能:
- 目录结构: 顶层条目 + 关键目录树(深度限制)
- 技术栈识别: 语言/框架/构建工具(从清单文件/入口文件特征)
- 依赖清单: package.json / requirements.txt / Cargo.toml / go.mod 等
- 规模统计: 文件数/行数(按主要源码扩展名)
- 接入评估: 产出结构化建议(接入方式/匹配度/与 PA 能力重叠/风险)

安全边界:
- 仅 monitor 会话装配; 只读扫描(不执行项目代码/不写文件)
- 目录深度/文件数/扫描字节上限(防超大项目卡死)
- 排除常见噪音目录(.git/node_modules/target/dist/build/.venv 等)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

__all__ = ["PROJECT_SCAN_TOOL", "_project_scan_handler"]

# 扫描上限(防超大项目卡死)
_MAX_DEPTH = 3          # 目录树深度(顶层+2 层)
_MAX_DIR_ENTRIES = 200  # 单目录最多列条目
_MAX_TREE_ENTRIES = 500 # 目录树总条目上限
_MAX_TOTAL_BYTES = 5_000_000  # 行数统计最多扫 5MB(PA backend 5 万行 .py 正常)
# 噪音目录(不深入)
_SKIP_DIRS = {
    ".git", "node_modules", "target", "dist", "build", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".idea", ".vscode", ".next", "out",
    ".sandbox", "uploads", "outputs", ".workbuddy",
}
# 主要源码扩展名(规模统计用)
_SRC_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c",
    ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".sh",
}
# 技术栈识别特征(清单文件 → 语言/框架/构建)
_MANIFEST_HINTS = {
    "package.json": ("node", "JavaScript/TypeScript(前端/Node)"),
    "requirements.txt": ("python", "Python"),
    "pyproject.toml": ("python", "Python"),
    "Cargo.toml": ("rust", "Rust"),
    "go.mod": ("go", "Go"),
    "pom.xml": ("java", "Java(Maven)"),
    "build.gradle": ("java", "Java(Gradle)"),
    "composer.json": ("php", "PHP"),
    "Gemfile": ("ruby", "Ruby"),
    "CMakeLists.txt": ("cpp", "C/C++(CMake)"),
}
_ENTRY_HINTS = {
    "main.py": ("python", "Python"),
    "manage.py": ("python", "Python(Django)"),
    "app.py": ("python", "Python(Flask/FastAPI)"),
    "index.js": ("node", "JavaScript(Node)"),
    "main.ts": ("node", "TypeScript(Node)"),
    "main.go": ("go", "Go"),
    "main.rs": ("rust", "Rust"),
    "index.html": ("web", "Web(静态)"),
}


def _read_manifest(root: Path) -> dict:
    """读取常见依赖清单文件(限 200KB)。"""
    out: dict = {}
    for fname in _MANIFEST_HINTS:
        fp = root / fname
        if not fp.is_file():
            continue
        try:
            if fp.stat().st_size > 200_000:
                out[fname] = "(超 200KB, 跳过)"
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
            out[fname] = text[:3000]  # 截断, 防刷爆上下文
        except OSError:
            out[fname] = "(读取失败)"
    return out


def _collect_tree(root: Path) -> list[str]:
    """目录树(顶层条目 + 深度 2 的关键目录展开, 排除噪音)。"""
    lines: list[str] = []
    try:
        entries = sorted(
            root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
    except OSError:
        return lines
    count = 0
    for p in entries[: _MAX_DIR_ENTRIES]:
        if count >= _MAX_TREE_ENTRIES:
            lines.append("…(目录树截断)")
            break
        if p.name in _SKIP_DIRS:
            continue
        suffix = "/" if p.is_dir() else ""
        lines.append(f"{p.name}{suffix}")
        count += 1
        if p.is_dir():
            try:
                sub = sorted(
                    p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
                )[: _MAX_DIR_ENTRIES // 2]
            except OSError:
                continue
            for s in sub:
                if count >= _MAX_TREE_ENTRIES:
                    lines.append("…(目录树截断)")
                    break
                if s.name in _SKIP_DIRS:
                    continue
                lines.append(f"  {s.name}{'/' if s.is_dir() else ''}")
                count += 1
    return lines


def _collect_stats_corrected(root: Path) -> dict:
    """源码规模统计: 按扩展名聚合文件数/行数(限 2MB, 超限截断)。"""
    stats: dict[str, dict] = {}
    total_files = 0
    total_lines = 0
    scanned_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if dirpath.count(os.sep) - str(root).count(os.sep) > 6:
            dirnames[:] = []
            continue
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext not in _SRC_EXTS:
                continue
            fp = Path(dirpath) / fn
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if scanned_bytes + size > _MAX_TOTAL_BYTES:
                # 截断: 仍汇总已统计部分(避免 stats 缺 _total)
                stats["_total"] = {"files": total_files, "lines": total_lines,
                                   "truncated": True}
                return stats
            scanned_bytes += size
            entry = stats.setdefault(ext, {"files": 0, "lines": 0})
            entry["files"] += 1
            total_files += 1
            try:
                with open(fp, "rb") as f:
                    lines = sum(1 for _ in f)
                entry["lines"] += lines
                total_lines += lines
            except OSError:
                continue
    stats["_total"] = {"files": total_files, "lines": total_lines}
    return stats


def _detect_tech(root: Path) -> list[str]:
    """技术栈识别(清单文件 + 入口特征)。"""
    detected: list[str] = []
    for fname, (lang, label) in _MANIFEST_HINTS.items():
        if (root / fname).is_file():
            detected.append(f"{fname} → {label}")
    for fname, (lang, label) in _ENTRY_HINTS.items():
        if (root / fname).is_file():
            detected.append(f"{fname} → {label}")
    if not detected:
        detected.append("(未识别到常见清单/入口, 可能是底层库/脚本集合)")
    return detected


async def _project_scan_handler(args: dict) -> ToolResult:
    """扫描外部项目, 产出结构/技术栈/依赖/规模 + 接入评估。

    Args:
        path: 项目根目录(必填)。
        workspace: 会话工作区(可选, 仅用于路径解析)。
    """
    path = str(args.get("path") or "").strip()
    ws = args.get("workspace") or ""
    if not path:
        return ToolResult(output="", error="path required(项目根目录)")
    root = Path(os.path.expandvars(path))
    if not root.is_absolute() and ws:
        root = Path(os.path.expandvars(str(ws))) / root
    root = root.resolve()
    if not root.is_dir():
        return ToolResult(output="", error=f"目录不存在: {root}")

    # 单层扫描(不深入)
    try:
        top = sorted(
            root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
    except OSError:
        top = []
    if len(top) == 0:
        return ToolResult(output="", error=f"目录为空: {root}")

    tech = _detect_tech(root)
    manifests = _read_manifest(root)
    tree = _collect_tree(root)
    stats = _collect_stats_corrected(root)

    report = {
        "path": str(root),
        "tech_stack": tech,
        "manifest_summary": {k: v[:200] for k, v in manifests.items()},
        "directory_tree": tree[:60],
        "stats": stats,
    }
    return ToolResult(
        output="项目扫描完成(结构化数据):\n"
        + json.dumps(report, ensure_ascii=False, indent=1)[:6000]
        + "\n\n请基于以上数据给出接入评估: "
          "① 是否值得接入; ② 接入方式(MCP/skill/代码改造/不建议); "
          "③ 与 PA 现有能力重叠; ④ 实施步骤。",
    )


PROJECT_SCAN_TOOL = ToolDef(
    name="project_scan",
    description=(
        "扫描外部项目(目录/技术栈/依赖/规模), 供接入评估。"
        "用于判断第三方项目是否值得接入 PA、以何种方式接入"
        "(MCP/skill/代码改造/不建议)。只读安全。path 为项目根目录。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "项目根目录(绝对路径)",
            },
            "workspace": {
                "type": "string",
                "description": "会话工作区(可选, 用于相对路径解析)",
            },
        },
        "required": ["path"],
    },
    handler=_project_scan_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)
