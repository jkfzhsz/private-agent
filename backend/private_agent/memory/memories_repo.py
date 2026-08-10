"""蓝图 §4.3 user_memories 表 CRUD 操作。

表结构(蓝图 §2.10 + §4.3):
- id BIGSERIAL PRIMARY KEY
- user_id BIGINT NOT NULL (单人场景固定为 1)
- type VARCHAR(20) NOT NULL (preference/fact/todo/decision/correction)
- content TEXT NOT NULL
- importance FLOAT DEFAULT 0.5
- source_session_id BIGINT (来源会话,评估溯源)
- created_at TIMESTAMPTZ DEFAULT NOW()
- last_accessed_at TIMESTAMPTZ DEFAULT NOW()
- access_count INT DEFAULT 0
- is_active BOOLEAN DEFAULT TRUE
- scope VARCHAR(20) DEFAULT 'global' (0.5.0 M1 场景独立:
  global=全局记忆; office/data_analysis/frontend_design=场景私有)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

__all__ = ["Memory", "MemoriesRepo"]


# 蓝图 §4.3 记忆类型枚举
# 阶段三批次3(T3.4, 调研 round2 §4.4.1): 新增 correction 类型(用户纠正沉淀,
# 高价值信号, importance 默认 high)
MEMORY_TYPES = frozenset({"preference", "fact", "todo", "decision", "correction"})

# 蓝图 §4.3 importance 初始值规则
TYPE_IMPORTANCE_MAP: dict[str, float] = {
    "decision": 0.9,
    "correction": 0.9,  # 阶段三: 用户纠正是高价值信号
    "fact": 0.7,
    "preference": 0.6,
    "todo": 0.5,
}

# 0.5.0 M1: 场景技术标识(与 sessions.locked_skill_name / kb_documents.scenario 同源)
SCENE_KEYS: frozenset[str] = frozenset(
    {"office", "data_analysis", "frontend_design"}
)
# 场景中文名映射(仅显示层/提取归属展示用; 后端存储一律技术标识)
SCENE_NAMES: dict[str, str] = {
    "office": "子瞻",
    "data_analysis": "白圭",
    "frontend_design": "清和",
}


@dataclass
class Memory:
    """用户记忆数据类(蓝图 §4.3)。"""

    id: int | None = None
    user_id: int = 1
    type: str = "fact"
    content: str = ""
    importance: float = 0.5
    source_session_id: int | None = None
    created_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0
    is_active: bool = True
    scope: str = "global"


class MemoriesRepo:
    """user_memories 表 CRUD 操作(蓝图 §4.3)。"""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, memory: Memory) -> int:
        """插入单条记忆,返回 id。

        Args:
            memory: Memory 实例(type/content/importance/source_session_id/scope)。

        Returns:
            新记录的 id。
        """
        if memory.type not in MEMORY_TYPES:
            raise ValueError(
                f"memory.type='{memory.type}' 不合法, 合法值: {sorted(MEMORY_TYPES)}"
            )
        scope = memory.scope if memory.scope in SCENE_KEYS or memory.scope == "global" else "global"
        return await self._conn.fetchval(
            """
            INSERT INTO user_memories
                   (user_id, type, content, importance, source_session_id, scope)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            memory.user_id,
            memory.type,
            memory.content,
            memory.importance,
            memory.source_session_id,
            scope,
        )

    async def batch_insert(self, memories: list[Memory]) -> list[int]:
        """批量插入记忆,返回 id 列表。

        Args:
            memories: Memory 列表。

        Returns:
            新记录的 id 列表。
        """
        ids: list[int] = []
        async with self._conn.transaction():
            for m in memories:
                mid = await self.insert(m)
                ids.append(mid)
        return ids

    async def get_top_active(
        self,
        user_id: int = 1,
        order_by: str = "importance DESC, last_accessed_at DESC",
        limit: int = 10,
        scope: str | None = None,
    ) -> list[Memory]:
        """查询活跃记忆,按重要性降序排列(蓝图 §4.5 注入用)。

        Args:
            user_id: 用户 ID(单人场景固定为 1)。
            order_by: 排序字段(默认 importance DESC, last_accessed_at DESC)。
            limit: 返回条数(默认 10)。
            scope: 0.5.0 M1 场景过滤(None=全部, 'global'=仅全局,
                   'office'/'data_analysis'/'frontend_design'=仅该场景)。

        Returns:
            Memory 列表。
        """
        if scope:
            rows = await self._conn.fetch(
                f"""
                SELECT id, user_id, type, content, importance, source_session_id,
                       created_at, last_accessed_at, access_count, is_active, scope
                FROM user_memories
                WHERE user_id = $1 AND is_active = TRUE AND scope = $2
                ORDER BY {order_by}
                LIMIT $3
                """,
                user_id,
                scope,
                limit,
            )
        else:
            rows = await self._conn.fetch(
                f"""
                SELECT id, user_id, type, content, importance, source_session_id,
                       created_at, last_accessed_at, access_count, is_active, scope
                FROM user_memories
                WHERE user_id = $1 AND is_active = TRUE
                ORDER BY {order_by}
                LIMIT $2
                """,
                user_id,
                limit,
            )
        return [self._row_to_memory(r) for r in rows]

    async def get_top_by_scopes(
        self,
        user_id: int = 1,
        scopes: list[str] | None = None,
        order_by: str = "importance DESC, last_accessed_at DESC",
        limit: int = 10,
    ) -> list[Memory]:
        """0.5.0 M1: 按多 scope 组合查询活跃记忆(注入策略: 全局画像 + 场景记忆)。

        用于场景会话注入: 一次查询 global + 场景 scope 的混合记忆,
        在 Python 侧做配额切分(全局 N 条 + 场景 M 条), 避免多次往返。

        Args:
            user_id: 用户 ID。
            scopes: 允许的 scope 列表(默认 [global]); 含场景时自动组合。
            order_by: 排序字段。
            limit: 每 scope 的返回条数(供上层分配配额, 此处宽松取大值)。

        Returns:
            Memory 列表(未按 scope 分组, 调用方用 m.scope 区分)。
        """
        if not scopes:
            scopes = ["global"]
        rows = await self._conn.fetch(
            f"""
            SELECT id, user_id, type, content, importance, source_session_id,
                   created_at, last_accessed_at, access_count, is_active, scope
            FROM user_memories
            WHERE user_id = $1 AND is_active = TRUE AND scope = ANY($2::varchar[])
            ORDER BY {order_by}
            LIMIT $3
            """,
            user_id,
            scopes,
            limit,
        )
        return [self._row_to_memory(r) for r in rows]

    async def get_global_core(
        self,
        user_id: int = 1,
        limit: int = 2,
    ) -> list[Memory]:
        """0.5.0 M1: 全局"身份 + 核心偏好"画像子集(注入策略常驻 1-3 条)。

        取 global scope 中 preference 类型 + 高 importance 的记忆
        (身份/协作偏好/沟通风格), 供场景会话头部常驻注入。

        Args:
            user_id: 用户 ID。
            limit: 返回条数(默认 2)。

        Returns:
            Memory 列表。
        """
        rows = await self._conn.fetch(
            """
            SELECT id, user_id, type, content, importance, source_session_id,
                   created_at, last_accessed_at, access_count, is_active, scope
            FROM user_memories
            WHERE user_id = $1 AND is_active = TRUE AND scope = 'global'
              AND type = 'preference'
            ORDER BY importance DESC, last_accessed_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [self._row_to_memory(r) for r in rows]

    async def count_active(self, user_id: int = 1) -> int:
        """统计活跃记忆数量(蓝图 §4.4 淘汰用)。

        Args:
            user_id: 用户 ID。

        Returns:
            活跃记忆总数。
        """
        row = await self._conn.fetchval(
            "SELECT COUNT(*) FROM user_memories WHERE user_id = $1 AND is_active = TRUE",
            user_id,
        )
        return row or 0

    async def deactivate_lowest(
        self, user_id: int, count: int
    ) -> list[Memory]:
        """按 importance 升序淘汰指定数量的记忆(蓝图 §4.4 条件 1)。

        0.5.0 M3: 返回完整 Memory 列表(含 content/scope)供驱逐前归档。

        Args:
            user_id: 用户 ID。
            count: 淘汰数量。

        Returns:
            被淘汰记忆的 Memory 列表。
        """
        rows = await self._conn.fetch(
            """
            UPDATE user_memories
            SET is_active = FALSE
            WHERE id IN (
                SELECT id FROM user_memories
                WHERE user_id = $1 AND is_active = TRUE
                ORDER BY importance ASC, last_accessed_at ASC
                LIMIT $2
            )
            RETURNING id, user_id, type, content, importance, source_session_id,
                      created_at, last_accessed_at, access_count, is_active, scope
            """,
            user_id,
            count,
        )
        return [self._row_to_memory(r) for r in rows]

    async def deactivate_expired(
        self,
        user_id: int,
        min_importance: float = 0.3,
        cutoff: datetime | None = None,
    ) -> list[Memory]:
        """淘汰低重要性 + 长期未访问的记忆(蓝图 §4.4 条件 2)。

        0.5.0 M3: 返回完整 Memory 列表(含 content/scope)供驱逐前归档。

        Args:
            user_id: 用户 ID。
            min_importance: 重要性阈值(低于此值且超期则淘汰)。
            cutoff: 超期时间点(默认 30 天前)。

        Returns:
            被淘汰记忆的 Memory 列表。
        """
        if cutoff is None:
            cutoff = datetime.now(timezone.utc)
        rows = await self._conn.fetch(
            """
            UPDATE user_memories
            SET is_active = FALSE
            WHERE user_id = $1 AND is_active = TRUE
              AND importance < $2
              AND last_accessed_at < $3
            RETURNING id, user_id, type, content, importance, source_session_id,
                      created_at, last_accessed_at, access_count, is_active, scope
            """,
            user_id,
            min_importance,
            cutoff,
        )
        return [self._row_to_memory(r) for r in rows]

    async def batch_update_access(self, memories: list[Memory]) -> None:
        """批量更新访问记录(last_accessed_at/access_count)(蓝图 §4.5)。

        Args:
            memories: 被注入会话的记忆列表。
        """
        now = datetime.now(timezone.utc)
        async with self._conn.transaction():
            for m in memories:
                if m.id is not None:
                    await self._conn.execute(
                        """
                        UPDATE user_memories
                        SET last_accessed_at = $1, access_count = access_count + 1
                        WHERE id = $2
                        """,
                        now,
                        m.id,
                    )

    async def get_by_ids(self, ids: list[int]) -> list[Memory]:
        """按 id 列表查询记忆(评估溯源用)。

        Args:
            ids: 记忆 id 列表。

        Returns:
            Memory 列表。
        """
        if not ids:
            return []
        rows = await self._conn.fetch(
            """
            SELECT id, user_id, type, content, importance, source_session_id,
                   created_at, last_accessed_at, access_count, is_active, scope
            FROM user_memories
            WHERE id = ANY($1::bigint[])
            """,
            ids,
        )
        return [self._row_to_memory(r) for r in rows]

    # ── 0.5.0 M3 B3: 巩固归档(user_memories_archive) ─────────────────────

    async def archive_memories(
        self,
        memories: list[Memory],
        summaries: dict[int, str] | None = None,
    ) -> int:
        """驱逐前巩固归档: 原记忆 1 行摘要入 user_memories_archive。

        归档不参与注入; search_knowledge/memory_search 可按需检索召回
        ("我之前说过什么")。幂等: 同一 memory_id 重复归档跳过。

        Args:
            memories: 被驱逐(已 deactivate)的记忆列表。
            summaries: {memory_id: 摘要} 映射(模型压缩), 缺省取 content 截断。

        Returns:
            本次新增归档数。
        """
        if not memories:
            return 0
        summaries = summaries or {}
        inserted = 0
        async with self._conn.transaction():
            for m in memories:
                if m.id is None:
                    continue
                exists = await self._conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM user_memories_archive "
                    "WHERE memory_id = $1)",
                    m.id,
                )
                if exists:
                    continue
                summary = summaries.get(m.id) or _default_summary(m)
                await self._conn.execute(
                    """
                    INSERT INTO user_memories_archive
                           (user_id, memory_id, scope, type, content, summary, importance)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    m.user_id,
                    m.id,
                    m.scope,
                    m.type,
                    m.content,
                    summary,
                    m.importance,
                )
                inserted += 1
        return inserted

    async def search_archived(
        self,
        query: str,
        user_id: int = 1,
        limit: int = 5,
        scope: str | None = None,
    ) -> list[dict]:
        """归档记忆按需检索(0.5.0 M3 B5 归档召回测试用)。

        简单关键词 LIKE 检索(archive 为纯文本摘要, 不做向量);
        模型侧可进一步用 memory_search 工具包装。

        Args:
            query: 检索关键词。
            user_id: 用户 ID。
            limit: 返回条数。
            scope: 场景过滤(可选)。

        Returns:
            [{memory_id, scope, type, summary, content, importance, archived_at}]。
        """
        sql = (
            "SELECT memory_id, scope, type, summary, content, importance, archived_at "
            "FROM user_memories_archive WHERE user_id = $1 "
            "AND (summary ILIKE '%' || $2 || '%' OR content ILIKE '%' || $2 || '%')"
        )
        params: list[Any] = [user_id, query]
        if scope:
            sql += " AND scope = $3"
            params.append(scope)
        sql += " ORDER BY archived_at DESC LIMIT $%d" % (len(params) + 1)
        params.append(limit)
        rows = await self._conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    # ── 0.5.0 M3 B1: 用户画像聚合(user_profile) ──────────────────────────

    async def memory_stats(self, user_id: int = 1) -> dict:
        """0.5.0 M3 B5: 记忆命中统计(评估工具)。

        统计维度: 活跃数/按 scope 分布/按 type 分布/低访问记忆
        (access_count=0 且创建超 7 天 → 候选整合)/归档数。

        Returns:
            {active, archived, by_scope, by_type, low_access_candidates, profile_exists}
        """
        active = await self.count_active(user_id)
        by_scope_rows = await self._conn.fetch(
            "SELECT scope, COUNT(*) AS cnt FROM user_memories "
            "WHERE user_id = $1 AND is_active = TRUE GROUP BY scope",
            user_id,
        )
        by_type_rows = await self._conn.fetch(
            "SELECT type, COUNT(*) AS cnt FROM user_memories "
            "WHERE user_id = $1 AND is_active = TRUE GROUP BY type",
            user_id,
        )
        low_rows = await self._conn.fetch(
            """
            SELECT id, scope, type, content, importance, access_count, created_at
            FROM user_memories
            WHERE user_id = $1 AND is_active = TRUE AND access_count = 0
              AND created_at < now() - INTERVAL '7 days'
            ORDER BY importance ASC LIMIT 20
            """,
            user_id,
        )
        archived = await self._conn.fetchval(
            "SELECT COUNT(*) FROM user_memories_archive WHERE user_id = $1",
            user_id,
        ) or 0
        profile = await self._conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM user_profile WHERE user_id = $1)",
            user_id,
        )
        return {
            "active": active,
            "archived": archived,
            "by_scope": {r["scope"]: r["cnt"] for r in by_scope_rows},
            "by_type": {r["type"]: r["cnt"] for r in by_type_rows},
            "low_access_candidates": [
                {
                    "id": r["id"], "scope": r["scope"], "type": r["type"],
                    "content": (r["content"] or "")[:60],
                    "importance": r["importance"],
                    "access_count": r["access_count"],
                }
                for r in low_rows
            ],
            "profile_exists": bool(profile),
        }

    async def get_profile(self, user_id: int = 1) -> dict | None:
        """获取用户画像(0.5.0 M3 B1)。

        Returns:
            画像 dict(无则 None)。
        """
        row = await self._conn.fetchrow(
            "SELECT user_id, name, collaboration_prefs, common_tools, "
            "communication_style, ongoing_projects, updated_at "
            "FROM user_profile WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return None
        return {
            "user_id": row["user_id"],
            "name": row["name"],
            "collaboration_prefs": row["collaboration_prefs"],
            "common_tools": row["common_tools"],
            "communication_style": row["communication_style"],
            "ongoing_projects": row["ongoing_projects"],
            "updated_at": row["updated_at"],
        }

    async def upsert_profile(self, profile: dict, user_id: int = 1) -> None:
        """写入/更新用户画像(0.5.0 M3 B1, 按 user_id 唯一)。

        Args:
            profile: 画像字段(name/collaboration_prefs/common_tools/
                     communication_style/ongoing_projects)。
            user_id: 用户 ID。
        """
        import json as _json

        await self._conn.execute(
            """
            INSERT INTO user_profile
                   (user_id, name, collaboration_prefs, common_tools,
                    communication_style, ongoing_projects, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name,
                collaboration_prefs = EXCLUDED.collaboration_prefs,
                common_tools = EXCLUDED.common_tools,
                communication_style = EXCLUDED.communication_style,
                ongoing_projects = EXCLUDED.ongoing_projects,
                updated_at = now()
            """,
            user_id,
            profile.get("name"),
            profile.get("collaboration_prefs"),
            profile.get("common_tools"),
            profile.get("communication_style"),
            _json.dumps(profile.get("ongoing_projects", []), ensure_ascii=False),
        )

    @staticmethod
    def _row_to_memory(row: asyncpg.Record) -> Memory:
        """将 asyncpg 行转换为 Memory 数据类。"""
        return Memory(
            id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            content=row["content"],
            importance=row["importance"],
            source_session_id=row["source_session_id"],
            created_at=row["created_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            is_active=row["is_active"],
            scope=row.get("scope", "global"),
        )


def _default_summary(m: Memory) -> str:
    """无模型压缩时的降级摘要: 原内容前 120 字。"""
    content = (m.content or "").strip()
    return content[:120] + ("…" if len(content) > 120 else "")