"""蓝图 §3.8 / §9.6 step 8 - ToolDef schema + mock 工具。

Source: spec/m1-react-loop AC-1 + Solution 模块划分
- ToolDef: OpenAI 2020-12 兼容 function calling schema
- ToolResult: 工具执行结果统一结构
- ECHO_TOOL / DATETIME_TOOL: M1 mock 工具(演示 tool_call/tool_result)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

__all__ = ["ToolDef", "ToolResult", "ECHO_TOOL", "DATETIME_TOOL"]


@dataclass
class ToolResult:
    """工具执行结果。

    output: 工具输出文本(注入 LLM 上下文)
    error: 失败原因(成功时为 None)
    metadata: 额外元数据(计时/调试信息)
    """

    output: str
    error: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ToolDef:
    """蓝图 §3.8 / §5.12 工具定义 schema(OpenAI 2020-12 兼容)。

    handler 签名: async (args: dict) -> ToolResult

    safety_level(蓝图 §5.12 权限分级):
    - "none"/"safe": 自动执行,不打断 Agent
    - "elevated": 执行前 WS 推送确认请求(60s 超时拒绝 + 会话级缓存)
    - "dangerous": 直接拦截

    risk_level(阶段三批次1 B-8, 调研 round2 §4.2.3): 确认卡片风险分级展示
    - "low" | "medium" | "high"; 默认 "medium"; 由 assess_risk 结合参数启发式。

    is_kernel(阶段三批次3 T3.3, 调研 round2 §4.3.1): 内核工具标记
    - True: ToolSelector 作为隐含锚点始终注入(高频基础能力)
    - False: 靠关键词/历史评分竞争 top-N(场景相关工具下沉)
    默认 False: 仅内置工具注册时显式标记内核, 避免测试/扩展工具意外变锚点。
    """

    name: str
    description: str
    parameters_schema: dict
    handler: Callable[[dict], Awaitable[ToolResult]]
    safety_level: str = "none"
    risk_level: str = "medium"
    is_kernel: bool = False

    def to_openai_schema(self) -> dict:
        """转换为 OpenAI tools 参数格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


# 高风险参数启发式: (工具名, args 键, 值子串) → 升为 high(阶段三批次1 B-8)
_HIGH_RISK_HINTS = (
    ("file_write", "path", ".env"),
    ("file_read", "path", ".env"),
    ("http_request", "url", "169.254.169.254"),
    ("http_request", "url", "127.0.0.1"),
    ("http_request", "url", "localhost"),
    ("code_execution", "code", "rm -rf"),
    ("code_execution", "code", "os.remove"),
)


def assess_risk(tool_def: ToolDef, args: dict | None = None) -> str:
    """评估工具调用的风险分级(B-8, 纯函数)。

    规则:
    - ToolDef.risk_level 为 high → 保持 high;
    - 否则按参数启发式(路径含 .env / 内网 URL / 危险命令片段)升为 high;
    - ToolDef.risk_level 为 low 且无启发式命中 → low;
    - 默认 → medium。

    Args:
        tool_def: 工具定义。
        args: 本次调用的参数(可选, 用于启发式)。

    Returns:
        "low" | "medium" | "high"。
    """
    base = getattr(tool_def, "risk_level", "medium")
    if base == "high":
        return "high"
    args = args or {}
    for tool_name, key, hint in _HIGH_RISK_HINTS:
        if tool_def.name == tool_name:
            value = args.get(key)
            if isinstance(value, str) and hint in value:
                return "high"
    return base


# ──────────────────────────────────────────────────────────────────────────────
# M1 mock 工具:echo / datetime(蓝图 §9.6 step 8)
# ──────────────────────────────────────────────────────────────────────────────


async def _echo_handler(args: dict) -> ToolResult:
    """echo 工具:回显输入 text。"""
    return ToolResult(output=str(args.get("text", "")))


async def _datetime_handler(args: dict) -> ToolResult:
    """datetime 工具:返回当前 ISO 8601 时间。"""
    from datetime import datetime, timezone

    return ToolResult(output=datetime.now(timezone.utc).isoformat())


ECHO_TOOL = ToolDef(
    name="echo",
    description="Echo back the input text. Useful for testing tool_call flow.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to echo back.",
            }
        },
        "required": ["text"],
    },
    handler=_echo_handler,
)

DATETIME_TOOL = ToolDef(
    name="datetime",
    description="Get current UTC datetime in ISO 8601 format.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=_datetime_handler,
)
