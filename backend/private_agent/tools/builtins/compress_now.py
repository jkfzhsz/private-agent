"""B-1 compress_now 工具 —— 模型自主压缩触发(设计文档 §3.2-B1)。

Deep Agents 思想: 固定阈值压缩常在坏时机触发(重构中途丢细节), 改为
模型在"完成一个交付物、准备开始新任务"时自主触发压缩。

配置(全部默认关闭, 可手动打开):
- context.compression.model_suggested: 信号开关(默认 false)
- context.compression.compress_now_tool: 工具注册开关(默认 false)

工具语义:
- handler 为纯标记(返回确认消息), 不直接执行压缩 —— ReactLoop 在
  _exec_plan 中检测到 compress_now 调用后置 self._compress_now_requested,
  本轮结束 _maybe_compress 以 model_suggested 信号触发真实压缩。
- safety_level=safe(仅触发压缩, 无文件/网络副作用)。
- is_kernel=True: 注册时始终注入模型(ToolSelector 锚点), 保证长任务
  中模型随时可见可用。
"""
from __future__ import annotations

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["COMPRESS_NOW_TOOL", "compress_now_handler"]


async def compress_now_handler(args: dict) -> ToolResult:
    """请求压缩(标记语义, 真实压缩由 ReactLoop 本轮结束执行)。"""
    return ToolResult(
        output=(
            "[compress_now] 压缩请求已记录: 本轮结束将压缩早期上下文, "
            "保留最近消息与关键事实。"
        )
    )


COMPRESS_NOW_TOOL = ToolDef(
    name="compress_now",
    description=(
        "主动压缩早期对话上下文, 释放 token 空间。当本任务的主要交付物已完成、"
        "即将开始新任务或后续轮次不再需要早期细节时调用; 压缩保留最近消息与关键事实。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "触发压缩的原因(可选, 供审计)。",
            }
        },
        "required": [],
    },
    handler=compress_now_handler,
    safety_level="safe",
    is_kernel=True,
)
