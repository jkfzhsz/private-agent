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
    """

    name: str
    description: str
    parameters_schema: dict
    handler: Callable[[dict], Awaitable[ToolResult]]
    safety_level: str = "none"

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
