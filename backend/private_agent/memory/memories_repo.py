"""蓝图 §4.3 user_memories 表 CRUD 操作。

表结构(蓝图 §2.10 + §4.3):
- id BIGSERIAL PRIMARY KEY
- user_id BIGINT NOT NULL (单人场景固定为 1)
- type VARCHAR(20) NOT NULL (preference/fact/todo/decision)
- content TEXT NOT NULL
- importance FLOAT DEFAULT 0.5
- source_session_id BIGINT (来源会话,评估溯源)
- created_at TIMESTAMPTZ DEFAULT NOW()
- last_accessed_at TIMESTAMPTZ DEFAULT NOW()
- access_count INT DEFAULT 0
- is_active BOOLEAN DEFAULT TRUE
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

__all__ = ["Memory", "MemoriesRepo"]


# 蓝图 §4.3 记忆类型枚举
MEMORY_TYPES = frozenset({"preference", "fact", "todo", "decision"})

# 蓝图 §4.3 importance 初始值规则
TYPE_IMPORTANCE_MAP: dict[str, float] = {
    "decision": 0.9,
    "fact": 0.7,
    "preference": 0.6,
    "todo": 0.5,
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


class MemoriesRepo:
    """user_memories 表 CRUD 操作(蓝图 §4.3)。"""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, memory: Memory) -> int:
        """插入单条记忆,返回 id。

        Args:
            memory: Memory 实例(type/content/importance/source_session_id)。

        Returns:
            新记录的 id。
        """
        if memory.type not in MEMORY_TYPES:
            raise ValueError(
                f"memory.type='{memory.type}' 不合法, 合法值: {sorted(MEMORY_TYPES)}"
            )
        return await self._conn.fetchval(
            """
            INSERT INTO user_memories (user_id, type, content, importance, source_session_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            memory.user_id,
            memory.type,
            memory.content,
            memory.importance,
            memory.source_session_id,
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
    ) -> list[Memory]:
        """查询活跃记忆,按重要性降序排列(蓝图 §4.5 注入用)。

        Args:
            user_id: 用户 ID(单人场景固定为 1)。
            order_by: 排序字段(默认 importance DESC, last_accessed_at DESC)。
            limit: 返回条数(默认 10)。

        Returns:
            Memory 列表。
        """
        rows = await self._conn.fetch(
            f"""
            SELECT id, user_id, type, content, importance, source_session_id,
                   created_at, last_accessed_at, access_count, is_active
            FROM user_memories
            WHERE user_id = $1 AND is_active = TRUE
            ORDER BY {order_by}
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
    ) -> list[int]:
        """按 importance 升序淘汰指定数量的记忆(蓝图 §4.4 条件 1)。

        Args:
            user_id: 用户 ID。
            count: 淘汰数量。

        Returns:
            被淘汰记忆的 id 列表。
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
            RETURNING id
            """,
            user_id,
            count,
        )
        return [r["id"] for r in rows]

    async def deactivate_expired(
        self,
        user_id: int,
        min_importance: float = 0.3,
        cutoff: datetime | None = None,
    ) -> list[int]:
        """淘汰低重要性 + 长期未访问的记忆(蓝图 §4.4 条件 2)。

        Args:
            user_id: 用户 ID。
            min_importance: 重要性阈值(低于此值且超期则淘汰)。
            cutoff: 超期时间点(默认 30 天前)。

        Returns:
            被淘汰记忆的 id 列表。
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
            RETURNING id
            """,
            user_id,
            min_importance,
            cutoff,
        )
        return [r["id"] for r in rows]

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
                   created_at, last_accessed_at, access_count, is_active
            FROM user_memories
            WHERE id = ANY($1::bigint[])
            """,
            ids,
        )
        return [self._row_to_memory(r) for r in rows]

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
        )