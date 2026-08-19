"""PA 智能体自身认知说明书生成器（Agent Self-Awareness, 2026-08-13）。

背景（见 memory-kb-storage-诊断报告-2026-08-13）：PA 智能体(子瞻/白圭/清和)
对自身系统认知不足——清和把记忆宫殿(ChromaDB)误判为 PG 通道、拿关键词检索
当语义检索、不知道知识库写入只有前端通道。

本模块是"单一事实源"，供 system_capabilities 工具按需生成：
- 三系统边界(storage)
- 工具能力边界(tools)
- 操作渠道(channels)
- 运行时快照(state, 实时查 DB/config)

原则(AI-Agents-in-Depth §2.6)：代码维护、键值/短句格式、数据来自真实运行状态。
不启动注入、不周期强调，由模型按需调用 system_capabilities 查询(见设计文档
docs/next-phase-plan-2026-08-13-agent-self-awareness.md)。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "STORAGE_MAP_TEXT",
    "TOOL_BOUNDARY_TEXT",
    "CHANNELS_TEXT",
    "build_state_snapshot",
    "build_capability_text",
]

# ── 静态：三系统边界 ──────────────────────────────────────────────────────
STORAGE_MAP_TEXT = """PA 有三个独立数据存储系统, 职责不同, 不可混用:
1. 原生记忆(PostgreSQL user_memories): 用户画像/偏好/事实/纠正, 轻量文本。
   读=memory_search(关键词子串匹配, 须用与原文相同措辞); 写=memory_save。
2. 场景知识库(PostgreSQL kb_documents/kb_chunks, 向量语义检索): 长文档 RAG。
   读=search_knowledge(语义检索, 措辞不同也能召回); 写=仅用户前端上传, 你无写工具。
3. 记忆宫殿(mempalace, 独立 ChromaDB 向量库, 外部 MCP 服务): 知识抽屉/知识图谱。
   读=mempalace_search/get_drawer; 写=mempalace_add_drawer 等。
   注意: 记忆宫殿不是 PostgreSQL, 与 1/2 无共享数据, 不可拿它推断 PG 状态。"""

# ── 静态：工具能力边界 ──────────────────────────────────────────────────────
TOOL_BOUNDARY_TEXT = """关键工具能力边界(用错会得出错误结论):
- memory_search: 关键词子串匹配, 非语义检索。多词查询按空格分词, 任一命中即可;
  单词查询须为内容的连续子串。"查不到" ≠ "没写入", 先换内容原词再查。
- search_knowledge: 向量+关键词+reranker 混合语义检索, 措辞不同也能召回; 仅只读。
- memory_save: 写入原生记忆(scope: global/office/data_analysis/frontend_design)。
- mempalace_*: 记忆宫殿读写, 与 PA 原生记忆/知识库互不相通。
  查持仓/个人档案等结构化事实, 优先用 mempalace_kg_query(知识图谱按 subject/谓词精确查询)
  或 mempalace_list_drawers/get_drawer(带 wing/room 过滤精确列举);
  不要只依赖 mempalace_search(抽屉语义检索对中文可能召回不准)。"""

# ── 静态：操作渠道 ──────────────────────────────────────────────────────────
CHANNELS_TEXT = """操作渠道:
- 你能直接做: 原生记忆读写(memory_save/memory_search)、知识库检索(search_knowledge)、
  记忆宫殿读写(mempalace_*)、文件读写(file_read/file_write)、代码执行(code_execution)、
  网页搜索(web_search)。
- 你不能做: 知识库写入(仅用户前端知识库管理页上传)、模型/工具/MCP 装配修改(仅用户设置页)、
  打包部署(仅用户手动执行 build-electron.bat)。
- 自我修复链路(遇到 bug 时, 2026-08-13 蒋先生确认): ①诊断(日志/代码/DB) → ②写修复方案 →
  ③file_write 改源码(仅 backend/ 与 frontend/ 目录, 会触发用户权限确认) →
  ④code_execution 语法验证 → ⑤明确告知用户"关闭 PA 并重新打包(build-electron.bat)"。
  注意: 你改的是源码, 当前运行的可能是打包版副本, 改完不立即生效, 必须等用户重新打包;
  同一问题最多改 2 次, 第 3 次仍失败请停止并求助 WorkBuddy; 禁止改 config.yaml/.env/数据文件。
- 不确定自身能力/装配时: 调用 system_capabilities 查询。"""


async def build_state_snapshot(
    conn: Any,
    cfg: dict[str, Any] | None = None,
) -> str:
    """运行时快照: 知识库统计 + 记忆分布 + embedding 模型 + MCP 声明。

    Args:
        conn: asyncpg 连接。
        cfg: 合并后的配置 dict(默认 None)。

    Returns:
        快照文本(短句/键值风格)。
    """
    cfg = cfg or {}
    lines: list[str] = []

    # 知识库统计(KB 文档/片段, 按场景)
    try:
        from private_agent.knowledge.kb_repo import KnowledgeBaseRepo

        stats = await KnowledgeBaseRepo(conn).get_stats()
        scenes = stats.get("scenarios", {})
        scene_parts = []
        for s, v in sorted(scenes.items()):
            scene_parts.append(f"{s} {v.get('docs', 0)}文档/{v.get('chunks', 0)}片段")
        kb_line = f"知识库: 共 {stats.get('total_documents', 0)} 文档/{stats.get('total_chunks', 0)} 片段"
        if scene_parts:
            kb_line += " (" + " | ".join(scene_parts) + ")"
        lines.append(kb_line)
    except Exception:  # noqa: BLE001 - 快照单项失败不影响整体
        lines.append("知识库: 统计不可用")

    # 记忆分布(活跃数 + 按 scope)
    try:
        from private_agent.memory.memories_repo import MemoriesRepo

        mstats = await MemoriesRepo(conn).memory_stats()
        by_scope = mstats.get("by_scope", {})
        scope_parts = ", ".join(
            f"{k}={v}" for k, v in sorted(by_scope.items())
        ) or "无"
        lines.append(
            f"原生记忆: 活跃 {mstats.get('active', 0)} / 归档 {mstats.get('archived', 0)} "
            f"(scope 分布: {scope_parts})"
        )
    except Exception:  # noqa: BLE001
        lines.append("原生记忆: 统计不可用")

    # embedding 模型(静态配置, 单一模型纪律)
    emb = (cfg.get("knowledge") or {}).get("embedding") or {}
    model = emb.get("local_default", "BAAI/bge-small-zh-v1.5")
    dim = emb.get("storage_dim", 1024)
    lines.append(f"embedding: {model}(存储 {dim} 维)")

    # MCP 声明(静态配置; 空则省略)
    servers = (cfg.get("tools") or {}).get("mcp", {}).get("servers", []) or []
    if servers:
        ids = [s.get("id") or s.get("name") for s in servers if s.get("id") or s.get("name")]
        if ids:
            lines.append(f"MCP 声明: {', '.join(ids)}")

    return "\n".join(lines)


def build_capability_text(aspect: str, snapshot_text: str = "") -> str:
    """按 aspect 拼装能力说明书文本。

    Args:
        aspect: storage / tools / channels / state / all。
        snapshot_text: state 快照文本(build_state_snapshot 结果)。

    Returns:
        格式化文本。
    """
    blocks: list[str] = []
    if aspect in ("storage", "all"):
        blocks.append("## 存储系统\n" + STORAGE_MAP_TEXT)
    if aspect in ("tools", "all"):
        blocks.append("## 工具能力边界\n" + TOOL_BOUNDARY_TEXT)
    if aspect in ("channels", "all"):
        blocks.append("## 操作渠道\n" + CHANNELS_TEXT)
    if aspect in ("state", "all"):
        blocks.append("## 运行时状态\n" + (snapshot_text or "(无快照)"))
    if not blocks:
        # 未知 aspect 兜底返回全部
        blocks = [
            "## 存储系统\n" + STORAGE_MAP_TEXT,
            "## 工具能力边界\n" + TOOL_BOUNDARY_TEXT,
            "## 操作渠道\n" + CHANNELS_TEXT,
            "## 运行时状态\n" + (snapshot_text or "(无快照)"),
        ]
    return "\n\n".join(blocks)
