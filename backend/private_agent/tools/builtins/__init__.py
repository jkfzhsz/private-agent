"""蓝图 §5.x / spec m2-tools-lifecycle - 内置工具注册。

注册所有内置工具到 ToolRegistry, 供 ReAct 循环调用。
"""
from __future__ import annotations

from private_agent.tools.builtins.calculator import CALCULATOR_TOOL
from private_agent.tools.builtins.code_execution import CODE_EXECUTION_TOOL
from private_agent.tools.builtins.datetime import DATETIME_TOOL
from private_agent.tools.builtins.evolution_tools import EVOLUTION_TOOLS
from private_agent.tools.builtins.file_read import FILE_READ_TOOL
from private_agent.tools.builtins.file_write import FILE_WRITE_TOOL
from private_agent.tools.builtins.http_request import HTTP_REQUEST_TOOL
from private_agent.tools.builtins.memory_search import MEMORY_SEARCH_TOOL
from private_agent.tools.builtins.memory_save import MEMORY_SAVE_TOOL
from private_agent.tools.builtins.monitor_tools import MONITOR_TOOLS
from private_agent.tools.builtins.read_artifact import READ_ARTIFACT_TOOL
from private_agent.tools.builtins.search_knowledge import SEARCH_KNOWLEDGE_TOOL
from private_agent.tools.builtins.search_lessons import SEARCH_LESSONS_TOOL
from private_agent.tools.builtins.system_capabilities import SYSTEM_CAPABILITIES_TOOL
from private_agent.tools.builtins.web_search import WEB_SEARCH_TOOL
from private_agent.tools.registry import ToolRegistry

__all__ = [
    "register_all_builtins",
    "register_monitor_tools",
    "CALCULATOR_TOOL",
    "CODE_EXECUTION_TOOL",
    "DATETIME_TOOL",
    "FILE_READ_TOOL",
    "FILE_WRITE_TOOL",
    "HTTP_REQUEST_TOOL",
    "MEMORY_SEARCH_TOOL",
    "MEMORY_SAVE_TOOL",
    "SEARCH_KNOWLEDGE_TOOL",
    "WEB_SEARCH_TOOL",
    "READ_ARTIFACT_TOOL",
    "MONITOR_TOOLS",
    "SEARCH_LESSONS_TOOL",
    "EVOLUTION_TOOLS",
    "SYSTEM_CAPABILITIES_TOOL",
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
    # 0.5.0 M1: memory_search 为记忆按需检索工具(非内核, 模型主动调用)
    MEMORY_SEARCH_TOOL.is_kernel = False
    # 0.5.1: memory_save 原生记忆主动写入 —— 设为内核(始终注入)。
    # 蒋先生反馈(2026-08-10): 非内核工具靠 top-N 竞争, 新工具无历史评分
    # 被筛掉 → 白圭"工具列表没有 memory_save"。记忆写入是跨场景基础能力,
    # 用户要求"记住"时必须可用, 与 file_read/file_write 同级常驻。
    MEMORY_SAVE_TOOL.is_kernel = True
    # Phase 1(2026-08-11): search_lessons 经验检索 —— 非内核, 模型主动调用
    # (与 memory_search 同模式: 历史经验不常驻, 任务相关时按需检索)。
    SEARCH_LESSONS_TOOL.is_kernel = False
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
        MEMORY_SEARCH_TOOL,
        MEMORY_SAVE_TOOL,
        SEARCH_LESSONS_TOOL,
        SYSTEM_CAPABILITIES_TOOL,
    ]
    for td in tools:
        registry.register_builtin(td.name, td)


def register_monitor_tools(registry: ToolRegistry) -> None:
    """0.5.0 P1: 主智能体监控工具注册(monitor 会话专属白名单)。

    与 register_all_builtins 独立: 不进入通用 12 内置计数,
    仅由 P3 的 kind='monitor' 会话装配时追加, 保证场景会话
    (子瞻/白圭/清和)不会暴露系统级工具。

    Phase 4(2026-08-11): 追加 EVOLUTION_TOOLS(lessons_stats/
    review_queue_summary), 无涯·项目进化者专属进化调度工具。
    """
    for td in MONITOR_TOOLS:
        registry.register_builtin(td.name, td)
    for td in EVOLUTION_TOOLS:
        registry.register_builtin(td.name, td)