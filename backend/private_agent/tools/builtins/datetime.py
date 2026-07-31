"""datetime 内置工具:返回当前 UTC 时间 ISO 8601 格式。

从 M1 mock 升级为真实实现。
"""
from __future__ import annotations

from datetime import datetime, timezone

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["datetime_handler", "DATETIME_TOOL"]


async def datetime_handler(args: dict) -> ToolResult:
    """返回当前 UTC 时间的 ISO 8601 格式字符串。

    Args:
        args: 空 dict(无参数)。

    Returns:
        当前 UTC 时间的 ISO 8601 格式。
    """
    _ = args  # 未使用但保留签名一致性
    now = datetime.now(timezone.utc).isoformat()
    return ToolResult(output=now)


DATETIME_TOOL = ToolDef(
    name="datetime",
    description="Get current UTC datetime in ISO 8601 format.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    handler=datetime_handler,
)