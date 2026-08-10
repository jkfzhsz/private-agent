"""memory_save 内置工具: 0.5.1(2026-08-10) 原生记忆主动写入。

背景(蒋先生反馈): PA 原生记忆系统只有 memory_search(只读)与后台自动提取
(每 8 轮触发), AI 没有主动记忆工具 → 用户说"记录下来"时 AI 只能调用
记忆宫殿(MCP) → 原生场景记忆(office/data_analysis/frontend_design)一直
为空。本工具补上主动写入通道:

- content(必选): 记忆内容(事实/偏好/指令)。
- scope(可选): global / office / data_analysis / frontend_design(默认按
  当前会话场景, 无场景则 global)。
- importance(可选): 0-1, 默认 0.7(用户主动要求记住的内容价值较高)。
"""
from __future__ import annotations

from private_agent.config import loader
from private_agent.memory.memories_repo import MemoriesRepo, Memory
from private_agent.storage import db
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["MEMORY_SAVE_TOOL"]

SCENE_KEYS = {"global", "office", "data_analysis", "frontend_design"}


async def _memory_save_handler(args: dict) -> ToolResult:
    """写入一条用户记忆(原生记忆系统, 非记忆宫殿)。"""
    content = (args.get("content") or "").strip()
    if not content:
        return ToolResult(output="", error="content is required")
    if len(content) > 2000:
        content = content[:2000]

    scope = (args.get("scope") or "global").strip()
    if scope not in SCENE_KEYS:
        scope = "global"
    try:
        importance = float(args.get("importance", 0.7))
    except (TypeError, ValueError):
        importance = 0.7
    importance = max(0.0, min(1.0, importance))

    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Database connection failed: {type(e).__name__}: {e}",
        )
    try:
        repo = MemoriesRepo(conn)
        memory = Memory(
            type="fact",
            content=content,
            importance=importance,
            scope=scope,
            source_session_id=args.get("session_id"),
        )
        memory_id = await repo.insert(memory)
    except Exception as e:
        return ToolResult(
            output="",
            error=f"Memory save failed: {type(e).__name__}: {e}",
        )
    finally:
        await conn.close()

    return ToolResult(
        output=(
            f"已保存到原生记忆(scope={scope}, id={memory_id})。"
            f"该记忆将在对应场景会话中自动注入/可检索。"
        )
    )


MEMORY_SAVE_TOOL = ToolDef(
    name="memory_save",
    description=(
        "Save a fact/preference/instruction to the built-in user memory "
        "system (PostgreSQL user_memories). Use when the user explicitly "
        "asks to remember/record something (持仓、偏好、约定、指令等). "
        "scope determines which scene can retrieve it: global(all scenes) "
        "or office/data_analysis/frontend_design(scene-specific). "
        "Prefer this over external memory tools for user-requested memories."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "记忆内容(事实/偏好/指令), 建议 ≤500 字。",
            },
            "scope": {
                "type": "string",
                "enum": ["global", "office", "data_analysis", "frontend_design"],
                "description": "记忆归属场景: global 所有会话可用; 场景名仅该场景可用(默认 global)。",
            },
            "importance": {
                "type": "number",
                "description": "重要性 0-1(默认 0.7, 用户主动要求记的内容较高)。",
            },
        },
        "required": ["content"],
    },
    handler=_memory_save_handler,
)
