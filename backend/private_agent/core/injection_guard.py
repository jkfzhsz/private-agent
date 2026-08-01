"""蓝图 §3.12 提示注入防护机制 — 三层防护(role 隔离 + 长度截断 + 关键词过滤)。

B3 P0-2: 中英文高危/低风险模式正则匹配,高危推送 WS 告警 + 入库,低风险仅日志。
沙箱与 MCP 工具差异化处理(2k vs 4k token 截断)。
告警不阻断:命中高危告警不打断 ReAct 循环,仅记录 + UI 告警。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# 高危模式:角色劫持、清空前置指令 → 推送 UI 告警 + 入库记录(蓝图 §3.12)
HIGH_RISK_PATTERNS = [
    # 英文
    r"ignore\s+(previous|above|prior)\s+(instructions?|prompt)",
    r"disregard\s+(above|prior|previous)",
    r"you\s+are\s+now\s+(a|an)\s+\w+",
    r"<\s*system\s*>",
    # 中文
    r"忽略(前面|以上|上文|全部).*指令",
    r"无视前文所有设定",
    r"你现在切换成(管理员|开发者|系统)",
]

# 低风险模式:单纯关键词 → 仅日志记录,不推送前端(蓝图 §3.12)
LOW_RISK_PATTERNS = [
    r"system\s*:\s*",
    r"系统指令[:：]",
]

MAX_TOOL_RESULT_TOKENS_MCP = 4000
MAX_TOOL_RESULT_TOKENS_SANDBOX = 2000


@dataclass
class InjectionAlert:
    pattern: str
    call_id: str
    risk: Literal["high", "low"]
    source: Literal["mcp", "sandbox"]
    snippet: str


@dataclass
class InjectionScanResult:
    high_alerts: list[InjectionAlert] = field(default_factory=list)
    low_alerts: list[InjectionAlert] = field(default_factory=list)


class InjectionGuard:
    """注入防护执行器(蓝图 §3.12)。

    三层防护:
    1. role 隔离(OpenAI 格式天然支持,无需额外实现)
    2. 长度截断(按工具来源差异化:沙箱 2k, MCP 4k)
    3. 关键词过滤(中英文 + 高低风险分级)
    """

    def _get_truncation_limit(self, source: str) -> int:
        return MAX_TOOL_RESULT_TOKENS_SANDBOX if source == "sandbox" else MAX_TOOL_RESULT_TOKENS_MCP

    def truncate_tool_result(self, result: str, source: str) -> str:
        limit = self._get_truncation_limit(source)
        if len(result) <= limit * 3:
            return result
        truncated = result[: limit * 3]
        return f"{truncated}\n\n[Result truncated: original ~{len(result)} chars]"

    def scan(
        self, tool_result: str, call_id: str, source: Literal["mcp", "sandbox"] = "mcp"
    ) -> InjectionScanResult:
        high_alerts = []
        low_alerts = []
        for pattern in HIGH_RISK_PATTERNS:
            if re.search(pattern, tool_result, re.IGNORECASE):
                high_alerts.append(
                    InjectionAlert(
                        pattern=pattern,
                        call_id=call_id,
                        risk="high",
                        source=source,
                        snippet=tool_result[:200],
                    )
                )
        for pattern in LOW_RISK_PATTERNS:
            if re.search(pattern, tool_result, re.IGNORECASE):
                low_alerts.append(
                    InjectionAlert(
                        pattern=pattern,
                        call_id=call_id,
                        risk="low",
                        source=source,
                        snippet=tool_result[:200],
                    )
                )
        return InjectionScanResult(high_alerts=high_alerts, low_alerts=low_alerts)

    def is_enabled(self, cfg: dict) -> bool:
        return cfg.get("injection_guard", {}).get("enabled", True)