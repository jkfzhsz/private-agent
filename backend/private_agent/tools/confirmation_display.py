"""权限确认卡片人性化描述(2026-08-15 蒋先生反馈: 确认内容英文+术语看不懂)。

问题: tool_confirmation_required 事件的 message 为英文
("Allow tool 'xxx' to execute?"), 参数直接甩原始 JSON, 原因含
"elevated"等术语 —— 非程序员用户无法据此做出授权决策, 授权等于盲签。

本模块提供零 LLM 的纯映射层: 工具中文名 + 参数人话提取 + 通俗风险提示。
设计原则:
- 不改动既有事件字段(tool_name/args_summary/risk_level 保持原样,
  历史回放与下游消费方 100% 兼容), 仅新增 display 字段;
- display 生成失败不影响确认流程(try/except 兜底为空);
- MCP 工具名(mcp__{server}__{tool})按 server 段映射中文名,
  未登记的 server 显示原名, 不报错。

字段约定(display dict):
- title: 一句话标题, 如 "运行一段 Python 代码(不联网)"
- summary: 人话要点列表(每行一条, 供前端逐行渲染)
- tool_label: 工具中文名(如 "运行代码")
"""
from __future__ import annotations

import re

__all__ = ["humanize_confirmation", "TOOL_LABELS", "MCP_SERVER_LABELS"]

# 内置工具中文名(触发确认的主要工具; 未登记的显示原名)
TOOL_LABELS: dict[str, str] = {
    "code_execution": "运行代码",
    "file_write": "写入文件",
    "file_read": "读取文件",
    "read_artifact": "读取产出文件",
    "apply_optim": "执行系统优化方案",
}

# MCP server 中文名(mcp__{server}__{tool} 的 server 段)
MCP_SERVER_LABELS: dict[str, str] = {
    "ifind": "同花顺iFinD(金融数据)",
    "qcc": "企查查(企业信息)",
    "mempalace": "记忆宫殿(个人知识库)",
    "searchpin": "Searchpin(网络搜索)",
}

# 常见参数键的中文释义(参数人话提取用)
_ARG_LABELS: dict[str, str] = {
    "path": "目标位置",
    "file_path": "目标文件",
    "filepath": "目标文件",
    "filename": "文件名",
    "code": "代码内容",
    "query": "查询内容",
    "keyword": "关键词",
    "keywords": "关键词",
    "search": "搜索内容",
    "url": "网址",
    "name": "名称",
    "symbol": "代码",
    "stock_code": "股票代码",
    "company": "公司",
    "company_name": "公司名称",
    "content": "写入内容",
    "text": "文本内容",
    "title": "标题",
    "optim_id": "方案编号",
    "timeout": "最长运行时间(秒)",
    "date": "日期",
    "start_date": "开始日期",
    "end_date": "结束日期",
    "limit": "条数上限",
    "page": "页码",
}

# 参数提取时跳过的内部字段
_SKIP_ARGS = {"_on_output", "_sandbox_config", "session_id"}

_MCP_NAME_RE = re.compile(r"^mcp__(?P<server>[^_]+)__(?P<tool>.+)$")

_MAX_VALUE_LEN = 60
_MAX_SUMMARY_LINES = 6


def _clip(value: str, limit: int = _MAX_VALUE_LEN) -> str:
    """截断长值, 附长度提示。"""
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + f"…(共 {len(value)} 字)"


def _code_first_meaningful_line(code: str) -> str:
    """取代码第一行有效语句(跳过注释/空行), 供"做什么"概览。"""
    for line in code.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return _clip(stripped, 50)
    return "(空代码)"


def _summarize_value(value: object) -> str:
    """单个人话化的参数值描述。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, list):
        inner = "、".join(_summarize_value(v) for v in value[:3])
        extra = f" 等 {len(value)} 项" if len(value) > 3 else ""
        return f"[{inner}{extra}]"
    if isinstance(value, dict):
        keys = list(value.keys())[:5]
        return "{" + "、".join(keys) + ("…" if len(value) > 5 else "") + "}"
    return _clip(str(value))


def _summary_for_builtin(tool_name: str, args: dict) -> list[str]:
    """内置工具的参数要点提取(按工具定制)。"""
    lines: list[str] = []
    if tool_name == "code_execution":
        network = bool(args.get("network", False))
        lines.append(
            "是否联网: 是(代码将访问互联网)" if network
            else "是否联网: 否(默认隔离, 不能访问网络)"
        )
        code = str(args.get("code", ""))
        if code:
            lines.append(f"代码开头: {_code_first_meaningful_line(code)}")
        timeout = args.get("timeout")
        if timeout:
            lines.append(f"最长运行 {timeout} 秒后自动停止")
        return lines
    if tool_name in ("file_write", "file_read", "read_artifact"):
        target = args.get("path") or args.get("file_path") or args.get("filename")
        if target:
            lines.append(f"涉及文件: {target}")
        content = args.get("content")
        if isinstance(content, str) and content:
            lines.append(f"写入内容约 {len(content)} 字(详见技术详情)")
        return lines
    # 通用: 逐参数人话展示
    return _summary_generic(args)


def _summary_generic(args: dict) -> list[str]:
    """通用参数要点: 已知键用中文释义, 未知键保留原名。"""
    lines: list[str] = []
    for key, value in args.items():
        if key in _SKIP_ARGS or key.startswith("_"):
            continue
        label = _ARG_LABELS.get(key, key)
        text = _summarize_value(value)
        if not text:
            continue
        lines.append(f"{label}: {text}")
        if len(lines) >= _MAX_SUMMARY_LINES:
            lines.append("…(更多参数见技术详情)")
            break
    return lines


def _label_for_tool(tool_name: str) -> tuple[str, str | None]:
    """返回 (工具中文名, mcp server 中文名或 None)。"""
    m = _MCP_NAME_RE.match(tool_name)
    if m:
        server = m.group("server").lower()
        server_label = MCP_SERVER_LABELS.get(server)
        display_server = server_label or m.group("server")
        return f"外部工具·{display_server}", server_label
    return TOOL_LABELS.get(tool_name, tool_name), None


def humanize_confirmation(tool_name: str, args: dict | None) -> dict:
    """生成确认卡片的人性化描述。

    Args:
        tool_name: 工具名(内置名或 mcp__{server}__{tool})。
        args: 工具调用参数。

    Returns:
        {"title": str, "summary": list[str], "tool_label": str}
        任何内部异常都吞掉, 兜底返回最小可用描述(确认流程绝不能因此中断)。
    """
    args = args or {}
    try:
        tool_label, server_label = _label_for_tool(tool_name)

        # 标题
        m = _MCP_NAME_RE.match(tool_name)
        if tool_name == "code_execution":
            network = bool(args.get("network", False))
            title = "运行一段 Python 代码" + ("(需要联网)" if network else "(不联网)")
        elif m:
            server_disp = server_label or m.group("server")
            title = f"调用外部工具: {server_disp} · {m.group('tool')}"
        else:
            title = tool_label

        # 要点
        if tool_name.startswith("mcp__"):
            summary = _summary_generic(args)
        else:
            summary = _summary_for_builtin(tool_name, args)

        return {"title": title, "summary": summary, "tool_label": tool_label}
    except Exception:  # noqa: BLE001 - 兜底: 绝不阻塞确认流程
        return {"title": tool_name, "summary": [], "tool_label": tool_name}
