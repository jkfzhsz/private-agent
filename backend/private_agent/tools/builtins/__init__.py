"""蓝图 §5.x / spec m2-tools-lifecycle - 8 类内置工具注册。

注册所有内置工具到 ToolRegistry, 供 ReAct 循环调用。
"""
from __future__ import annotations

from private_agent.tools.builtins.calculator import CALCULATOR_TOOL
from private_agent.tools.builtins.code_execution import CODE_EXECUTION_TOOL
from private_agent.tools.builtins.datetime import DATETIME_TOOL
from private_agent.tools.builtins.file_read import FILE_READ_TOOL
from private_agent.tools.builtins.file_write import FILE_WRITE_TOOL
from private_agent.tools.builtins.http_request import HTTP_REQUEST_TOOL
from private_agent.tools.builtins.read_artifact import READ_ARTIFACT_TOOL
from private_agent.tools.builtins.web_search import WEB_SEARCH_TOOL
from private_agent.tools.registry import ToolRegistry

__all__ = [
    "register_all_builtins",
    "CALCULATOR_TOOL",
    "CODE_EXECUTION_TOOL",
    "DATETIME_TOOL",
    "FILE_READ_TOOL",
    "FILE_WRITE_TOOL",
    "HTTP_REQUEST_TOOL",
    "WEB_SEARCH_TOOL",
    "READ_ARTIFACT_TOOL",
]


def register_all_builtins(registry: ToolRegistry) -> None:
    """注册所有 8 类内置工具到 ToolRegistry。

    Args:
        registry: ToolRegistry 实例。
    """
    tools = [
        CALCULATOR_TOOL,
        CODE_EXECUTION_TOOL,
        DATETIME_TOOL,
        FILE_READ_TOOL,
        FILE_WRITE_TOOL,
        HTTP_REQUEST_TOOL,
        WEB_SEARCH_TOOL,
        READ_ARTIFACT_TOOL,
    ]
    for td in tools:
        registry.register_builtin(td.name, td)