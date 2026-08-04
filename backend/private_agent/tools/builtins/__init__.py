"""蓝图 §5.x / spec m2-tools-lifecycle - 9 类内置工具注册。

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
from private_agent.tools.builtins.search_knowledge import SEARCH_KNOWLEDGE_TOOL
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
    "SEARCH_KNOWLEDGE_TOOL",
    "WEB_SEARCH_TOOL",
    "READ_ARTIFACT_TOOL",
]


def register_all_builtins(registry: ToolRegistry) -> None:
    """注册所有 9 类内置工具到 ToolRegistry。

    阶段三批次3(T3.3, 调研 round2 §4.3.1): 内核/非内核标记 ——
    高频基础能力(calculator/datetime/web_search/code_execution/file_*
    /http_request)标记 is_kernel=True(ToolSelector 隐含锚点始终注入);
    场景相关工具(search_knowledge/read_artifact)保持 False, 靠关键词/
    历史评分竞争 top-N —— 实现"非场景工具不主动注入"的下沉效果。

    Args:
        registry: ToolRegistry 实例。
    """
    # 内核工具: 高频基础能力, 始终注入模型
    for td in (
        CALCULATOR_TOOL,
        CODE_EXECUTION_TOOL,
        DATETIME_TOOL,
        FILE_READ_TOOL,
        FILE_WRITE_TOOL,
        HTTP_REQUEST_TOOL,
        WEB_SEARCH_TOOL,
    ):
        td.is_kernel = True
    # 场景相关工具: 下沉为 Skill 可选(office/data_analysis 声明依赖时启用)
    SEARCH_KNOWLEDGE_TOOL.is_kernel = False
    READ_ARTIFACT_TOOL.is_kernel = False
    tools = [
        CALCULATOR_TOOL,
        CODE_EXECUTION_TOOL,
        DATETIME_TOOL,
        FILE_READ_TOOL,
        FILE_WRITE_TOOL,
        HTTP_REQUEST_TOOL,
        SEARCH_KNOWLEDGE_TOOL,
        WEB_SEARCH_TOOL,
        READ_ARTIFACT_TOOL,
    ]
    for td in tools:
        registry.register_builtin(td.name, td)