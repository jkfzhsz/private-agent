"""system_capabilities 内置工具: PA 智能体自身认知查询(2026-08-13)。

背景: PA 智能体(子瞻/白圭/清和)对自身系统认知不足(清和把记忆宫殿 ChromaDB
误判为 PG、拿关键词检索当语义检索)。本工具让模型在不确定自身数据系统/工具
能力/操作渠道时, 按需查询"说明书"(单一事实源 core/capability_map.py)。

设计(见 docs/next-phase-plan-2026-08-13-agent-self-awareness.md v2):
- 按需自省, 不启动注入、不周期强调; 会话上下文内缓存复用(查到一次即记住)。
- is_kernel + _ALWAYS_AVAILABLE_TOOLS 白名单豁免, 保证始终可见(不被裁剪)。
- aspect: storage(三系统边界)/tools(工具边界)/channels(渠道)/state(运行时快照)/all。
"""
from __future__ import annotations

from private_agent.config import loader
from private_agent.core.capability_map import (
    build_capability_text,
    build_state_snapshot,
)
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["SYSTEM_CAPABILITIES_TOOL"]

_ASPECTS = {"storage", "tools", "channels", "state", "all"}


async def _system_capabilities_handler(args: dict) -> ToolResult:
    """查询 PA 自身能力说明书(三系统边界/工具边界/渠道/运行时状态)。"""
    aspect = (args.get("aspect") or "all").strip().lower()
    if aspect not in _ASPECTS:
        aspect = "all"

    snapshot_text = ""
    if aspect in ("state", "all"):
        try:
            cfg = loader.load_config()
            conn = await db.connect(cfg)
        except Exception as e:
            return ToolResult(
                output="",
                error=f"Database connection failed: {type(e).__name__}: {e}",
            )
        try:
            snapshot_text = await build_state_snapshot(conn, cfg)
        except Exception as e:
            snapshot_text = f"(状态查询失败: {type(e).__name__})"
        finally:
            await conn.close()
    else:
        # storage/tools/channels 为纯静态, 不查 DB
        cfg = None
        try:
            cfg = loader.load_config()
        except Exception:
            cfg = None

    text = build_capability_text(aspect, snapshot_text)
    return ToolResult(output=text)


SYSTEM_CAPABILITIES_TOOL = ToolDef(
    name="system_capabilities",
    description=(
        "查询 PA 自身的系统说明书: 有三个独立数据存储系统(原生记忆 PostgreSQL/"
        "场景知识库 PostgreSQL/记忆宫殿 mempalace 是 ChromaDB), 职责不同勿混用。"
        "当你不确定自身有哪些数据系统、各工具的能力边界(如 memory_search 是"
        "关键词匹配非语义检索)、或某操作该走哪个通道(如知识库只能用户前端上传)时, "
        "调用本工具查询。aspect: storage(存储系统)/tools(工具边界)/channels(渠道)/"
        "state(运行时状态)/all(全部, 默认)。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "aspect": {
                "type": "string",
                "enum": ["storage", "tools", "channels", "state", "all"],
                "description": (
                    "查询维度: storage=三系统边界; tools=工具能力边界; "
                    "channels=操作渠道; state=运行时状态(知识库/记忆/embedding); "
                    "all=全部(默认)。"
                ),
            },
        },
    },
    handler=_system_capabilities_handler,
    is_kernel=True,  # 自我认知入口: 始终可见, 不被 ToolSelector 裁剪
    safety_level="none",
)
