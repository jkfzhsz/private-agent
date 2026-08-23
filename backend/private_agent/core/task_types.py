"""2026-08-13 子代理类型感知并发限流 —— 任务类型判定与工具分类。

设计(见 docs/next-phase-plan-2026-08-13-subagent-type-concurrency.md §4.1):
- 类型枚举: search(网络搜索/调研, 反爬敏感) / analysis(数据分析) /
  code(代码/文件) / other(兜底)。
- 判定三级: 显式 type > 关键词推断 > 默认 search(保守 —— 类型不确定
  按最敏感的搜索处理, 确保反爬保护生效)。
- 工具分类(classify_tool): 执行层(MCP client 类型限流)与委派层共用敏感度逻辑。
"""
from __future__ import annotations

import re

TASK_TYPES = ("search", "analysis", "code", "other")

# 类型判定优先级: 命中即判该类型(按此顺序, search 最敏感最优先)
_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "search",
        (
            "搜索", "检索", "调研", "查询", "查找", "查一下", "找一下",
            "搜集", "收集", "资料", "信息", "资讯", "新闻", "报告", "清单",
            "政策", "web", "search", "fetch", "research", "find",
            "lookup", "gather", "investigate", "browse", "online", "internet",
            "网址", "链接", "页面",
        ),
    ),
    (
        "analysis",
        (
            "分析", "计算", "统计", "数据处理", "数据整理", "汇总", "评估",
            "对比", "比较", "解析", "测算", "提取", "筛选", "分类", "聚类",
            "数据挖掘", "建模", "回归", "预测",
            "analysis", "calc", "compute", "stat", "aggregate", "evaluate",
            "compare", "parse", "extract", "summar", "summari", "model",
        ),
    ),
    (
        "code",
        (
            "编写", "写一个", "创建", "修改", "代码", "脚本", "文件", "生成",
            "保存", "编写", "实现", "重构", "调试", "函数", "程序",
            "write", "create", "modify", "script", "code", "file", "generate",
            "save", "implement", "refactor", "debug", "function", "program",
        ),
    ),
)

# 预编译(大小写不敏感)
_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (typ, re.compile("|".join(map(re.escape, kws)), re.IGNORECASE))
    for typ, kws in _TYPE_RULES
]


def infer_task_type(prompt: str, explicit: str | None = None) -> str:
    """三级判定子任务类型。

    Args:
        prompt: 子任务指令文本(自包含)。
        explicit: 模型显式声明的 type(可选, 来自 delegate_subtask schema)。

    Returns:
        search / analysis / code / other 之一。
    """
    if explicit is not None:
        e = str(explicit).strip().lower()
        if e in TASK_TYPES:
            return e
        # 显式非法 → 忽略, 走推断
    text = prompt or ""
    for typ, pattern in _COMPILED:
        if pattern.search(text):
            return typ
    return "search"  # 保守兜底: 类型不确定按最敏感的搜索处理


# ── 工具分类(执行层 MCP client 类型限流用) ────────────────────────────────

# 工具名前缀 → 类型(顺序敏感: 先匹配先返回)
_TOOL_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("search", ("web_search", "web_fetch", "mcp__Searchpin__", "mcp__searchpin__")),
    ("analysis", ("mcp__hexin-ifind-ds-", "calculator")),
    ("code", ("file_read", "file_write", "read_artifact", "code_execution")),
)


def classify_tool(tool_name: str) -> str:
    """按工具名分类(执行层限流维度)。

    - 搜索类(web_search/web_fetch/Searchpin): 访问外部网站, 反爬敏感 → search
    - 金融数据(ifind): 数据拉取非外网爬取 → analysis
    - 文件/代码: 本地沙箱 → code
    - 其余: other
    """
    name = tool_name or ""
    for typ, prefixes in _TOOL_TYPE_RULES:
        if any(name.startswith(p) for p in prefixes):
            return typ
    return "other"
